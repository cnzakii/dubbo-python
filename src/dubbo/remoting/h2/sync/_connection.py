#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Optional, TypeVar, Union

from h2 import events as h2_events, exceptions as h2_exceptions
from h2.config import H2Configuration
from h2.connection import H2Connection

from dubbo.common import constants
from dubbo.common.types import BytesLike
from dubbo.common.utils import common as common_utils
from dubbo.logger import logger
from dubbo.remoting.backend import NetworkStream
from dubbo.remoting.backend.exceptions import ReceiveError, SendError
from dubbo.remoting.h2 import (
    Http2ChangedSetting,
    Http2ChangedSettingsType,
    Http2Connection,
    Http2ErrorCode,
    Http2SettingsType,
    Http2Stream,
    Http2StreamHandlerType,
    PingAckHandlerType,
    SettingsAckHandlerType,
)
from dubbo.remoting.h2.common import AUTO_PROCESS_EVENTS, STREAM_EVENTS
from dubbo.remoting.h2.exceptions import (
    H2ConnectionError,
    H2ConnectionTerminatedError,
    H2ProtocolError,
    convert_h2_exception,
)

from ._managers import CallbackManager, StreamManager
from ._stream import SyncHttp2Stream
from ._tracker import SendTracker, dummy_tracker

__all__ = ["SyncHttp2Connection"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], None]]


