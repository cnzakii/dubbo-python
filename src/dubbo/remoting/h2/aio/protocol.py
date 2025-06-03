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
import asyncio
import collections
import socket
from contextlib import asynccontextmanager, suppress
from types import TracebackType
from typing import Any, Callable, Optional, TypeVar, Union, cast

from anyio import abc as anyio_abc
from h2 import events as h2_events, exceptions as h2_exceptions
from h2.config import H2Configuration
from h2.connection import H2Connection

from dubbo.logger import logger
from dubbo.remoting.h2.exceptions import (
    H2ConnectionError,
    H2ConnectionTerminatedError,
    H2ProtocolError,
    convert_h2_exception,
)

from ..base import (
    AUTO_PROCESS_EVENTS,
    STREAM_EVENTS,
    AsyncHttp2Connection,
    AsyncHttp2ConnectionHandlerType,
    AsyncHttp2Stream,
    AsyncHttp2StreamHandlerType,
    AsyncPingAckHandlerType,
    AsyncSettingsAckHandlerType,
    Http2ChangedSetting,
    Http2ChangedSettingsType,
    Http2SettingsType,
    StreamManager,
)
from ..registries import Http2ErrorCode
from .managers import AsyncCallbackManager, AsyncStreamManager
from .stream import AioHttp2Stream
from .tracker import AioSendTracker, AsyncSendTracker

__all__ = ["Http2Protocol"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], None]]

_DUMMY_TRACKER = AioSendTracker(send_func=lambda: None, no_wait=True)


