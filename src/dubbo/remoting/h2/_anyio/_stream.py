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
from types import TracebackType
from typing import Any, Callable, Optional, Union

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from h2 import events as h2_events

from dubbo import logger
from dubbo.common.types import BytesLike
from dubbo.remoting.h2 import AsyncHttp2Stream, Http2ErrorCodes, Http2Headers
from dubbo.remoting.h2.exceptions import (
    H2ProtocolError,
    H2StreamClosedError,
    H2StreamInactiveError,
    H2StreamResetError,
)

from ._tracker import SendTracker

if typing.TYPE_CHECKING:
    from ._connection import AnyIOHttp2Connection

_LOGGER = logger.get_instance()


class _ReceivedData:
    """
    Encapsulates received DATA frame payload and flow-control size.
    Internally stores a memoryview over the data to avoid copies.
    """

    __slots__ = ("_data_view", "_ack_size")

    _data_view: memoryview
    _ack_size: int

    def __init__(self, data_view: memoryview, ack_size: int) -> None:
        self._data_view = data_view
        self._ack_size = ack_size

    @property
    def ack_size(self) -> int:
        """
        Return the ack size and reset it to zero (one-shot property).
        """
        size = self._ack_size
        self._ack_size = 0
        return size

    def is_empty(self) -> bool:
        """
        Return True if all data has been consumed.
        """
        return len(self._data_view) == 0

    def get_data(self, max_bytes: int) -> memoryview:
        """
        Return a memoryview of up to `max_bytes` of the data and advance the view.
        """
        view = self._data_view

        if max_bytes == -1 or max_bytes >= len(view):
            # Return the entire view
            chunk = view
            self._data_view = memoryview(b"")
        else:
            chunk = view[:max_bytes]
            self._data_view = view[max_bytes:]

        return chunk

    @classmethod
    def from_h2(cls, event: h2_events.DataReceived) -> "_ReceivedData":
        """
        Create a _ReceivedData instance from a h2 event.
        """
        return cls(memoryview(event.data or b""), event.flow_controlled_length or 0)


_DUMMY_RECEIVED_DATA = _ReceivedData(memoryview(b""), 0)


