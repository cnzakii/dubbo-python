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

import typing
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Callable, Optional, TypeVar, Union

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from h2 import events as h2_events, exceptions as h2_exceptions

from dubbo.common.types import BytesLike
from dubbo.logger import logger
from dubbo.remoting.h2 import AsyncHttp2Stream, HeadersType, Http2ErrorCode
from dubbo.remoting.h2.common import ReceivedData, parse_headers
from dubbo.remoting.h2.exceptions import (
    H2ConnectionError,
    H2ProtocolError,
    H2StreamClosedError,
    H2StreamError,
    H2StreamInactiveError,
    H2StreamResetError,
    convert_h2_exception,
)

from ._tracker import AsyncSendTracker

if typing.TYPE_CHECKING:
    from ._connection import AnyIOHttp2Connection

__all__ = ["AnyIOHttp2Stream"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], Awaitable[None]]]

_DUMMY_RECEIVED_DATA = ReceivedData(memoryview(b""), 0)


class AnyIOHttp2Stream(AsyncHttp2Stream):
    """AnyIO-based implementation of an HTTP/2 stream.

    This class provides an asynchronous HTTP/2 stream implementation using the AnyIO
    framework. It handles stream lifecycle, flow control, header/data transmission,
    and protocol event processing according to RFC 7540.

    The stream supports bidirectional communication with proper flow control,
    graceful closure, and error handling. It maintains compatibility with the
    AsyncHttp2Stream interface while providing AnyIO-specific optimizations.

    """

    __slots__ = (
        "_stream_id",
        "_conn",
        "_local_exc",
        "_remote_exc",
        "_window_update",
        "_event_dispatcher",
        "_headers_received",
        "_received_headers",
        "_trailers_received",
        "_received_trailers",
        "_received_data_buffer",
    )

    _stream_id: int
    _conn: "AnyIOHttp2Connection"

    # some exceptions
    _local_exc: Optional[Exception]
    _remote_exc: Optional[Exception]

    # flow control
    _window_update: anyio.Event

    # event dispatcher
    _event_dispatcher: _EventDispatcher

    # Received headers and trailers
    _headers_received: anyio.Event
    _received_headers: Optional[HeadersType]

    _trailers_received: anyio.Event
    _received_trailers: Optional[HeadersType]

    # Received data buffer
    # Note: this is a tuple of (prev_data, sender, receiver)
    # prev_data: The previous data received from the stream
    # sender: The sender of the received data
    # receiver: The receiver of the received data
    _received_data_buffer: tuple[
        ReceivedData, MemoryObjectSendStream[ReceivedData], MemoryObjectReceiveStream[ReceivedData]
    ]

    def __init__(self, connection: "AnyIOHttp2Connection", stream_id: int) -> None:
        """Initialize a new HTTP/2 stream.

        Args:
            connection: The parent HTTP/2 connection.
            stream_id: The stream identifier (0 for uninitialized streams).
        """
        # base attributes
        self._conn = connection
        self._stream_id = stream_id

        # flow control
        self._window_update = anyio.Event()

        # received headers and trailers
        self._headers_received = anyio.Event()
        self._received_headers: Optional[HeadersType] = None
        self._trailers_received = anyio.Event()
        self._received_trailers: Optional[HeadersType] = None

        # received data buffer
        sender, receiver = anyio.create_memory_object_stream[ReceivedData](max_buffer_size=1000)
        self._received_data_buffer = (_DUMMY_RECEIVED_DATA, sender, receiver)

        self._event_dispatcher = {
            h2_events.RequestReceived: self._handle_request,
            h2_events.ResponseReceived: self._handle_response,
            h2_events.TrailersReceived: self._handle_trailers,
            h2_events.DataReceived: self._handle_data,
            h2_events.WindowUpdated: self._handle_window_update,
            h2_events.StreamReset: self._handle_stream_reset,
            h2_events.StreamEnded: self._handle_stream_ended,
            h2_events.ConnectionTerminated: self._handle_connection_terminated,
        }

        # Initialize local and remote exceptions
        exc = None
        if stream_id <= 0:
            exc = H2StreamInactiveError(stream_id, "Stream is not initialized, please call `send_headers` first")
        self._update_state(exc)

    async def __aenter__(self) -> "AnyIOHttp2Stream":
        """Enter the async context manager.

        Returns:
            The stream instance for use in async context.
        """
        return self

    async def __aexit__(
        self, exc_type: Optional[type], exc_val: Optional[Exception], exc_tb: Optional[TracebackType]
    ) -> None:
        """Exit the async context manager.

        Args:
            exc_type: Exception type if an exception occurred.
            exc_val: Exception value if an exception occurred.
            exc_tb: Exception traceback if an exception occurred.
        """
        if exc_type is not None:
            # If an exception occurred, reset the stream
            await self.reset()
        else:
            # Close the stream gracefully
            await self.aclose()

    # ------ Properties ------

    @property
    def stream_id(self) -> int:
        """Get the stream ID.

        Returns:
            The unique HTTP/2 stream identifier.
        """
        return self._stream_id

    @property
    def local_closed(self) -> bool:
        """Check if the stream is closed from the local side.

        Returns:
            True if the stream is closed locally, False otherwise.
        """
        return self._local_exc is not None

    @property
    def remote_closed(self) -> bool:
        """Check if the stream is closed from the remote side.

        Returns:
            True if the stream is closed remotely, False otherwise.
        """
        return self._remote_exc is not None

    # ------ State Machine ------

    def _update_state(self, exc: Optional[Exception]) -> None:
        """Update the stream state based on an exception.

        This method manages the stream's local and remote close states based on
        the type of exception received. It handles inactive streams, reset streams,
        and closed streams appropriately.

        Args:
            exc: The exception that triggered the state change, or None to clear state.
        """
        if not exc or isinstance(exc, H2StreamInactiveError):
            self._local_exc = exc
            self._remote_exc = exc
            return

        if isinstance(exc, H2StreamResetError):
            self._local_exc = self._remote_exc = exc

        elif isinstance(exc, H2StreamClosedError):
            # Merge partial local/remote closed state with previous state
            local_side = exc.local_side or isinstance(self._local_exc, H2StreamClosedError)
            remote_side = exc.remote_side or isinstance(self._remote_exc, H2StreamClosedError)

            merged_exc = H2StreamClosedError(self._stream_id, local_side, remote_side)
            if local_side:
                self._local_exc = merged_exc
            if remote_side:
                self._remote_exc = merged_exc

        # Unblock all waiting operations
        if self._remote_exc:
            try:
                self._headers_received.set()
                self._trailers_received.set()
                self._window_update.set()
                self._received_data_buffer[1].close()  # close the sender
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                # Ignore stream end or broken resource errors
                pass

        # Both sides are closed, unregister the stream
        if self._local_exc and self._remote_exc:
            self._conn.stream_manager.unregister(self)

    @asynccontextmanager
    async def _send_guard(self):
        """Context manager for guarding send operations.

        This context manager catches and converts various HTTP/2 exceptions into
        appropriate stream-level exceptions, ensuring consistent error handling
        across all send operations.

        Raises:
            H2ProtocolError: For protocol-level errors.
            H2ConnectionError: For connection-level errors.
            H2StreamError: For general stream errors.
        """
        try:
            yield
        except (H2ProtocolError, H2ConnectionError):
            # already handled by the stream, just re-raise
            raise
        except h2_exceptions.H2Error as e:
            inner_exc = convert_h2_exception(e)
            if isinstance(inner_exc, H2StreamClosedError):
                self._update_state(inner_exc)
            raise inner_exc from e
        except Exception as e:
            # Convert any other exception to H2StreamError
            inner_exc = H2StreamError(self._stream_id, f"Stream operation failed: {e}")
            raise inner_exc from e

    # ------ Atomic Operations ------
    def _atomic_initialize(self, headers: HeadersType, end_stream: bool) -> None:
        """Initialize the stream atomically.

        This method atomically obtains a new stream ID and sends the initial
        HEADERS frame to avoid protocol errors. Only client-side connections
        can initialize streams.

        Args:
            headers: The headers to send with the initial HEADERS frame.
            end_stream: Whether to end the stream after sending headers.

        Raises:
            H2ProtocolError: If stream is already initialized or not on client side.
        """
        if self._stream_id > 0:
            raise H2ProtocolError("Stream id is already set, cannot be changed")

        h2_core = self._conn.h2_core
        if not h2_core.config.client_side:
            raise H2ProtocolError("Only client can initialize the stream")

        # Clear abnormal status
        self._update_state(None)

        # In client-side HTTP/2, obtaining a new stream ID and sending the initial HEADERS frame
        # must be treated as an atomic operation to avoid protocol errors.
        self._stream_id = h2_core.get_next_available_stream_id()
        self._conn.stream_manager.register(self)
        self._atomic_send_headers(headers, end_stream)

    def _atomic_send_headers(self, headers: HeadersType, end_stream: bool) -> None:
        """Send headers or trailers to the stream atomically.

        Args:
            headers: The headers to send.
            end_stream: Whether to end the stream after sending headers.

        Raises:
            H2StreamClosedError: If the stream is already closed locally.
            H2StreamResetError: If the stream was reset.
            H2StreamInactiveError: If the stream is not initialized.
            H2ProtocolError: If other protocol violations occur.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.send_headers(
            stream_id=self._stream_id,
            headers=headers,
            end_stream=end_stream,
        )

        # update local exception
        if end_stream:
            exc = H2StreamClosedError(self._stream_id, True, False)
            self._update_state(exc)

    def _atomic_send_data(self, data_view: memoryview, end_stream: bool) -> int:
        """Send data to the stream respecting flow control.

        Attempts to send a chunk of data within the current flow control window.

        Args:
            data_view: The data to send as a memoryview.
            end_stream: Whether this is the final frame for the stream.

        Returns:
            The number of bytes consumed from data_view, or -1 if no window available.

        Raises:
            H2StreamClosedError: If the stream is already closed locally.
            H2StreamResetError: If the stream was reset.
            H2StreamInactiveError: If the stream is not initialized.
            H2ProtocolError: If other protocol violations occur.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        window_size = h2_core.local_flow_control_window(self._stream_id)
        data_len = len(data_view)

        if window_size == 0:
            # No window available, wait for a window update
            return -1

        chunk_size = min(window_size, data_len)
        chunk_view = data_view[:chunk_size]
        is_final_chunk = chunk_size == data_len

        # Send DATA frame
        h2_core.send_data(
            stream_id=self._stream_id, data=chunk_view, end_stream=end_stream if is_final_chunk else False
        )

        if end_stream and is_final_chunk:
            # Stream is fully closed locally
            exc = H2StreamClosedError(self._stream_id, True, False)
            self._update_state(exc)

        return chunk_size

    def _atomic_ack_data(self, ack_size: int) -> None:
        """Acknowledge received data for flow control.

        Args:
            ack_size: The size of data to acknowledge in bytes.

        Raises:
            H2StreamClosedError: If the stream is already closed locally.
            H2StreamResetError: If the stream was reset.
            H2StreamInactiveError: If the stream is not initialized.
            H2ProtocolError: If other protocol violations occur.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.acknowledge_received_data(ack_size, self._stream_id)

    def _atomic_reset(self, error_code: Union[Http2ErrorCode, int]) -> None:
        """Reset the stream with an error code.

        Args:
            error_code: The HTTP/2 error code for the reset.

        Raises:
            H2StreamClosedError: If the stream is already closed locally.
            H2StreamInactiveError: If the stream is not initialized.
            H2ProtocolError: If other protocol violations occur.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.reset_stream(self._stream_id, error_code)

        # update local and remote exceptions
        exc = H2StreamResetError(self._stream_id, error_code, False, "Stream reset by local peer")
        self._update_state(exc)

    # ------ Public Methods ------
    async def send_headers(self, headers: HeadersType, end_stream: bool = False) -> None:
        """Send headers or trailers to the stream."""
        async with self._send_guard():
            if self._stream_id <= 0:
                # Stream is not initialized, initialize it with the provided headers
                tracker = AsyncSendTracker(lambda: self._atomic_initialize(headers, end_stream))
            else:
                # Stream already initialized, send headers frame
                tracker = AsyncSendTracker(lambda: self._atomic_send_headers(headers, end_stream))

            # Trigger the tracker to send data
            await self._conn.send(tracker)

            # Wait for the result or exception
            await tracker.result()

    async def send_data(self, data: BytesLike, end_stream: bool = False) -> None:
        """Send data in chunks according to HTTP/2 flow control."""
        async with self._send_guard():
            data_view = memoryview(data)

            while True:
                tracker: AsyncSendTracker[int] = AsyncSendTracker(lambda: self._atomic_send_data(data_view, end_stream))
                await self._conn.send(tracker)
                consumed = await tracker.result()

                if consumed == -1:
                    # Flow control window is zero, wait for a window update
                    if self._window_update.is_set():
                        self._window_update = anyio.Event()
                    await self._window_update.wait()
                else:
                    # Data was sent, update the data view
                    data_view = data_view[consumed:]
                    if len(data_view) == 0:
                        break

    async def reset(self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR) -> None:
        """Reset the stream with an error code."""
        async with self._send_guard():
            tracker = AsyncSendTracker(lambda: self._atomic_reset(error_code))
            await self._conn.send(tracker)
            await tracker.result()

    async def aclose(self) -> None:
        """Close the stream gracefully."""
        await self.send_data(b"", end_stream=True)

    async def receive_headers(self) -> HeadersType:
        """Wait for headers from the remote peer."""
        # wait for the event to be set
        await self._headers_received.wait()
        # get the headers
        headers = self._received_headers
        if headers:
            return headers

        # if headers is None, it means the stream is closed
        if self._remote_exc:
            raise self._remote_exc
        else:
            raise H2StreamError(self._stream_id, "No headers received, but stream is not closed")

    async def receive_trailers(self) -> HeadersType:
        """Wait for trailers from the remote peer."""
        # wait for the event to be set
        await self._trailers_received.wait()
        # get the headers
        trailers = self._received_trailers
        if trailers:
            return trailers

        # if trailers is None, it means the stream is closed
        if self._remote_exc:
            raise self._remote_exc
        else:
            raise H2StreamError(self._stream_id, "No trailers received, but stream is not closed")

    async def receive_data(self, max_bytes: int = -1, strict: bool = False) -> bytes:
        """Receive data from the stream with flow control."""
        if max_bytes <= 0 and max_bytes != -1:
            raise ValueError("max_bytes must be -1 or greater than 0")
        elif max_bytes == -1 and strict:
            raise ValueError("max_bytes cannot be -1 when strict is True")

        prev_data, sender, receiver = self._received_data_buffer
        chunks: list[memoryview] = []
        total_ack = 0
        total_bytes = 0

        try:
            while max_bytes == -1 or total_bytes < max_bytes:
                # 1. get the data from the previous data
                if not prev_data.is_empty():
                    chunk = prev_data.get_data(max_bytes - total_bytes if max_bytes > 0 else -1)
                    total_bytes += len(chunk)
                    chunks.append(chunk)
                    total_ack += prev_data.ack_size

                # 2. Do we have enough data now?
                if not prev_data.is_empty():
                    # previous data is not empty, we can break
                    break

                # 3. Do we need to block to get data from a stream or not?
                if total_bytes > 0 and not strict:
                    # We have received some, and the pattern is not strict,
                    # only attempting to get more data non-blocking.
                    prev_data = receiver.receive_nowait()
                else:
                    # We have not received any data or have not obtained enough data,
                    # then we need to wait for the blockage.

                    if total_ack >= self._conn.h2_core.remote_flow_control_window(self._stream_id):
                        # Send ACK if total_ack exceeds the remote flow control window limit
                        tracker = AsyncSendTracker(lambda: self._atomic_ack_data(total_ack), no_wait=True)
                        await self._conn.send(tracker)
                        exc = await tracker.exception()  # Ignore the exception
                        if not exc:
                            total_ack = 0

                    prev_data = await receiver.receive()
        except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.EndOfStream):
            # Ignore stream end or broken resource errors
            pass

        if self._remote_exc and (total_bytes != max_bytes if strict else total_bytes == 0):
            # Raise if no data was received, or strict mode was not fully satisfied due to stream closure.
            raise self._remote_exc

        # update the received data buffer
        self._received_data_buffer = (prev_data, sender, receiver)

        # If we have received data, we need to send the ACK to the remote peer
        if total_ack > 0:
            tracker = AsyncSendTracker(lambda: self._atomic_ack_data(total_ack), no_wait=True)
            await self._conn.send(tracker)
            await tracker.exception()  # Ignore the exception

        return b"".join(chunks)

    # ------ Event Handlers ------
    async def _handle_request(self, event: h2_events.RequestReceived) -> None:
        """Handle HTTP/2 request received event.

        Args:
            event: The request received event from h2 library.
        """
        self._received_headers = parse_headers(event.headers)
        self._headers_received.set()

    async def _handle_response(self, event: h2_events.ResponseReceived) -> None:
        """Handle HTTP/2 response received event.

        Args:
            event: The response received event from h2 library.
        """
        self._received_headers = parse_headers(event.headers)
        self._headers_received.set()

    async def _handle_trailers(self, event: h2_events.TrailersReceived) -> None:
        """Handle HTTP/2 trailers received event.

        Args:
            event: The trailers received event from h2 library.
        """
        self._received_trailers = parse_headers(event.headers)
        self._trailers_received.set()

    async def _handle_data(self, event: h2_events.DataReceived) -> None:
        """Handle HTTP/2 data received event.

        Args:
            event: The data received event from h2 library.
        """
        # Update the received data buffer
        sender = self._received_data_buffer[1]
        try:
            sender.send_nowait(ReceivedData.from_h2(event))
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            logger.error(
                "Stream closed — cannot process received data. stream_id=%s, data_length=%d",
                self._stream_id,
                len(event.data or b""),
            )
        except anyio.WouldBlock:
            logger.error(
                "Receive buffer full — dropping data. stream_id=%s, data_length=%d",
                self._stream_id,
                len(event.data or b""),
            )

    async def _handle_window_update(self, event: h2_events.WindowUpdated) -> None:
        """Handle HTTP/2 window update event.

        Args:
            event: The window update event from h2 library.
        """
        self._window_update.set()

    async def _handle_stream_reset(self, event: h2_events.StreamReset) -> None:
        """Handle HTTP/2 stream reset event.

        Args:
            event: The stream reset event from h2 library.
        """
        error_code: Union[int, None] = event.error_code
        if error_code is None:
            # If no error code is provided, use NO_ERROR
            error_code = Http2ErrorCode.NO_ERROR
        exc = H2StreamResetError(self._stream_id, error_code, False)
        self._update_state(exc)

    async def _handle_stream_ended(self, event: h2_events.StreamEnded) -> None:
        """Handle HTTP/2 stream end event.

        Args:
            event: The stream end event from h2 library.
        """
        exc = H2StreamClosedError(self._stream_id, False, True)
        self._update_state(exc)

    async def _handle_connection_terminated(self, event: h2_events.ConnectionTerminated) -> None:
        """Handle HTTP/2 connection termination event.

        Args:
            event: The connection termination event from h2 library.
        """
        exc = H2StreamClosedError(self._stream_id, True, True, "Stream closed due to connection termination")
        self._update_state(exc)

    async def _handle_ignored_event(self, event: h2_events.Event) -> None:
        """Handle ignored HTTP/2 events.

        Args:
            event: The ignored event from h2 library.
        """
        logger.debug("Ignored event: %s", type(event))

    async def handle_event(self, event: h2_events.Event) -> None:
        """Handle HTTP/2 events from the connection.

        Args:
            event: The HTTP/2 event to handle.
        """
        handler = self._event_dispatcher.get(type(event), self._handle_ignored_event)
        await handler(event)
