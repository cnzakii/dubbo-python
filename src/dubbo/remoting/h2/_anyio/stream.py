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
import abc
import functools
import queue
import sys
from typing import Optional

import anyio
from h2 import events as h2_events

from ..exceptions import StreamClosedException, StreamException, StreamResetException
from .protocol import AnyIOHttp2Connection


class BaseBytesChunk:
    __slots__ = ("_data", "_view")

    _data: bytes
    _view: memoryview

    def __init__(self, data: bytes):
        self._data = data
        self._view = memoryview(data)

    def get(self, max_size: int) -> bytes:
        """
        Get the data from the buffer with flow control.
        :param max_size: The maximum size of the data to get.
        :return: The data from the buffer.
        """
        if len(self._view) == 0:
            return b""
        if max_size > len(self._view):
            max_size = len(self._view)
        data = self._view[:max_size].tobytes()
        self._view = self._view[max_size:]

        if len(self._view) == 0:
            # If the view is empty, we can release the memory
            self.clear()

        return data

    def clear(self):
        """
        Clear the buffer.
        """
        self._data = b""
        self._view = memoryview(self._data)

    def __len__(self) -> int:
        return len(self._view)


class OutboundBytesChunk(BaseBytesChunk):
    """
    Outbound bytes chunk.
    """

    pass


class InboundBytesChunk(BaseBytesChunk):
    """
    Inbound bytes chunk.
    """

    __slots__ = ("_data", "_view", "_ack_size")

    _ack_size: int

    def __init__(self, data: bytes, ack_size: int):
        super().__init__(data)
        self._ack_size = ack_size

    @property
    def ack_size(self) -> int:
        """
        Get the ack size.
        :return: The ack size.
        """
        temp = self._ack_size
        self._ack_size = 0
        return temp


class StreamInboundBuffer:
    _ack_chunk: Optional[InboundBytesChunk]
    _buffer: queue.Queue[InboundBytesChunk]

    _eof: bool
    _has_data: anyio.Event

    def __init__(self):
        self._buffer = queue.Queue()
        self._eof = False
        self._has_data = anyio.Event()

    @property
    def eof(self) -> bool:
        """
        Check if the stream is EOF.
        :return: True if the stream is EOF, False otherwise.
        """
        return self._eof

    @eof.setter
    def eof(self, value: bool) -> None:
        """
        Set the EOF flag.
        :param value: The value to set.
        """
        self._eof = value
        if value:
            self._has_data.set()

    def put(self, data: bytes, ack_size: int) -> None:
        """
        Put the data into the buffer.
        :param data: The data to put into the buffer.
        :param ack_size: The ack size.
        """
        chunk = InboundBytesChunk(data, ack_size)
        self._buffer.put_nowait(chunk)
        self._has_data.set()

    async def get(self, max_size: int = -1) -> tuple[bytes, int]:
        """
        Get the data from the buffer.
        :param max_size: The maximum size of the data to get.
        :return: The data from the buffer and the ack size.
        """
        if self._ack_chunk is None and self._buffer.empty():
            if self._eof:
                # No more data can be read from the stream.
                return b"", 0
            # Wait for the data to be available
            self._has_data = anyio.Event()
            await self._has_data.wait()

        chunk = self._ack_chunk
        output = bytearray()
        remaining_size = max_size if max_size > 0 else sys.maxsize
        total_ack_size = 0

        while remaining_size > 0:
            # Get the data from the chunk
            data = chunk.get(remaining_size)
            output.extend(data)
            remaining_size -= len(data)
            total_ack_size += chunk.ack_size

            # Get next chunk from the buffer
            if len(chunk) == 0:
                try:
                    chunk = self._buffer.get_nowait()
                except queue.Empty:
                    # No more data in the buffer
                    chunk = None
                    break

        self._ack_chunk = chunk

        return bytes(output), total_ack_size


