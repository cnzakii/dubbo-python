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
from typing import Any, Callable, Optional, TypeVar, Union

from h2 import events as h2_events, exceptions as h2_exceptions
from h2.config import H2Configuration
from h2.connection import H2Connection

from dubbo.common import constants
from dubbo.common.utils import common as common_utils
from dubbo.logger import logger
from dubbo.remoting.backend import NetworkStream
from dubbo.remoting.backend.exceptions import ReceiveError, SendError

from ..base import (
    AUTO_PROCESS_EVENTS,
    STREAM_EVENTS,
    Http2ChangedSetting,
    Http2ChangedSettingsType,
    Http2Connection,
    Http2SettingsType,
    Http2Stream,
    Http2StreamHandlerType,
    PingAckHandlerType,
    SettingsAckHandlerType,
    StreamManager,
)
from ..exceptions import (
    H2ConnectionError,
    H2ConnectionTerminatedError,
    H2ProtocolError,
    convert_h2_exception,
)
from ..registries import Http2ErrorCode
from ._tracker import SendTracker
from .managers import SyncCallbackManager, SyncStreamManager
from .stream import SyncHttp2Stream

__all__ = ["SyncHttp2Connection"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], None]]


_DUMMY_TRACKER = SendTracker(lambda: None, no_wait=True)


