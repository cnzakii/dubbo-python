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
import typing
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Callable, Optional, TypeVar, Union

from h2 import events as h2_events, exceptions as h2_exceptions

from dubbo.logger import logger
from dubbo.remoting.h2.exceptions import (
    H2ConnectionError,
    H2ProtocolError,
    H2StreamClosedError,
    H2StreamError,
    H2StreamInactiveError,
    H2StreamResetError,
    convert_h2_exception,
)

from ..base import AsyncHttp2Connection, AsyncHttp2Stream, HeadersType, parse_headers
from ..registries import Http2ErrorCode
from ._buffer import StreamDataBuffer
from .tracker import AioSendTracker

if typing.TYPE_CHECKING:
    from .protocol import Http2Protocol

__all__ = ["AioHttp2Stream"]

_T_Event = TypeVar("_T_Event", bound=h2_events.Event)
_EventDispatcher = dict[type[_T_Event], Callable[[_T_Event], None]]


class AioHttp2Stream(AsyncHttp2Stream):
    """Base class for HTTP/2 streams using asyncio."""

    __slots__ = (
        "_conn",
        "_id",
        "_remote_exc",
        "_local_exc",
        "_window_update",
        "_received_headers",
        "_headers_received",
        "_received_trailers",
        "_trailers_received",
        "_received_data",
        "_event_dispatcher",
    )

    _conn: "Http2Protocol"
    _id: int
    _remote_exc: Optional[H2StreamError]
    _local_exc: Optional[H2StreamError]

    _window_update: asyncio.Event

    _received_headers: Optional[HeadersType]
    _headers_received: Optional[asyncio.Event]

    _received_trailers: Optional[HeadersType]
    _trailers_received: Optional[asyncio.Event]
    _received_data: StreamDataBuffer

    _event_dispatcher: _EventDispatcher

    def __init__(self, connection: "Http2Protocol", stream_id: int) -> None:
        self._conn = connection
        self._id = stream_id
        self.local_exc = None
        self.remote_exc = (
            None
            if stream_id > 0
            else H2StreamInactiveError(message="Stream is not initialized, please call `send_headers` first")
        )

        self._window_update = asyncio.Event()
        self._received_headers = None
        self._headers_received = None
        self._received_trailers = None
        self._trailers_received = None
        self._received_data = StreamDataBuffer(ack_callback=self.ack_data)

        self._event_dispatcher: _EventDispatcher = {
            h2_events.RequestReceived: self._handle_headers_received,
            h2_events.ResponseReceived: self._handle_headers_received,
            h2_events.TrailersReceived: self._handle_trailers_received,
            h2_events.DataReceived: self._handle_data_received,
            h2_events.StreamEnded: self._handle_stream_ended,
            h2_events.StreamReset: self._handle_stream_reset,
        }

    async def __aenter__(self):
        """Enter the asynchronous context manager."""
        return self

    async def __aexit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        """Exit the asynchronous context manager."""
        await self.aclose()

    # ------ Properties ------

    @property
    def connection(self) -> AsyncHttp2Connection:
        """Get the connection this stream belongs to."""
        return self._conn

    @property
    def id(self) -> int:
        """Get the stream ID."""
        return self._id

    @id.setter
    def id(self, value: int) -> None:
        if self._id > 0:
            raise H2ProtocolError(f"Stream ID has already been assigned (id={self._id}). Cannot reassign to {value}.")
        self._id = value

    @property
    def local_exc(self) -> Optional[H2StreamError]:
        """Get the local exception for this stream."""
        return self._local_exc

    @local_exc.setter
    def local_exc(self, exc: Optional[H2StreamError]) -> None:
        """Set the local exception for this stream."""
        self._local_exc = exc

    @property
    def remote_exc(self) -> Optional[H2StreamError]:
        """Get the remote exception for this stream."""
        return self._remote_exc

    @remote_exc.setter
    def remote_exc(self, exc: Optional[H2StreamError]) -> None:
        """Set the remote exception for this stream."""
        self._remote_exc = exc

    # ------ Stream State Management ------
    def _on_remote_closed(self, exc: H2StreamError) -> None:
        """Clean up remote sources."""
        if self._headers_received:
            self._headers_received.set()
        if self._trailers_received:
            self._trailers_received.set()
        self._window_update.set()
        self._received_data.close()

    @asynccontextmanager
    async def _send_guard(self):
        """Context manager for guarding send operations."""
        try:
            yield
        except H2StreamError:
            raise  # re-raise
        except H2ConnectionError as e:
            raise H2StreamError(self._id, "Stream operation failed due to connection error") from e
        except H2ProtocolError as e:
            raise H2StreamError(self._id, f"Stream operation failed due to protocol error: {e}") from e
        except h2_exceptions.H2Error as e:
            inner_exc = convert_h2_exception(e)
            if isinstance(inner_exc, H2StreamClosedError):
                self.handle_stream_error(inner_exc)
            raise inner_exc from e
        except Exception as e:
            # Convert any other exception to H2StreamError
            inner_exc = H2StreamError(self._id, f"Stream operation failed: {e}")
            raise inner_exc from e

    # ------ Sending Methods ------
    async def send_headers(self, headers: HeadersType, end_stream: bool = False) -> None:
        """Send headers on this stream."""
        async with self._send_guard():
            if self._id <= 0:
                # Stream is not initialized, initialize it with the provided headers
                tracker = AioSendTracker(lambda: self._atomic_initialize(headers, end_stream))
            else:
                # Stream already initialized, send headers frame
                tracker = AioSendTracker(lambda: self._atomic_send_headers(headers, end_stream))

            # Trigger the tracker to send data
            await self._conn.send(tracker)

            # Wait for the result or exception
            await tracker.result()

    async def send_data(self, data: bytes, end_stream: bool = False) -> None:
        """Send data on this stream."""
        async with self._send_guard():
            data_view = memoryview(data)

            while True:
                tracker: AioSendTracker[int] = AioSendTracker(lambda: self._atomic_send_data(data_view, end_stream))
                await self._conn.send(tracker)
                consumed = await tracker.result()

                if consumed == -1:
                    # Flow control window is zero, wait for a window update
                    self._window_update.clear()
                    await self._window_update.wait()
                else:
                    # Data was sent, update the data view
                    data_view = data_view[consumed:]
                    if len(data_view) == 0:
                        break

    async def end(self) -> None:
        """Sends an empty DATA frame with the END_STREAM flag set, indicating that
        no more data or headers will be sent on this stream."""
        await self.send_data(b"", end_stream=True)

    async def reset(self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR) -> None:
        """Reset the stream with the given error code."""
        async with self._send_guard():
            tracker = AioSendTracker(lambda: self._atomic_reset(error_code))
            await self._conn.send(tracker)
            await tracker.result()

    async def aclose(self) -> None:
        """Close the stream asynchronously."""
        if self.local_exc and isinstance(self.local_exc, H2StreamInactiveError):
            raise self.local_exc

        logger.debug("Closing HTTP/2 stream %s", self._id)

        # Acknowledge any unacknowledged data before closing
        unacked_size = self._received_data.get_unacked_size()
        if unacked_size:
            logger.debug(
                "Stream %s: acknowledging %d bytes of unacknowledged data before closing", self._id, unacked_size
            )
            tracker = AioSendTracker(lambda: self._atomic_ack_data(unacked_size), no_wait=True)
            await self._conn.send(tracker)

        if self.local_exc and self._remote_exc:
            logger.debug("Stream %s: both local and remote sides already closed", self._id)
            return
        else:
            logger.warning(
                "Stream %s: stream not fully closed; sending RST_STREAM with NO_ERROR to force cleanup",
                self._id,
            )
            await self.reset(Http2ErrorCode.NO_ERROR)

    # ------ Receiving Methods ------
    async def receive_headers(self) -> HeadersType:
        """Wait for headers to be received."""
        if self._received_headers:
            return self._received_headers

        if self.remote_exc:
            raise self.remote_exc

        self._headers_received = asyncio.Event()
        await self._headers_received.wait()

        # Recursive call is safe: headers will be set before next wait,
        # so recursion will not go deeper than one additional level.
        return await self.receive_headers()

    async def receive_trailers(self) -> HeadersType:
        """Wait for trailers to be received."""
        if self._received_trailers:
            return self._received_trailers

        if self.remote_exc:
            raise self.remote_exc

        self._trailers_received = asyncio.Event()
        await self._trailers_received.wait()

        return await self.receive_trailers()

    def ack_data(self, ack_size: int) -> None:
        """Acknowledge the receipt of data by the stream."""
        try:
            self._atomic_ack_data(ack_size)
            self._conn.flush()
        except Exception:
            pass

    async def receive_data(self, max_bytes: int = -1) -> bytes:
        """Wait for data to be received."""
        try:
            return await self._received_data.get_data(max_bytes)
        except RuntimeError:
            # buffer is closed, stream is likely closed
            if self.remote_exc:
                raise self.remote_exc
            else:
                # This should never happen
                raise H2StreamError(
                    self._id,
                    f"Stream {self._id} closed before receiving data, "
                    f"but no remote exception was recorded. This indicates a protocol or internal logic error.",
                )

    async def receive_data_exactly(self, exact_bytes: int) -> bytes:
        """Wait for exactly `exact_bytes` of data to be received."""
        if exact_bytes < 0:
            raise ValueError("exact_bytes must be non-negative")

        try:
            return await self._received_data.get_data_exactly(exact_bytes)
        except RuntimeError:
            # buffer is closed, stream is likely closed
            if self.remote_exc:
                raise self.remote_exc
            else:
                # This should never happen
                raise H2StreamError(
                    self._id,
                    f"Stream {self._id} closed before receiving {exact_bytes} bytes, "
                    f"but no remote exception was recorded. This indicates a protocol or internal logic error.",
                )

    # ------ Event Handling ------
    def handle_event(self, event: h2_events.Event):
        """Handle an HTTP/2 event."""
        handle_fn = self._event_dispatcher.get(type(event), self._handle_ignored_event)
        handle_fn(event)

    def _handle_headers_received(self, event: Union[h2_events.RequestReceived, h2_events.ResponseReceived]) -> None:
        """Handle headers received event."""
        self._received_headers = parse_headers(event.headers)
        if self._headers_received:
            self._headers_received.set()

    def _handle_trailers_received(self, event: h2_events.TrailersReceived) -> None:
        """Handle trailers received event."""
        self._received_trailers = parse_headers(event.headers)
        if self._trailers_received:
            self._trailers_received.set()

    def _handle_data_received(self, event: h2_events.DataReceived) -> None:
        """Handle data received event."""
        try:
            self._received_data.append(event.data or b"", event.flow_controlled_length or 0)
        except RuntimeError as e:
            logger.error("Data reception failed on stream %s: %s", self._id, e)

    def _handle_stream_ended(self, event: h2_events.StreamEnded) -> None:
        """Handle stream ended event."""
        exc = H2StreamClosedError(self._id, False, True)
        self.handle_stream_error(exc)

    def _handle_stream_reset(self, event: h2_events.StreamReset) -> None:
        """Handle stream reset event."""
        error_code = Http2ErrorCode(event.error_code) if event.error_code else Http2ErrorCode.NO_ERROR
        exc = H2StreamResetError(self._id, error_code, event.remote_reset)
        self.handle_stream_error(exc)

    def _handle_window_update(self, event: h2_events.WindowUpdated) -> None:
        """Handle window update event."""
        self._window_update.set()

    def _handle_ignored_event(self, event: h2_events.Event) -> None:
        """Handle events that are not explicitly handled."""
        logger.debug("Ignored event: %r on stream %s", event, self._id)