class Http2Protocol(asyncio.BufferedProtocol, AsyncHttp2Connection):
    """An asyncio-based HTTP/2 protocol implementation."""

    __slots__ = (
        "_h2_core",
        "_transport",
        "_tg",
        "_connection_handler",
        "_stream_manager",
        "_ping_manager",
        "_settings_manager",
        "_event_dispatcher",
        "_conn_exc",
        "_buffer",
        "_is_writable",
        "_writer_waiter",
    )

    _h2_core: H2Connection
    _transport: Optional[asyncio.Transport]
    _tg: anyio_abc.TaskGroup

    # some handlers
    _connection_handler: Optional[AsyncHttp2ConnectionHandlerType]

    # some managers
    _stream_manager: AsyncStreamManager
    _ping_manager: AsyncCallbackManager[bytes, bytes]
    _settings_manager: AsyncCallbackManager[str, Http2ChangedSettingsType]

    # event dispatcher
    _event_dispatcher: _EventDispatcher

    # some flags
    _conn_exc: Optional[H2ConnectionError]

    # buffer for incoming data
    _buffer: bytearray

    # Flow control flags for write operations
    _is_writable: bool
    _writer_waiter: collections.deque[AsyncSendTracker]

    def __init__(
        self,
        h2_config: H2Configuration,
        task_group: anyio_abc.TaskGroup,
        stream_handler: Optional[AsyncHttp2StreamHandlerType] = None,
        connection_handler: Optional[AsyncHttp2ConnectionHandlerType] = None,
    ) -> None:
        """Initialize the HTTP/2 protocol handler."""
        self._h2_core = H2Connection(h2_config)
        self._transport = None
        self._tg = task_group

        self._connection_handler = connection_handler

        self._stream_manager = AsyncStreamManager(self._tg, stream_handler, self._streams_monitor)
        self._ping_manager = AsyncCallbackManager(self._tg)
        self._settings_manager = AsyncCallbackManager(self._tg)

        self._conn_exc = H2ConnectionError("Connection not established yet")
        self._buffer = bytearray(64 * 1024)  # 64 KiB buffer for incoming data
        self._is_writable = False
        self._writer_waiter = collections.deque()

        # Map HTTP/2 event types to their handler methods
        self._event_dispatcher: _EventDispatcher = {
            h2_events.RequestReceived: self._handle_request_received,
            h2_events.ConnectionTerminated: self._handle_connection_terminated,
            h2_events.PingAckReceived: self._handle_ping_ack,
            h2_events.SettingsAcknowledged: self._handle_settings_ack,
        }

    async def __aenter__(self) -> "Http2Protocol":
        return self

    async def __aexit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        await self.aclose()

    # -- Properties ------
    @property
    def connected(self) -> bool:
        """Check if the connection is currently established."""
        return self._transport is not None and not self._transport.is_closing()

    def get_extra_info(self, name: str) -> Any:
        """Get extra information about the connection."""
        if self._transport is None:
            return None
        return self._transport.get_extra_info(name)

    @property
    def h2_core(self) -> H2Connection:
        """Get the underlying H2Connection instance."""
        return self._h2_core

    @property
    def stream_manager(self) -> StreamManager:
        """Get the stream manager for this connection."""
        return self._stream_manager

    def create_stream(self, stream_id: int = -1) -> AsyncHttp2Stream:
        if self._conn_exc:
            raise self._conn_exc
        return AioHttp2Stream(self, stream_id)

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

    # ------ Sending Methods ------
    async def update_settings(
        self, settings: Http2SettingsType, handler: Optional[AsyncSettingsAckHandlerType] = None
    ) -> None:
        """Update HTTP/2 settings and send a SETTINGS frame to the peer."""
        if self._conn_exc:
            raise self._conn_exc

        async with self._send_guard():
            tracker = AioSendTracker(lambda: self._h2_core.update_settings(settings))

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

            tracker = AioSendTracker(lambda: self._h2_core.ping(opaque_data=payload))

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
            tracker = AioSendTracker(
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
        has received a GOAWAY, the connection can be fully closed.

        Args:
            active_streams: The current number of active streams.
        """
        if not self._conn_exc or active_streams > 0:
            return
        logger.debug("No active streams left on terminated connection, closing")

        # Close the transport
        if self._transport and not self._transport.is_closing():
            self._transport.close()

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

    def close(self) -> None:
        """Close the HTTP/2 connection."""
        if self._transport is None or self._conn_exc:
            return

        # Send a GOAWAY frame to gracefully close the connection
        try:
            self._h2_core.close_connection(error_code=Http2ErrorCode.NO_ERROR, last_stream_id=0, additional_data=None)
            self.flush()
        except Exception:
            pass

        # Close the transport
        if not self._transport.is_closing():
            self._transport.close()

    async def aclose(self) -> None:
        """Asynchronously close the HTTP/2 connection."""
        self.close()

    # ------ asyncio.BufferedProtocol methods ------
    def pause_writing(self) -> None:
        """Pause writing to the transport."""
        self._is_writable = False

    def resume_writing(self) -> None:
        """Resume writing to the transport."""
        if self._conn_exc is None:
            self._is_writable = True
            self.flush()
        # notify any waiting senders that writing can resume
        for tracker in self._writer_waiter:
            tracker.complete(self._conn_exc)

    def flush(self) -> None:
        """Flush buffered HTTP/2 data to the transport."""
        data = self._h2_core.data_to_send()
        if not data:
            return

        try:
            self._transport.write(data)  # type: ignore[union-attr]
        except Exception as e:
            exc = H2ConnectionError(f"Failed to write data to transport: {type(e).__name__}: {e}", e)
            if not self._conn_exc:
                self._conn_exc = exc

            # Abort the transport on write failure
            if self._transport:
                with suppress(Exception):
                    self._transport.abort()

            raise exc from e

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Called when the connection is established."""

        # Initialize the transport and set socket options
        self._transport = cast(asyncio.Transport, transport)
        self._conn_exc = None
        sock = self._transport.get_extra_info("socket")
        if sock is not None and sock.family in (socket.AF_INET, socket.AF_INET6):
            with suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Initialize the HTTP/2 connection with the connection preface
        self._h2_core.initiate_connection()
        self.flush()

        if self._connection_handler:
            # Asynchronously notify the connection handler
            self._tg.start_soon(self._connection_handler, self)

        # Notify the transport that we are ready to write
        self.resume_writing()
        logger.debug("HTTP/2 connection established with %s", transport.get_extra_info("peername"))

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """Called when the connection is lost."""
        if exc and not self._conn_exc:
            self._conn_exc = H2ConnectionError(f"Connection lost: {type(exc).__name__}: {exc}", exc)

        # call resume_writing to ensure we can notify any waiting senders
        self.resume_writing()

        # notify the stream manager about the connection loss
        self.stream_manager.dispatch_connection_error(self._conn_exc)  # type: ignore[arg-type]

        logger.debug("HTTP/2 connection lost, due to: %s", self._conn_exc)

    def get_buffer(self, sizehint: int) -> memoryview:
        """Get a buffer for receiving data."""
        return memoryview(self._buffer)

    def buffer_updated(self, nbytes: int) -> None:
        """Called when the buffer is updated with new data."""
        if nbytes <= 0:
            return

        data = self._buffer[:nbytes]
        try:
            events = self._h2_core.receive_data(data)
            if events:
                self.handle_events(events)
        except Exception as e:
            if isinstance(e, h2_exceptions.ProtocolError):
                # Handle HTTP/2 protocol violations
                with suppress(Exception):
                    self.flush()
                exc = H2ConnectionError(f"HTTP/2 protocol error: {type(e).__name__}: {e}", e)
            else:
                # Handle unexpected errors
                exc = H2ConnectionError(f"Unexpected error processing received data: {type(e).__name__}: {e}", e)

            if not self._conn_exc:
                self._conn_exc = exc

            # Abort the transport on error
            with suppress(Exception):
                if self._transport:
                    self._transport.abort()

    async def send(self, tracker: AsyncSendTracker) -> None:
        """Initiates sending data over the HTTP/2 connection."""
        # Fast fail if the connection has a non-graceful error
        exc = self._conn_exc
        if exc and not (isinstance(exc, H2ConnectionTerminatedError) and exc.remote_termination):
            tracker.complete(exc)
            return

        try:
            tracker.trigger()

            if self._is_writable:
                self.flush()
                tracker.complete(None)
            else:
                self._writer_waiter.append(tracker)

        except Exception as e:
            tracker.complete(e)