class AnyIOHttp2Stream(AsyncHttp2Stream):
    _stream_id: int
    _conn: "AnyIOHttp2Connection"

    # some exceptions
    _local_exc: Optional[Exception]
    _remote_exc: Optional[Exception]

    # flow control
    _window_update: anyio.Event

    # Received data
    _received_event: anyio.Event
    _received_headers: Optional[Http2Headers]
    _received_trailers: Optional[Http2Headers]

    # Received data buffer
    # Note: this is a tuple of (prev_data, sender, receiver)
    # prev_data: The previous data received from the stream
    # sender: The sender of the received data
    # receiver: The receiver of the received data
    _received_data_buffer: tuple[
        _ReceivedData, MemoryObjectSendStream[_ReceivedData], MemoryObjectReceiveStream[_ReceivedData]
    ]

    def __init__(self, connection: "AnyIOHttp2Connection", stream_id: int = -1) -> None:
        self._stream_id = stream_id
        self._conn = connection
        self._window_update = anyio.Event()
        self._received_event = anyio.Event()
        self._received_headers = None
        self._received_trailers = None
        sender, receiver = anyio.create_memory_object_stream[_ReceivedData](max_buffer_size=1000)
        self._received_data_buffer = (_DUMMY_RECEIVED_DATA, sender, receiver)

        # Initialize local and remote exceptions
        if stream_id <= 0:
            self._update_state(
                H2StreamInactiveError(stream_id, "Stream is not initialized, please call `send_headers` first")
            )
        else:
            self._update_state(None)

    async def __aenter__(self) -> "AnyIOHttp2Stream":
        """
        Enter the context manager for the stream.
        """
        return self

    async def __aexit__(
        self, exc_type: Optional[type], exc_val: Optional[Exception], exc_tb: Optional[TracebackType]
    ) -> None:
        """
        Exit the context manager for the stream.
        """
        if exc_type is not None:
            # If an exception occurred, reset the stream
            await self.reset()
        else:
            # Close the stream gracefully
            await self.aclose()

    @property
    def stream_id(self) -> int:
        """
        The stream id of the stream.
        """
        return self._stream_id

    def _update_state(self, exc: Optional[Exception]) -> None:
        """
        Update the local and remote exceptions based on the new exception.
        - If exc is None, clears both local and remote exceptions.
        - If exc is a hard termination, sets both sides to the exception.
        - If exc is a H2StreamClosedError, merges existing close state.
        """
        if not exc or isinstance(exc, (H2StreamInactiveError, H2StreamResetError)):
            # These indicate hard termination: both sides are considered closed
            self._local_exc = exc
            self._remote_exc = exc
            return

        if isinstance(exc, H2StreamClosedError):
            # Start with the incoming states
            local_side = exc.local_side
            remote_side = exc.remote_side

            # Merge with existing local and remote close states if present
            for side_exc in (self._local_exc, self._remote_exc):
                if isinstance(side_exc, H2StreamClosedError):
                    local_side = side_exc.local_side or local_side
                    remote_side = side_exc.remote_side or remote_side

            # Apply merged state to new exception
            exc.local_side = local_side
            exc.remote_side = remote_side

            # Update internal states based on sides
            if local_side:
                self._local_exc = exc
            if remote_side:
                self._remote_exc = exc

            if local_side and remote_side:
                # unregister the stream
                self._conn.stream_manager.unregister(self)

    def _initialize(self, headers: Http2Headers, end_stream: bool = False) -> None:
        """
        Initialize the stream.
        Note: Only client can initialize the stream.
        """
        if self._stream_id > 0:
            raise H2ProtocolError("Stream id is already set, cannot be changed")

        # Clear abnormal status
        self._update_state(None)

        h2_core = self._conn.h2_core
        # In client-side HTTP/2, obtaining a new stream ID and sending the initial HEADERS frame
        # must be treated as an atomic operation to avoid protocol errors.
        self._stream_id = h2_core.get_next_available_stream_id()
        self._conn.stream_manager.register(self)
        self._do_send_headers(headers, end_stream)

    def _do_send_headers(self, headers: Http2Headers, end_stream: bool) -> None:
        """
        Send TRAILERS to the stream.

        :param headers: The headers to send.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.send_headers(
            stream_id=self._stream_id,
            headers=headers.to_hpack(),
            end_stream=end_stream,
        )

        # update local exception
        if end_stream:
            exc = H2StreamClosedError(self._stream_id, True, False)
            self._update_state(exc)

    async def send_headers(self, headers: Http2Headers, end_stream: bool = False) -> None:
        """
        Send HEADERS or TRAILERS to the stream.

        Sends a HEADERS frame if it is the first call, and a TRAILERS frame if it is the last call.

        :param headers: The headers to send.
        :param end_stream: Whether this is the last frame for the stream (i.e., end the stream).
        """
        if self._stream_id <= 0:
            # Stream is not initialized, initialize it with the provided headers
            tracker = SendTracker(lambda: self._initialize(headers, end_stream))
        else:
            # Stream already initialized, send headers frame
            tracker = SendTracker(lambda: self._do_send_headers(headers, end_stream))

        # Trigger the tracker to send data
        await self._conn.send(tracker)

        # Wait for the result or exception
        await tracker.result()

    def _do_send_data_by_flow_control(self, data_view: memoryview, end_stream: bool) -> int:
        """
        Attempt to send a chunk of DATA to the stream, respecting the local flow control window.

        :param data_view: The memoryview of the data to send.
        :param end_stream: Whether this is the final frame for the stream.
        :return: The number of bytes consumed from the data_view. If window is zero, return -1.
        """
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

    async def send_data(self, data: BytesLike, end_stream: bool = False) -> None:
        """
        Send data in chunks according to HTTP/2 flow control.

        :param data: The data to send (bytes, bytearray, or memoryview).
        :param end_stream: Whether this is the last DATA frame to send on this stream.
        """
        data_view = memoryview(data)

        while True:
            if self._local_exc:
                raise self._local_exc

            tracker = SendTracker(lambda: self._do_send_data_by_flow_control(data_view, end_stream))
            await self._conn.send(tracker)
            consumed = await tracker.result()

            if consumed == -1:
                # Flow control window is zero, wait for a window update
                self._window_update = anyio.Event()
                await self._window_update.wait()
            else:
                # Data was sent, update the data view
                data_view = data_view[consumed:]
                if len(data_view) == 0:
                    break

    async def aclose(self) -> None:
        """
        Close the stream.
        """
        # send empty DATA frame
        await self.send_data(b"", end_stream=True)

    def _do_reset(self, error_code: Union[Http2ErrorCodes, int]) -> None:
        """
        Send RST_STREAM to the stream.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.reset_stream(self._stream_id, error_code)

        # update local and remote exceptions
        exc = H2StreamResetError(self._stream_id, error_code, False, "Stream reset by local peer")
        self._update_state(exc)

    async def reset(self, error_code: Union[Http2ErrorCodes, int] = Http2ErrorCodes.NO_ERROR) -> None:
        """
        Send RST_STREAM to the stream.
        """
        tracker = SendTracker(lambda: self._do_reset(error_code))
        await self._conn.send(tracker)
        await tracker.result()

    async def _wait_for_field(self, getter: Callable[[], Optional[Any]], timeout: Optional[float] = None) -> Any:
        """
        Wait for a specific field (headers or trailers) to become available,
        honoring total timeout duration.

        :param getter: Function to retrieve the awaited field.
        :param timeout: Total timeout in seconds (None means wait indefinitely).
        :return: The awaited value. If timeout and no value is received, returns None.
        :raises Exception: If a remote exception is raised.
        """
        value: Any = None
        with anyio.move_on_after(timeout):
            while True:
                value = getter()
                if value is not None:
                    break

                # If the stream is closed, raise the exception
                if self._remote_exc:
                    raise self._remote_exc

                # If the value is None and the stream is not closed, wait for the event
                if self._received_event.is_set():
                    self._received_event = anyio.Event()
                await self._received_event.wait()

        return value

    async def receive_headers(self, timeout: Optional[float] = None) -> Optional[Http2Headers]:
        """
        Wait until headers are received from the remote peer.

        :param timeout: Optional timeout in seconds.
        :return: The received HTTP/2 headers. If timeout and no headers are received, returns None.
        """
        return await self._wait_for_field(lambda: self._received_headers, timeout=timeout)

    async def receive_trailers(self, timeout: Optional[float] = None) -> Optional[Http2Headers]:
        """
        Wait until trailers are received from the remote peer.

        :param timeout: Optional timeout in seconds.
        :return: The received HTTP/2 trailers. If timeout and no trailers are received, returns None.
        """
        return await self._wait_for_field(lambda: self._received_trailers, timeout=timeout)

    def _do_ack_data(self, ack_size: int) -> None:
        """
        Acknowledge the received data to the remote peer by updating flow control.

        :param ack_size: The size of the data to acknowledge.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.acknowledge_received_data(ack_size, self._stream_id)

    async def receive_data(self, max_bytes: int = -1, timeout: Optional[float] = None) -> bytes:
        """
        Receive up to `max_bytes` of data from the stream.

        :param max_bytes: The maximum number of bytes to receive (0 means no limit).
        :param timeout: Optional timeout for waiting on data.
        :return: The received bytes.
        :raises: Any stored remote exception.
        """
        prev_data, sender, receiver = self._received_data_buffer
        outbound_data = bytearray()
        total_ack = 0
        total_bytes = 0

        try:
            with anyio.move_on_after(timeout):
                while max_bytes == -1 or total_bytes < max_bytes:
                    # 1. get the data from the previous data
                    if not prev_data.is_empty():
                        chunk = prev_data.get_data(max_bytes)
                        total_bytes += len(chunk)
                        outbound_data.extend(chunk)
                        total_ack += prev_data.ack_size

                    # 2. Do we have enough data now?
                    if not prev_data.is_empty():
                        # previous data is not empty, we can break
                        break

                    # 3. Do we need to block to get data from a stream or not?
                    if total_bytes > 0 and timeout is None:
                        # We have received some, and the next step is to attempt to get more data non-blocking.
                        prev_data = receiver.receive_nowait()
                    else:
                        # We have not received any data at present, so we need to wait blocked.
                        prev_data = await receiver.receive()
        except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.WouldBlock):
            # Ignore stream end or broken resource errors
            pass

        if total_bytes == 0 and max_bytes > 0 and self._remote_exc:
            # If we have not received any data and the stream is closed, raise the exception
            raise self._remote_exc

        # update the received data buffer
        self._received_data_buffer = (prev_data, sender, receiver)

        # If we have received data, we need to send the ACK to the remote peer
        if total_ack > 0:
            tracker = SendTracker(lambda: self._do_ack_data(total_ack), no_wait=True)
            await self._conn.send(tracker)
            await tracker.exception()  # ignore the exception

        return bytes(outbound_data)

    async def handle_event(self, event: h2_events.Event) -> None:
        """
        Handle the event from the h2 connection.
        """
        if isinstance(event, (h2_events.RequestReceived, h2_events.ResponseReceived)):
            # Handle request or response received
            assert event.headers is not None
            self._received_headers = Http2Headers.from_hpack(event.headers)
        elif isinstance(event, h2_events.TrailersReceived):
            # Handle trailers received
            assert event.headers is not None
            self._received_trailers = Http2Headers.from_hpack(event.headers)
        elif isinstance(event, h2_events.DataReceived):
            # Handle data received
            _, sender, _ = self._received_data_buffer

            try:
                sender.send_nowait(_ReceivedData.from_h2(event))
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                _LOGGER.error(
                    f"[H2Stream] Stream closed — cannot process received data. "
                    f"stream_id={self._stream_id}, data_length={len(event.data or b'')}"
                )
            except anyio.WouldBlock:
                _LOGGER.error(
                    f"[H2Stream] Receive buffer full — dropping data. "
                    f"stream_id={self._stream_id}, data_length={len(event.data or b'')}"
                )
        elif isinstance(event, h2_events.WindowUpdated):
            # Notify the window update
            self._window_update.set()

        # Update the state based on the event
        if isinstance(event, (h2_events.StreamReset, h2_events.StreamEnded, h2_events.ConnectionTerminated)):
            if isinstance(event, h2_events.StreamReset):
                assert event.error_code is not None
                exc = H2StreamResetError(self._stream_id, event.error_code, True, "Stream reset by remote peer")
            elif isinstance(event, h2_events.ConnectionTerminated):
                exc = H2StreamClosedError(self._stream_id, True, True, "Connection terminated by remote peer")  # type: ignore[assignment]
            else:
                exc = H2StreamClosedError(self._stream_id, False, True)  # type: ignore[assignment]

            # handle stream reset or end
            self._update_state(exc)
            _, sender, _ = self._received_data_buffer
            await sender.aclose()  # Close the sender to notify the receiver

        # notify the received event
        if not self._received_event.is_set():
            self._received_event.set()