class AnyIOHttp2Stream(abc.ABC):
    __slots__ = (
        "_conn",
        "_id",
        "_local_exc",
        "_remote_exc",
        "_window_update",
        "_received_headers",
        "_received_trailers",
        "_received_data",
    )

    _conn: AnyIOHttp2Connection
    _id: int

    # some flags
    _local_exc: Optional[Exception]
    _remote_exc: Optional[Exception]
    _window_update: anyio.Event

    _received_headers: tuple[anyio.Event, Optional[list[tuple[str, str]]]]
    _received_trailers: tuple[anyio.Event, Optional[list[tuple[str, str]]]]
    _received_data: StreamInboundBuffer

    def __init__(self, conn, stream_id: int = -1):
        """
        Initialize the stream.
        :param conn: The connection object.
        :param stream_id: The stream ID. If -1, the stream ID will be assigned later.
        """
        self._conn = conn
        self._id = stream_id

        exc = (
            StreamException(self._id, "The stream has not been initialized. Please call `send_headers` first.")
            if stream_id == -1
            else None
        )
        self._local_exc = exc
        self._remote_exc = exc

        self._window_update = anyio.Event()
        self._received_headers = (anyio.Event(), None)
        self._received_trailers = (anyio.Event(), None)
        self._received_data = StreamInboundBuffer()

    @property
    def id(self) -> int:
        return self._id

    def _initialize(self, headers: list[tuple[str, str]], end_stream: bool) -> None:
        """
        Initialize the stream.
        Only the client side can initialize the stream.
        :param headers: The headers to send.
        :param end_stream: If True, the stream will be closed after sending the headers.
        """
        # Register the stream with the connection
        self._conn.register_stream(self)

        # Send the HEADERS frame
        self._conn.h2_state.send_headers(
            stream_id=self._id,
            headers=headers,
            end_stream=end_stream,
        )
        self._local_exc = None
        self._remote_exc = None

    async def send_headers(self, headers: list[tuple[str, str]], end_stream: bool = False) -> None:
        # If the stream ID is not set
        # we will apply for the stream ID and send HEADERS frame

        if self._id == -1:
            # Only the client side can initialize the stream
            fn = functools.partial(self._initialize, headers, end_stream)
        else:
            # Check the status of the stream
            if self._local_exc is not None:
                raise self._local_exc
            fn = functools.partial(self._conn.h2_state.send_headers, self._id, headers, end_stream)

        # send HEADERS/TRAILERS frame
        await self._conn.send(fn)

        if end_stream:
            self._local_exc = StreamClosedException(self._id, True)
            await self._try_unregister_stream()

    async def send_data(self, data: bytes, end_stream: bool = False) -> None:
        buffer = OutboundBytesChunk(data)

        def _send_by_flow_control(_buffer: OutboundBytesChunk, _end_stream: bool) -> None:
            h2_state = self._conn.h2_state
            window_size = h2_state.local_flow_control_window(self._id)
            data_block = _buffer.get(window_size)
            _end_stream = _end_stream and len(_buffer) == 0
            h2_state.send_data(
                stream_id=self._id,
                data=data_block,
                end_stream=_end_stream,
            )

        while True:
            if self._conn.h2_state.local_flow_control_window(self._id) == 0:
                #  wait for the window to be updated
                self._window_update = anyio.Event()
                await self._window_update.wait()

            # Check the status of the stream
            if self._local_exc is not None:
                raise self._local_exc

            # send by flow control
            await self._conn.send(_send_by_flow_control, buffer, end_stream)

            if len(buffer) == 0:
                break

        if end_stream:
            self._local_exc = StreamClosedException(self._id, True)
            await self._try_unregister_stream()

    async def rest_stream(self, err_code: int = 0) -> None:
        """
        Reset the stream.
        :param err_code: The error code to reset the stream.
        """
        if self._local_exc is not None:
            raise self._local_exc
        # Send the RST_STREAM frame
        await self._conn.send(self._conn.h2_state.reset_stream, self._id, err_code)
        # Handle the reset event
        await self._handle_reset(err_code, True)

    async def close(self) -> None:
        """
        Close the stream by sending an empty DATA frame.
        """
        if self._local_exc is not None:
            raise self._local_exc
        await self.send_data(b"", True)
        # Update the local exception to indicate that the stream is closed
        self._local_exc = StreamClosedException(self._id, True)
        await self._try_unregister_stream()

    async def handle_event(self, event: h2_events.Event) -> None:
        """
        Handle the incoming events from the H2 connection.
        :param event: The event to process.
        """
        if isinstance(event, (h2_events.RequestReceived, h2_events.ResponseReceived)):
            self._received_headers = event.headers
        elif isinstance(event, h2_events.TrailersReceived):
            self._received_trailers = event.headers
        elif isinstance(event, h2_events.DataReceived):
            self._received_data.put(event.data, event.flow_controlled_length)
        elif isinstance(event, h2_events.StreamReset):
            await self._handle_reset(event.error_code, False)
        elif isinstance(event, h2_events.WindowUpdated):
            self._window_update.set()

        if hasattr(event, "stream_ended") and event.stream_ended:
            self._received_data.eof = True
            self._remote_exc = StreamClosedException(self._id, False)
            await self._try_unregister_stream()

    async def _handle_reset(self, err_code: int, local_side) -> None:
        """
        Handle the stream reset event.
        """
        exc = StreamResetException(self._id, err_code, local_side)
        self._local_exc = exc
        self._remote_exc = exc
        self._received_data.eof = True
        self._window_update.set()
        await self._try_unregister_stream()

    async def _try_unregister_stream(self) -> None:
        if self._local_exc is not None and self._remote_exc is not None:
            # Both sides have closed the stream
            await self._conn.unregister_stream(self)

    async def _waiting_response(self, wait_event: anyio.Event) -> None:
        """
        Wait for the response.
        """
        if self._remote_exc is not None:
            raise self._remote_exc

        # Wait for the response
        try:
            await wait_event.wait()
        except anyio.ClosedResourceError:
            # The stream has been closed
            pass

        if self._remote_exc is not None:
            raise self._remote_exc

    async def receive_headers(self) -> list[tuple[str, str]]:
        """
        Receive the headers.
        :return: The headers.
        """
        event, headers = self._received_headers
        if headers is None:
            # No headers received yet
            await self._waiting_response(event)
            _, headers = self._received_headers

        return headers

    async def receive_trailers(self) -> list[tuple[str, str]]:
        """
        Receive the trailers.
        :return: The trailers.
        """
        event, trailers = self._received_trailers
        if trailers is None:
            # No trailers received yet
            await self._waiting_response(event)
            _, trailers = self._received_trailers
        return trailers

    async def receive_data(self, max_size: int = -1) -> bytes:
        """
        Receive the data.
        :param max_size: The maximum size of the data to receive.
        :return: The data.
        """
        data, ack_size = await self._received_data.get(max_size)

        if not data and self._received_data.eof and self._remote_exc is not None:
            # The stream has been closed
            raise self._remote_exc

        # acknowledge the received data
        if ack_size > 0:
            await self._conn.send(self._conn.h2_state.acknowledge_received_data, self._id, ack_size)

        return data