class SyncHttp2Connection(Http2Connection):
    """A synchronous HTTP/2 connection implementation."""

    _net_stream: NetworkStream
    _h2_core: H2Connection
    _h2_core_lock: threading.RLock
    _executor: ThreadPoolExecutor
    _send_thread: threading.Thread
    _recv_thread: threading.Thread

    _stream_manager: SyncStreamManager
    _ping_manager: SyncCallbackManager[bytes, bytes]
    _settings_manager: SyncCallbackManager[str, Http2ChangedSettingsType]

    # send/receive buffer
    _send_buffer: queue.Queue[SendTracker]

    _event_dispatcher: _EventDispatcher

    _conn_exc: Optional[H2ConnectionError]
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

        self._ping_manager: SyncCallbackManager[bytes, bytes] = SyncCallbackManager(self._executor)
        self._settings_manager: SyncCallbackManager[str, Http2ChangedSettingsType] = SyncCallbackManager(self._executor)

        self._stream_manager = SyncStreamManager(
            self._executor, stream_handler=stream_handler, count_monitor=self._streams_monitor
        )
        self._send_buffer: queue.Queue[SendTracker] = queue.Queue(maxsize=1000)

        self._conn_exc = None
        self._closed_event = threading.Event()

        self._event_dispatcher: _EventDispatcher = {
            h2_events.RequestReceived: self._handle_request_received,
            h2_events.ConnectionTerminated: self._handle_connection_terminated,
            h2_events.PingAckReceived: self._handle_ping_ack,
            h2_events.SettingsAcknowledged: self._handle_settings_ack,
        }

        # Start the connection
        self.start()

    def __enter__(self) -> "SyncHttp2Connection":
        """Enters the context manager for the connection."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Exits the context manager for the connection."""
        self.close()

    @property
    def connected(self) -> bool:
        """Checks if the connection is currently active."""
        return not self._closed_event.is_set() and not self._conn_exc

    @property
    def h2_core(self) -> H2Connection:
        """Returns the internal h2 connection core."""
        return self._h2_core

    @property
    def h2_core_lock(self) -> threading.RLock:
        """Returns the lock used for synchronizing access to the h2 core."""
        return self._h2_core_lock

    def get_extra_info(self, name: str) -> Any:
        """Returns extra information about the connection."""
        return self._net_stream.get_extra_info(name)

    @property
    def stream_manager(self) -> StreamManager:
        """Returns the stream manager used for routing events."""
        return self._stream_manager

    def create_stream(self, stream_id: int = -1) -> Http2Stream:
        if self._conn_exc:
            raise self._conn_exc

        return SyncHttp2Stream(self, stream_id)

    # ----------- Internal API -----------

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
            tracker.complete(H2ConnectionError("Send buffer is full, cannot send data"))

    def send_nowait(self, tracker: SendTracker) -> None:
        """Send data without waiting for the send operation to complete."""
        # Fast fail if the connection has a non-graceful error
        exc = self._conn_exc
        if exc and not (isinstance(exc, H2ConnectionTerminatedError) and exc.remote_termination):
            tracker.complete(exc)
            return

        try:
            with self.h2_core_lock:
                tracker.trigger()
                data_to_send = self._h2_core.data_to_send()
            if data_to_send:
                self._net_stream.send(data_to_send)
            tracker.complete()
        except Exception as e:
            tracker.complete(H2ConnectionError(f"Error during send operation: {type(e).__name__}: {e}", e))

    # ----------- Public API for HTTP/2 operations -----------
    def update_settings(
        self,
        settings: Http2SettingsType,
        handler: Optional[SettingsAckHandlerType] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Sends a SETTINGS frame to the peer."""
        with self._send_guard():
            tracker = SendTracker(lambda: self._h2_core.update_settings(settings), timeout=timeout)

            # Register the callback for settings acknowledgment
            if handler:
                self._settings_manager.register_callback("settings", handler)
            try:
                self.send(tracker)
                tracker.result()
            except Exception as e:
                if handler:
                    self._settings_manager.unregister_callback("settings", handler)
                raise e

    def ping(
        self, payload: bytes, handler: Optional[PingAckHandlerType] = None, timeout: Optional[float] = None
    ) -> None:
        """Sends a PING frame to the peer."""
        with self._send_guard():
            if len(payload) != 8:
                raise H2ProtocolError(f"Ping payload must be 8 bytes, got {len(payload)}")

            tracker = SendTracker(lambda: self._h2_core.ping(opaque_data=payload), timeout=timeout)
            # Register the callback for the ping acknowledgment
            if handler:
                self._ping_manager.register_callback(payload, handler)

            try:
                self.send(tracker)
                tracker.result()
            except Exception as e:
                if handler:
                    self._ping_manager.unregister_callback(payload, handler)
                raise e

    def goaway(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Sends a GOAWAY frame to the peer."""
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

        # Notify the stream manager about the connection termination
        self.stream_manager.dispatch_connection_error(self._conn_exc)

    def _streams_monitor(self, active_streams: int) -> None:
        """Monitors the number of active streams and handles connection closure.

        Called by the StreamManager whenever the number of active streams changes.
        When there are no active streams left on a terminated connection, it can be fully closed.

        Args:
            active_streams: The current number of active streams.
        """
        if not self._conn_exc or active_streams > 0:
            return

        logger.debug("No active streams left on terminated connection, closing")

        # already closed or in the process of closing
        if self._closed_event.is_set():
            return

        # close the connection gracefully
        try:
            self._net_stream.close()
        except Exception:
            pass
        finally:
            self.connection_lost()

    def wait_closed(self, timeout: Optional[float] = None) -> None:
        """Wait until the connection is closed.

        This method blocks until the connection is fully closed, which happens when:
        1. An abnormal network condition has occurred.
        2. A GOAWAY frame was sent or received, and all streams have been completed.
        """
        self._closed_event.wait(timeout=timeout)

    def close(self) -> None:
        """Close the connection gracefully."""
        if self._conn_exc and self._closed_event.is_set():
            return

        # Send a GOAWAY frame to gracefully close the connection
        try:
            self.goaway()
        except Exception:
            pass

        # Close the network stream
        try:
            self._net_stream.close()
        except Exception:
            pass

        # Finally, mark the connection as closed
        self.connection_lost()

    # ----------- Event Handlers -----------
    def handle_events(self, events: list[h2_events.Event]) -> None:
        """Dispatches incoming HTTP/2 events to appropriate handlers."""
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
                self._executor.submit(self.send, _DUMMY_TRACKER)

    def _handle_request_received(self, event: h2_events.RequestReceived) -> None:
        """Handles a new request received event."""
        assert event.stream_id is not None, "RequestReceived event must have a stream_id"
        stream = self.create_stream(event.stream_id)
        self.stream_manager.register_stream(stream)
        # Dispatch the event to the stream manager
        stream.handle_event(event)

    def _handle_connection_terminated(self, event: h2_events.ConnectionTerminated) -> None:
        """Handles a connection termination event."""
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
        self.stream_manager.dispatch_connection_error(self._conn_exc)

    def _handle_ping_ack(self, event: h2_events.PingAckReceived) -> None:
        """Handles a ping acknowledgment event."""
        assert event.ping_data is not None, "PingAckReceived event must contain ping_data"
        self._ping_manager.dispatch_one(event.ping_data, event.ping_data)

    def _handle_settings_ack(self, event: h2_events.SettingsAcknowledged) -> None:
        """Handles a settings acknowledgment event."""
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
        """Handles events that don't require special processing."""
        if isinstance(event, h2_events.UnknownFrameReceived):
            logger.warning("Received unknown frame: %s", event)
        else:
            logger.debug("Ignoring event: %s", event)

    # ----------- send/receive loops -----------
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
                self.connection_lost()

    def _receive_loop(self) -> None:
        try:
            while True:
                data = self._net_stream.receive()
                with self.h2_core_lock:
                    events = self._h2_core.receive_data(data)
                if events:
                    self.handle_events(events)
        except Exception as e:
            if isinstance(e, h2_exceptions.ProtocolError):
                # Handle HTTP/2 protocol violations
                try:
                    with self.h2_core_lock:
                        data = self._h2_core.data_to_send()
                    if data:
                        self._net_stream.send(data)
                except Exception:
                    pass

                exc = H2ConnectionError(f"HTTP/2 protocol error: {type(e).__name__}: {e}", e)
            elif isinstance(e, ReceiveError):
                exc = H2ConnectionError(f"Failed to receive data from network stream: {e}", e)
            elif isinstance(e, threading.ThreadError):
                exc = H2ConnectionError("Receive loop was interrupted", e)
            else:
                exc = H2ConnectionError(f"Unexpected error in receive loop: {type(e).__name__}: {e}", e)

            if not self._conn_exc:
                self._conn_exc = exc
                self.connection_lost()

    def connection_lost(self) -> None:
        """Called when the connection is lost or closed."""
        assert self._conn_exc, "Connection must have an exception set before calling connection_lost"

        # notify the stream manager about the connection loss
        self.stream_manager.dispatch_connection_error(self._conn_exc)

        # Set the closed event to signal that the connection is closed
        self._closed_event.set()
        logger.debug("HTTP/2 connection lost, due to: %s", self._conn_exc)