class SyncHttp2Connection(Http2Connection):
    """A synchronous HTTP/2 connection implementation."""

    __slots__ = (
        "_net_stream",
        "_h2_core",
        "_h2_core_lock",
        "_executor",
        "_send_thread",
        "_recv_thread",
        "_stream_manager",
        "_ping_manager",
        "_settings_manager",
        "_send_buffer",
        "_event_dispatcher",
        "_conn_exc",
        "_closed_event",
    )

    _net_stream: NetworkStream
    _h2_core: H2Connection
    _h2_core_lock: threading.RLock
    _executor: ThreadPoolExecutor
    _send_thread: threading.Thread
    _recv_thread: threading.Thread

    _stream_manager: StreamManager
    _ping_manager: CallbackManager[bytes, bytes]
    _settings_manager: CallbackManager[str, Http2ChangedSettingsType]

    # send/receive buffer
    _send_buffer: queue.Queue[SendTracker]

    _event_dispatcher: _EventDispatcher

    _conn_exc: Optional[Exception]
    _closed_event: threading.Event

    def __init__(
        self,
        net_stream: NetworkStream,
        h2_config: H2Configuration,
        stream_handler: Optional[Http2StreamHandlerType] = None,
        executor: Optional[ThreadPoolExecutor] = None,
    ) -> None:
        self._net_stream = net_stream
        self._h2_core = H2Connection(h2_config)
        self._h2_core_lock = threading.RLock()

        self._executor = executor or ThreadPoolExecutor(
            max_workers=40, thread_name_prefix=f"{constants.DUBBO}-http2-connection"
        )
        self._send_thread = threading.Thread(
            target=self._send_loop, name=f"{constants.DUBBO}-http2-send-loop", daemon=True
        )
        self._recv_thread = threading.Thread(
            target=self._receive_loop, name=f"{constants.DUBBO}-http2-recv-loop", daemon=True
        )

        self._ping_manager: CallbackManager[bytes, bytes] = CallbackManager(self._executor)
        self._settings_manager: CallbackManager[str, Http2ChangedSettingsType] = CallbackManager(self._executor)

        self._stream_manager = StreamManager(
            self._executor, stream_handler=stream_handler, count_monitor=self._streams_monitor
        )
        self._send_buffer: queue.Queue[SendTracker] = queue.Queue(maxsize=1)

        self._conn_exc = None
        self._closed_event = threading.Event()

        self._event_dispatcher: _EventDispatcher = {
            h2_events.RequestReceived: self._handle_request_received,
            h2_events.ConnectionTerminated: self._handle_connection_terminated,
            h2_events.PingAckReceived: self._handle_ping_ack,
            h2_events.SettingsAcknowledged: self._handle_settings_ack,
            h2_events.UnknownFrameReceived: self._handle_unknown_event,
        }

    def __enter__(self) -> "SyncHttp2Connection":
        """Enters the context manager for the connection.

        Returns:
            SyncHttp2Connection: This connection instance.
        """
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Exits the context manager for the connection.

        Closes the connection and cleans up resources.

        Args:
            exc_type: Type of exception that occurred in the context.
            exc_value: Exception instance that occurred in the context.
            traceback: Traceback information.
        """
        try:
            self.close()
        finally:
            self._executor.shutdown(wait=True)
            self._net_stream.close()
            self._closed_event.set()

        if not self._closed_event.is_set():
            try:
                self.close()
            except Exception:
                # Ignore errors during graceful closure in exit context
                pass

        # Clean up network stream
        try:
            self._net_stream.close()
        except Exception:
            # Ignore network cleanup errors
            pass

        self._closed_event.set()
        self._ping_manager.close()
        self._settings_manager.close()
        self._stream_manager.close()

        # Close the thread pool executor
        if self._executor:
            self._executor.shutdown(wait=True)

    @property
    def h2_core(self) -> H2Connection:
        """Returns the internal h2 connection core.

        Note:
            This is not thread-safe and should only be accessed within the h2_core_lock.

        Returns:
            H2Connection: The internal h2 connection core.
        """
        return self._h2_core

    @property
    def h2_core_lock(self) -> threading.RLock:
        """Returns the lock used for synchronizing access to the h2 core.

        Returns:
            threading.RLock: The lock for h2 core access synchronization.
        """
        return self._h2_core_lock

    @property
    def stream_manager(self) -> StreamManager:
        """Returns the stream manager used for routing events.

        Returns:
            StreamManager: The stream manager instance.
        """
        return self._stream_manager

    def create_stream(self, stream_id: int = -1) -> Http2Stream:
        if self._conn_exc:
            raise self._conn_exc

        return SyncHttp2Stream(self, stream_id)

    def start(self) -> None:
        """Starts the connection and its threads.

        Initializes the HTTP/2 connection and starts the send and receive threads.
        """
        self._initialize()
        self._send_thread.start()
        self._recv_thread.start()

    def _initialize(self) -> None:
        try:
            with self.h2_core_lock:
                self._h2_core.initiate_connection()
                data_to_send = self._h2_core.data_to_send()
            if data_to_send:
                self._net_stream.send(data_to_send)
        except Exception as e:
            raise H2ConnectionError(f"Connection initialization failed: {e}") from e

    def _send_loop(self) -> None:
        """Continuously checks the send buffer for outgoing data.

        This method runs in a dedicated thread and processes outgoing data.
        """
        tracker: Optional[SendTracker] = None
        try:
            while True:
                tracker = self._send_buffer.get()
                if tracker is None:
                    logger.info("Send loop received shutdown signal.")
                    break
                with self.h2_core_lock:
                    tracker.trigger()
                    data_to_send = self._h2_core.data_to_send()
                if data_to_send:
                    self._net_stream.send(data_to_send)

                # Notify the tracker that the data has been sent
                tracker.complete()
        except Exception as e:
            if isinstance(e, SendError):
                exc = H2ConnectionError(f"Failed to send data over network stream (SendError): {e}", e)
            elif isinstance(e, threading.ThreadError):
                exc = H2ConnectionError("Send loop was interrupted (ThreadError)", e)
            elif isinstance(e, h2_exceptions.ProtocolError):
                exc = H2ConnectionError(f"Protocol error in send loop: {type(e).__name__}: {e}", e)
            else:
                exc = H2ConnectionError(f"Unexpected error in send loop: {type(e).__name__}: {e}", e)

            if tracker:
                # If we have a tracker, mark it as complete with the exception
                tracker.complete(exc)
            if not self._conn_exc:
                # Only set the connection exception if it hasn't been set yet
                self._conn_exc = exc
        finally:
            self._closed_event.set()

    def _receive_loop(self):
        try:
            while True:
                data = self._net_stream.receive()
                with self.h2_core_lock:
                    events = self._h2_core.receive_data(data)
                self.handle_events(events)
        except Exception as e:
            if isinstance(e, ReceiveError):
                exc = H2ConnectionError(f"Failed to receive data from network stream (ReceiveError): {e}", e)
            elif isinstance(e, threading.ThreadError):
                exc = H2ConnectionError("Receive loop was interrupted (ThreadError)", e)
            elif isinstance(e, h2_exceptions.ProtocolError):
                exc = H2ConnectionError(f"Protocol error in receive loop: {type(e).__name__}: {e}", e)
            else:
                exc = H2ConnectionError(f"Unexpected error in receive loop: {type(e).__name__}: {e}", e)

            if not self._conn_exc:
                self._conn_exc = exc
        finally:
            self._closed_event.set()

    # ----------- Internal API for sending data -----------
    def send(self, tracker: SendTracker) -> None:
        exc = self._conn_exc
        if exc and not (isinstance(exc, H2ConnectionTerminatedError) and exc.remote_termination):
            # Only suppress the exception if the connection was gracefully closed
            # by the remote peer using a GOAWAY frame. In all other cases—such as
            # local termination, protocol errors, or unexpected disconnects—re-raise
            # the stored connection exception.
            tracker.complete(exc)
            return
        try:
            self._send_buffer.put(tracker, timeout=tracker.remaining_time)
        except queue.Full:
            tracker.complete(TimeoutError("Send buffer is full, unable to send data"))

    @contextmanager
    def _send_guard(self):
        try:
            yield
        except (H2ProtocolError, H2ConnectionError, TimeoutError):
            # already handled by the stream, just re-raise
            raise
        except h2_exceptions.H2Error as e:
            inner_exc = convert_h2_exception(e)
            raise inner_exc from e
        except Exception as e:
            # Convert any other exception to H2ConnectionError
            inner_exc = H2ConnectionError(f"Error during send operation: {type(e).__name__}: {e}", e)
            raise inner_exc from e

    # ----------- Public API for HTTP/2 operations -----------

    def update_settings(
        self,
        settings: Http2SettingsType,
        handler: Optional[SettingsAckHandlerType] = None,
        timeout: Optional[float] = None,
    ) -> None:
        with self._send_guard():
            tracker = SendTracker(lambda: self._h2_core.update_settings(settings), timeout=timeout)

            # Register the callback for settings acknowledgment
            if handler:
                self._settings_manager.register("settings", handler)
            try:
                self.send(tracker)
                tracker.result()
            except Exception as e:
                if handler:
                    self._settings_manager.unregister("settings", handler)
                raise e

    def ping(
        self, payload: BytesLike, handler: Optional[PingAckHandlerType] = None, timeout: Optional[float] = None
    ) -> None:
        with self._send_guard():
            payload_b = common_utils.to_bytes(payload)
            if len(payload_b) != 8:
                raise H2ProtocolError(f"Ping payload must be 8 bytes, got {len(payload_b)}")

            tracker = SendTracker(lambda: self._h2_core.ping(opaque_data=payload), timeout=timeout)
            # Register the callback for the ping acknowledgment
            if handler:
                self._ping_manager.register(payload_b, handler)

            try:
                self.send(tracker)
                tracker.result()
            except Exception as e:
                if handler:
                    self._ping_manager.unregister(payload_b, handler)
                raise e

    def close(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[BytesLike] = None,
        timeout: Optional[float] = None,
    ) -> None:
        with self._send_guard():
            if self._closed_event.is_set():
                # Already closed
                logger.debug("Connection already closed, ignoring aclose request")
                return

            additional_data = common_utils.to_bytes(additional_data) if additional_data else None

            tracker = SendTracker(
                lambda: self._h2_core.close_connection(
                    error_code=error_code,
                    last_stream_id=last_stream_id,
                    additional_data=additional_data,
                ),
                timeout=timeout,
            )
            self.send(tracker)
            tracker.result()

            self._conn_exc = H2ConnectionTerminatedError(
                error_code=error_code,
                last_stream_id=last_stream_id or 0,
                additional_data=additional_data,
                remote_termination=False,  # Local termination
            )
            # build a ConnectionTerminated event to notify the relevant streams
            event = h2_events.ConnectionTerminated()
            event.error_code = error_code
            event.last_stream_id = last_stream_id or 0
            event.additional_data = additional_data

            # Handle the event in the stream manager
            self.stream_manager.dispatch_event(event)

    # ----------- Event Handlers -----------

    def handle_events(self, events: list[h2_events.Event]) -> None:
        """Dispatches incoming HTTP/2 events to appropriate handlers.

        Args:
            events: A list of parsed H2 events.
        """
        for event in events:
            event_type = type(event)
            if event_type in STREAM_EVENTS:
                # Dispatch stream-level events to the stream manager
                self.stream_manager.dispatch_event(event)
                continue

            # If the event is not a stream-level event, check if it has a custom handler
            handler = self._event_dispatcher.get(event_type, self._handle_ignored_events)
            handler(event)

            if event_type in AUTO_PROCESS_EVENTS and not self._conn_exc:
                # Automatically process certain events that don't require user intervention
                # For example, RemoteSettingsChanged and PingReceived
                # we need to send a dummy tracker to flush the h2_core
                self._executor.submit(self.send, dummy_tracker)

    def _handle_request_received(self, event: h2_events.RequestReceived) -> None:
        """Handles a new request received event.

        Creates a new stream and dispatches the event to the stream manager.

        Args:
            event: RequestReceived event from the h2 library.
        """
        assert event.stream_id is not None, "RequestReceived event must have a stream_id"
        stream = self.create_stream(event.stream_id)
        self.stream_manager.register(stream)
        # Dispatch the event to the stream manager
        self.stream_manager.dispatch_event(event)

    def _handle_connection_terminated(self, event: h2_events.ConnectionTerminated) -> None:
        error_code = event.error_code
        if error_code is None:
            error_code = Http2ErrorCode.NO_ERROR

        self._conn_exc = H2ConnectionTerminatedError(
            error_code=error_code,
            last_stream_id=event.last_stream_id or 0,
            additional_data=event.additional_data,
            remote_termination=True,
        )
        # Notify the stream manager about the connection termination
        self._stream_manager.dispatch_event(event)

    def _handle_ping_ack(self, event: h2_events.PingAckReceived) -> None:
        """Handles a PING ACK frame from the peer.

        Args:
            event: PingAckReceived event from the h2 library.
        """
        assert event.ping_data is not None, "PingAckReceived event must contain ping_data"
        self._ping_manager.dispatch_one(event.ping_data, event.ping_data)

    def _handle_settings_ack(self, event: h2_events.SettingsAcknowledged) -> None:
        """Handles a SETTINGS ACK frame from the peer.

        Args:
            event: SettingsAcknowledged event from the h2 library.
        """
        converted_settings: Http2ChangedSettingsType = {
            int(k): Http2ChangedSetting(
                setting_code=int(v.setting),
                original_value=v.original_value,
                new_value=v.new_value,
            )
            for k, v in event.changed_settings.items()
        }
        # Notify the settings manager about the acknowledged settings
        self._settings_manager.dispatch_one("settings", converted_settings)

    def _handle_ignored_events(self, event: h2_events.Event) -> None:
        """Handles events that don't require special processing.

        These events are typically auto-processed by the h2 library.

        Args:
            event: An HTTP/2 event from the h2 library.
        """
        # Logging ignored events
        logger.debug("Ignored event: %s", event)

    def _handle_unknown_event(self, event: h2_events.UnknownFrameReceived) -> None:
        """Handle unknown frame types.

        Args:
            event: UnknownFrameReceived event from the h2 library.
        """
        logger.error("Unknown event: %s", event)

    def _streams_monitor(self, active_streams: int) -> None:
        """Monitors the number of active streams and handles connection closure.

        Called by the StreamManager whenever the number of active streams changes.
        When there are no active streams left on a terminated connection, it can be fully closed.

        Args:
            active_streams: The current number of active streams.
        """
        if not self._conn_exc or active_streams > 0:
            return

        # If the connection is closed and there are no active streams, set the closed event
        logger.debug("No active streams left on terminated connection, closing")
        self._closed_event.set()

    def wait_until_closed(self, timeout: Optional[float] = None) -> None:
        """Waits until the connection is fully closed.

        Blocks until the connection is fully closed, which happens when:
        1. An abnormal network condition has occurred.
        2. A GOAWAY frame was sent or received, and all streams have been completed.

        Args:
            timeout: Maximum time to wait in seconds, or None to wait indefinitely.
        """
        self._closed_event.wait(timeout=timeout)
