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
from collections.abc import Awaitable
from typing import Callable, Optional, TypeVar, cast

from anyio import abc as anyio_abc
from h2 import events as h2_events, exceptions as h2_exceptions
from h2.config import H2Configuration

from dubbo.logger import logger
from dubbo.remoting.h2 import AsyncHttp2ConnectionHandlerType, AsyncHttp2StreamHandlerType, Http2ErrorCode
from dubbo.remoting.h2.exceptions import H2ConnectionError

from .._connection import BaseHttp2Connection  # type: ignore[misc]
from .._tracker import AsyncSendTracker  # type: ignore[misc]

__all__ = ["Http2Protocol"]


_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], Awaitable[None]]]


class Http2Protocol(asyncio.BufferedProtocol, BaseHttp2Connection):
    """An asyncio-based HTTP/2 protocol implementation.

    This class implements the HTTP/2 protocol using asyncio's BufferedProtocol
    interface for efficient data handling. It manages HTTP/2 connections,
    handles protocol events, and coordinates flow control for multiple streams.
    """

    __slots__ = (
        "_tg",
        "_h2_core",
        "_stream_manager",
        "_ping_manager",
        "_settings_manager",
        "_send_buffer",
        "_event_dispatcher",
        "_closed_event",
        "_conn_exc",
        "_net_stream",
        "_stack",
        "_buffer",
        "_transport",
        "_loop",
        "_connection_handler",
        "_write_waiters",
        "_is_writable",
    )

    _buffer: bytearray
    _transport: Optional[asyncio.Transport]
    _loop: asyncio.AbstractEventLoop
    _connection_handler: Optional[AsyncHttp2ConnectionHandlerType]

    # Flow control flags for write operations
    _write_waiters: collections.deque[asyncio.Future[None]]
    _is_writable: bool

    def __init__(
        self,
        h2_config: H2Configuration,
        stream_handler: Optional[AsyncHttp2StreamHandlerType] = None,
        connection_handler: Optional[AsyncHttp2ConnectionHandlerType] = None,
        task_group: Optional[anyio_abc.TaskGroup] = None,
    ) -> None:
        """Initialize the HTTP/2 protocol handler.

        Args:
            h2_config: HTTP/2 configuration settings for the connection.
            stream_handler: Optional callback for handling new HTTP/2 streams.
            connection_handler: Optional callback for connection-level events.
            task_group: Optional anyio task group for managing async operations.
        """
        super().__init__(h2_config=h2_config, stream_handler=stream_handler, task_group=task_group)
        self._connection_handler = connection_handler
        self._buffer = bytearray(65535)  # 64KB initial buffer size for optimal performance
        self._transport = None
        self._loop = asyncio.get_running_loop()

        self._write_waiters = collections.deque()
        self._is_writable = False

    def pause_writing(self) -> None:
        """Pause writing to the transport.

        Called by asyncio when the transport's buffer is above the high watermark.
        This temporarily stops sending data to prevent memory pressure.
        """
        self._is_writable = False

    def resume_writing(self) -> None:
        """Resume writing to the transport.

        Called by asyncio when the transport's buffer is below the low watermark.
        This resumes data transmission and notifies any waiting coroutines.
        """
        self._is_writable = True
        # Notify all waiting coroutines that writing can resume
        for waiter in self._write_waiters:
            if not waiter.done():
                waiter.set_result(None)

    def _flush(self) -> None:
        """Flush buffered HTTP/2 data to the transport.

        Retrieves any pending data from the HTTP/2 connection and writes it
        to the underlying transport. This method is the primary way data
        flows from the HTTP/2 protocol layer to the network.

        Raises:
            H2ConnectionError: If writing to the transport fails or if the
                transport is not available.
        """
        data = self._h2_core.data_to_send()
        if not data:
            return

        try:
            self._transport.write(data)  # type: ignore[union-attr]
        except Exception as e:
            exc = H2ConnectionError(f"Failed to write data to transport: {type(e).__name__}: {e}", e)
            if not self._conn_exc:
                self._conn_exc = exc

            self._finalize(error_code=Http2ErrorCode.INTERNAL_ERROR)
            raise exc from e

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Called when the connection is established.

        This method is invoked by asyncio when a connection is successfully
        established. It initializes the HTTP/2 connection, sends the connection
        preface, and starts handling the connection.

        Args:
            transport: The asyncio transport for this connection.
        """
        self._transport = cast(asyncio.Transport, transport)

        # Initialize the HTTP/2 connection with the connection preface
        self._h2_core.initiate_connection()
        self._flush()

        if self._connection_handler:
            # Asynchronously notify the connection handler
            self.task_group.start_soon(self._connection_handler, self)

        # Mark the connection as ready for writing
        self._is_writable = True

        peer_info = transport.get_extra_info("peername")
        logger.debug("HTTP/2 connection established with %s", peer_info)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """Called when the connection is lost.

        This method is invoked by asyncio when the connection is closed or lost.
        It performs cleanup and notifies the connection of the termination.

        Args:
            exc: Exception that caused the connection loss, if any.
        """
        if exc and not self._conn_exc:
            self._conn_exc = H2ConnectionError(f"Connection lost: {type(exc).__name__}: {exc}", exc)

        error_code = Http2ErrorCode.NO_ERROR if not exc else Http2ErrorCode.CONNECT_ERROR
        self._finalize(error_code=error_code)

        logger.debug("HTTP/2 connection lost%s", f" due to {exc}" if exc else "")

    def get_buffer(self, sizehint: int) -> memoryview:
        """Get a buffer for receiving data.

        This method is called by asyncio to obtain a buffer for reading
        incoming data from the network. The returned buffer will be used
        to store received bytes.

        Args:
            sizehint: Suggested buffer size (maybe ignored).

        Returns:
            A memoryview of the internal buffer for writing received data.
        """
        return memoryview(self._buffer)

    def buffer_updated(self, nbytes: int) -> None:
        """Called when the buffer is updated with new data.

        This method processes incoming HTTP/2 data by feeding it to the
        h2 library and handling any resulting events. It's the main entry
        point for processing received network data.

        Args:
            nbytes: Number of bytes received and written to the buffer.

        Raises:
            H2ConnectionError: If a protocol error or unexpected error occurs
                during data processing.
        """
        if nbytes <= 0:
            return

        data = self._buffer[:nbytes]
        try:
            events = self._h2_core.receive_data(data)
            if events:
                self.task_group.start_soon(self.handle_events, events)
        except h2_exceptions.ProtocolError as e:
            # Handle HTTP/2 protocol violations
            try:
                self._flush()  # Try to send any pending data before terminating
            except Exception:
                pass  # Ignore flush errors during error handling

            exc = H2ConnectionError(f"HTTP/2 protocol error: {type(e).__name__}: {e}", e)
            error_code = getattr(e, "error_code", Http2ErrorCode.PROTOCOL_ERROR)

            if not self._conn_exc:
                self._conn_exc = exc

            self._finalize(error_code=error_code)
            raise exc from e
        except Exception as e:
            # Handle unexpected errors
            exc = H2ConnectionError(f"Unexpected error processing received data: {type(e).__name__}: {e}", e)

            if not self._conn_exc:
                self._conn_exc = exc

            self._finalize(error_code=Http2ErrorCode.INTERNAL_ERROR)
            raise exc from e

    async def _do_send(self, tracker: AsyncSendTracker) -> None:
        """Execute a send operation with proper flow control.

        This method coordinates the sending of HTTP/2 data by waiting for
        write readiness, triggering data generation, flushing to the transport,
        and handling any errors that occur during the process.

        Args:
            tracker: The send tracker managing this operation's lifecycle.
        """
        await self._write_ready()
        tracker.trigger()

        exc = None
        try:
            self._flush()
        except Exception as e:
            exc = e
        finally:
            tracker.complete(exc)

    async def _write_ready(self) -> None:
        """Wait until the transport is ready for writing.

        This method implements flow control by blocking when the transport
        is not writable (e.g., when the send buffer is full). It returns
        immediately if writing is allowed, or waits for the transport to
        become writable again.
        The method properly handles cleanup to prevent memory leaks from
        abandoned futures in the waiter queue.
        """
        if self._is_writable:
            return

        # Create a future to wait for write readiness
        waiter = self._loop.create_future()
        self._write_waiters.append(waiter)
        try:
            await waiter
        finally:
            # Ensure the waiter is removed even if cancelled or interrupted
            try:
                self._write_waiters.remove(waiter)
            except ValueError:
                # Waiter was already removed (e.g., by resume_writing)
                pass
