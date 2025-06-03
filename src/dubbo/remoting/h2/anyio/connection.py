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
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any, Callable, Optional, TypeVar, Union

import anyio
from anyio import abc as anyio_abc
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from h2 import events as h2_events, exceptions as h2_exceptions
from h2.config import H2Configuration
from h2.connection import H2Connection

from dubbo.logger import logger
from dubbo.remoting.backend import AsyncNetworkStream
from dubbo.remoting.backend.exceptions import ReceiveError, SendError

from ..aio.managers import AsyncCallbackManager, AsyncStreamManager
from ..aio.tracker import AnyIOSendTracker, AsyncSendTracker
from ..base import (
    AUTO_PROCESS_EVENTS,
    STREAM_EVENTS,
    AsyncHttp2Connection,
    AsyncHttp2Stream,
    AsyncHttp2StreamHandlerType,
    AsyncPingAckHandlerType,
    AsyncSettingsAckHandlerType,
    Http2ChangedSetting,
    Http2ChangedSettingsType,
    Http2SettingsType,
    StreamManager,
)
from ..exceptions import H2ConnectionError, H2ConnectionTerminatedError, H2ProtocolError, convert_h2_exception
from ..registries import Http2ErrorCode
from .stream import AnyIOH2Stream

__all__ = ["AnyIOH2Connection"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], None]]

# A dummy tracker that does nothing, used for flushing the H2 core
_DUMMY_TRACKER = AnyIOSendTracker(lambda: None, no_wait=True)


class AnyIOH2Connection(AsyncHttp2Connection):
    """An HTTP/2 connection implementation using AnyIO."""

    __slots__ = (
        "_tg",
        "_h2_core",
        "_net_stream",
        "_send_buffer",
        "_stream_manager",
        "_ping_manager",
        "_settings_manager",
        "_event_dispatcher",
        "_initialized",
        "_closed_event",
        "_conn_exc",
    )

    _tg: anyio_abc.TaskGroup
    _h2_core: H2Connection
    _net_stream: AsyncNetworkStream[bytes]

    # send/receive buffer
    _send_buffer: tuple[MemoryObjectSendStream[AsyncSendTracker], MemoryObjectReceiveStream[AsyncSendTracker]]

    # some managers
    _stream_manager: AsyncStreamManager
    _ping_manager: AsyncCallbackManager[bytes, bytes]
    _settings_manager: AsyncCallbackManager[str, Http2ChangedSettingsType]

    # event dispatcher
    _event_dispatcher: _EventDispatcher

    # some flags
    _initialized: bool
    _closed_event: anyio_abc.Event
    _conn_exc: Optional[H2ConnectionError]

    def __init__(
        self,
        task_group: anyio_abc.TaskGroup,
        net_stream: AsyncNetworkStream[bytes],
        h2_config: H2Configuration,
        stream_handler: Optional[AsyncHttp2StreamHandlerType] = None,
    ) -> None:
        self._tg = task_group
        self._h2_core = H2Connection(h2_config)
        self._net_stream = net_stream

        # Create a zero-buffer stream for precise backpressure control
        sender, receiver = anyio.create_memory_object_stream[AsyncSendTracker](max_buffer_size=0)
        self._send_buffer = (sender, receiver)

        self._stream_manager = AsyncStreamManager(self._tg, stream_handler, self._streams_monitor)
        self._ping_manager = AsyncCallbackManager(self._tg)
        self._settings_manager = AsyncCallbackManager(self._tg)

        self._initialized = False
        self._closed_event = anyio.Event()
        self._conn_exc = None

        # Map HTTP/2 event types to their handler methods
        self._event_dispatcher: _EventDispatcher = {
            h2_events.RequestReceived: self._handle_request_received,
            h2_events.ConnectionTerminated: self._handle_connection_terminated,
            h2_events.PingAckReceived: self._handle_ping_ack,
            h2_events.SettingsAcknowledged: self._handle_settings_ack,
        }

    async def __aenter__(self) -> "AsyncHttp2Connection":
        """Enter the async context, initializing the connection."""
        await self.initialize()
        return self

    async def __aexit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        await self.aclose()

    # ----------- Properties -----------
    @property
    def connected(self) -> bool:
        """Check if the connection is currently established."""
        return not self._conn_exc and not self._closed_event.is_set()

    @property
    def h2_core(self) -> H2Connection:
        """Get the underlying H2Connection instance."""
        return self._h2_core

    def get_extra_info(self, name: str) -> Any:
        """Get extra information about the connection."""
        return self._net_stream.get_extra_info(name)

    @property
    def stream_manager(self) -> StreamManager:
        """Get the stream manager for this connection."""
        return self._stream_manager

    def create_stream(self, stream_id: int = -1) -> AsyncHttp2Stream:
        if self._conn_exc:
            raise self._conn_exc
        return AnyIOH2Stream(self, stream_id)

    @asynccontextmanager
    async def _send_guard(self):
        """Context manager to handle exceptions during send operations.

        Converts H2 library exceptions to our custom exception classes and ensures
        proper error propagation.
        """
        try:
            yield
        except (H2ProtocolError, H2ConnectionError):
            # already handled by the stream, just re-raise
            raise
        except h2_exceptions.H2Error as e:
            inner_exc = convert_h2_exception(e)
            raise inner_exc from e
        except Exception as e:
            # Convert any other exception to H2ConnectionError
            inner_exc = H2ConnectionError(f"Error during send operation: {type(e).__name__}: {e}", e)
            raise inner_exc from e

    async def send(self, tracker: AsyncSendTracker) -> None:
        # Fast fail if the connection has a non-graceful error
        exc = self._conn_exc
        if exc and not (isinstance(exc, H2ConnectionTerminatedError) and exc.remote_termination):
            tracker.complete(exc)
            return

        try:
            sender, _ = self._send_buffer
            await sender.send(tracker)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError) as e:
            # If the send buffer is closed or broken, we should not raise an error
            # but rather handle it gracefully.
            tracker.complete(H2ConnectionError(f"Send buffer is closed or broken: {e}", e))

    async def initialize(self) -> None:
        """Initialize the connection by sending the initial settings frame."""
        if self._initialized:
            return

        try:
            self._h2_core.initiate_connection()
            data_to_send = self._h2_core.data_to_send()
            if data_to_send:
                await self._net_stream.send(data_to_send)
        except Exception as e:
            raise H2ConnectionError(f"Connection initialization failed: {e}") from e

        # Start the send and receive loops in the task group
        self._tg.start_soon(self._send_loop)  # type: ignore[no-untyped-call]
        self._tg.start_soon(self._receive_loop)  # type: ignore[no-untyped-call]

        self._initialized = True

    # ------ Sending Methods ------
    async def update_settings(
        self, settings: Http2SettingsType, handler: Optional[AsyncSettingsAckHandlerType] = None
    ) -> None:
        """Update HTTP/2 settings and send a SETTINGS frame to the peer."""
        if self._conn_exc:
            raise self._conn_exc

        async with self._send_guard():
            tracker = AnyIOSendTracker(lambda: self._h2_core.update_settings(settings))

            if handler:
                # Only support one handler for settings acknowledgment
                self._settings_manager.register_callback("settings", handler)

            try:
                await self.send(tracker)
                await tracker.result()
            except Exception as e:
                if handler:
                    # If the settings update fails, unregister the callback
                    self._settings_manager.unregister_callback("settings", handler)
                raise e

    async def ping(self, payload: bytes, handler: Optional[AsyncPingAckHandlerType] = None) -> None:
        """Send a PING frame to the peer and optionally register a handler for the ACK."""
        if self._conn_exc:
            raise self._conn_exc

        async with self._send_guard():
            if len(payload) != 8:
                raise H2ProtocolError(f"PING payload must be exactly 8 bytes, got {len(payload)}")

            tracker = AnyIOSendTracker(lambda: self._h2_core.ping(opaque_data=payload))

            if handler:
                self._ping_manager.register_callback(payload, handler)

            try:
                await self.send(tracker)
                await tracker.result()
            except Exception as e:
                # If the ping fails, unregister the callback
                if handler:
                    self._ping_manager.unregister_callback(payload, handler)
                raise e

    async def goaway(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[bytes] = None,
    ) -> None:
        """Send a GOAWAY frame to the peer."""
        if self._conn_exc:
            raise self._conn_exc

        async with self._send_guard():
            tracker = AnyIOSendTracker(
                lambda: self._h2_core.close_connection(
                    error_code=error_code,
                    last_stream_id=last_stream_id,
                    additional_data=additional_data,
                )
            )
            await self.send(tracker)
            await tracker.result()
            self._conn_exc = H2ConnectionTerminatedError(
                error_code=error_code,
                last_stream_id=last_stream_id or 0,
                additional_data=additional_data,
                remote_termination=False,  # Local termination
            )

            # Notify the stream manager about the connection termination
            self.stream_manager.dispatch_connection_error(self._conn_exc)

    def _streams_monitor(self, active_streams: int) -> None:
        """Monitor the number of active streams and handle connection closure.

        This callback is invoked by the StreamManager whenever the number of active
        streams changes. When there are no active streams left on a connection that
        has received a GOAWAY, the connection can be fully closed."""
        if not self._conn_exc or active_streams > 0:
            return
        logger.debug("No active streams left on terminated connection, closing")

        # already closed or in the process of closing
        if self._closed_event.is_set():
            return

        # close the connection gracefully
        async def _close_net_stream() -> None:
            try:
                await self._net_stream.aclose()
            except Exception:
                pass
            finally:
                self.connection_lost()

        # Start the close operation in the task group
        self._tg.start_soon(_close_net_stream)  # type: ignore[no-untyped-call]

    async def wait_closed(self) -> None:
        """Wait until the connection is closed.

        This method blocks until the connection is fully closed, which happens when:
        1. An abnormal network condition has occurred.
        2. A GOAWAY frame was sent or received, and all streams have been completed.
        """
        await self._closed_event.wait()

    async def aclose(self) -> None:
        """Close the connection gracefully."""
        if self._conn_exc and self._closed_event.is_set():
            return

        # Send a GOAWAY frame to gracefully close the connection
        try:
            await self.goaway()
        except Exception:
            pass

        # Close the network stream
        try:
            await self._net_stream.aclose()
        except Exception:
            pass

        # Finally, mark the connection as closed
        self.connection_lost()

    # ------ Handling Methods ------
    def handle_events(self, events: list[h2_events.Event]) -> None:
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
                self._tg.start_soon(self.send, _DUMMY_TRACKER)

    def _handle_request_received(self, event: h2_events.RequestReceived) -> None:
        """Handle the RequestReceived event."""
        # Create and register a new stream for the request
        assert event.stream_id is not None, "RequestReceived event must contain stream_id"
        stream = self.create_stream(event.stream_id)
        self.stream_manager.register_stream(stream)
        # Handle the event in the stream
        stream.handle_event(event)

    def _handle_connection_terminated(self, event: h2_events.ConnectionTerminated) -> None:
        """Handle the ConnectionTerminated event."""
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
        """Handle the PingAckReceived event."""
        # Notify the ping manager about the received PING ACK
        assert event.ping_data is not None, "PingAckReceived event must contain ping_data"
        self._ping_manager.dispatch_one(event.ping_data, event.ping_data)

    def _handle_settings_ack(self, event: h2_events.SettingsAcknowledged) -> None:
        """Handle the SettingsAcknowledged event."""
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
        if isinstance(event, h2_events.UnknownFrameReceived):
            logger.warning("Received unknown frame: %s", event)
        else:
            logger.debug("Ignoring event: %s", event)

    # ----------- send/receive loops -----------
    async def _send_loop(self) -> None:
        """Internal send loop that pulls data from the send buffer and writes it to the network stream.

        This is a background task that continuously processes outgoing data requests.
        """
        tracker: Optional[AsyncSendTracker] = None
        try:
            _, receiver = self._send_buffer
            async with receiver:
                async for next_tracker in receiver:
                    tracker = next_tracker
                    # Generate the data to send
                    tracker.trigger()
                    data_to_send = self._h2_core.data_to_send()
                    if data_to_send:
                        await self._net_stream.send(data_to_send)
                    # Notify the tracker that the data has been sent
                    tracker.complete()
        except Exception as e:
            if isinstance(e, SendError):
                exc = H2ConnectionError(f"Failed to send data over network stream (SendError): {e}", e)
            elif isinstance(e, anyio.get_cancelled_exc_class()):
                exc = H2ConnectionError("Send loop was cancelled (likely during shutdown)", e)
            elif isinstance(e, (anyio.BrokenResourceError, anyio.ClosedResourceError)):
                exc = H2ConnectionError("Send loop encountered a broken or closed resource", e)
            else:
                exc = H2ConnectionError(f"Unexpected error in send loop: {type(e).__name__}: {e}", e)

            if tracker:
                # If we have a tracker, mark it as complete with the exception
                tracker.complete(exc)

            if exc and not self._conn_exc:
                # Only set the connection exception if it hasn't been set yet
                self._conn_exc = exc
                self.connection_lost()

    async def _receive_loop(self) -> None:
        """Internal receive loop that pulls raw data from the network stream
        and dispatches parsed HTTP/2 events.

        This is a background task that continuously reads from the network connection.
        """
        try:
            while True:
                data = await self._net_stream.receive()
                events = self._h2_core.receive_data(data)
                if events:
                    self.handle_events(events)
        except Exception as e:
            if isinstance(e, h2_exceptions.ProtocolError):
                # Handle HTTP/2 protocol violations
                try:
                    data = self._h2_core.data_to_send()
                    if data:
                        await self._net_stream.send(data)
                except Exception:
                    pass
                exc = H2ConnectionError(f"HTTP/2 protocol error: {type(e).__name__}: {e}", e)
            elif isinstance(e, ReceiveError):
                exc = H2ConnectionError(f"Failed to receive data from network stream: {e}", e)
            elif isinstance(e, anyio.get_cancelled_exc_class()):
                exc = H2ConnectionError("Receive loop was cancelled (likely during shutdown)", e)
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
