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
import math
from collections.abc import Awaitable
from typing import Callable

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

__all__ = ["ReceivedData", "StreamDataBuffer"]


class ReceivedData:
    """A chunk of received data with associated ack size."""

    __slots__ = ("_data_view", "_ack_size")

    _data_view: memoryview
    _ack_size: int

    def __init__(self, data: bytes, ack_size: int):
        self._data_view = memoryview(data)
        self._ack_size = ack_size

    @property
    def ack_size(self) -> int:
        self._ack_size, size = 0, self._ack_size
        return size

    def is_empty(self) -> bool:
        return not self._data_view

    def get_data(self, max_bytes: int = -1) -> memoryview:
        available = len(self._data_view)
        to_consume = available if max_bytes < 0 else min(available, max_bytes)

        view = self._data_view[:to_consume]
        self._data_view = self._data_view[to_consume:]
        return view


class StreamDataBuffer:
    """
    A buffer for streaming received data with acknowledgement tracking.
    """

    __slots__ = ("_prev_data", "_buffer", "_ack_callback")

    _prev_data: ReceivedData
    _buffer: tuple[MemoryObjectSendStream[ReceivedData], MemoryObjectReceiveStream[ReceivedData]]

    _ack_callback: Callable[[int], Awaitable[None]]

    def __init__(self, ack_callback: Callable[[int], Awaitable[None]]) -> None:
        self._prev_data = ReceivedData(b"", 0)
        sender, receiver = anyio.create_memory_object_stream[ReceivedData](max_buffer_size=math.inf)
        self._buffer = (sender, receiver)
        self._ack_callback = ack_callback

    def get_unacked_size(self) -> int:
        """Get the total size of unacknowledged data in the buffer."""
        receiver = self._buffer[1]
        unacked_size = 0
        try:
            while True:
                item = receiver.receive_nowait()
                unacked_size += item.ack_size
        except (anyio.ClosedResourceError, anyio.WouldBlock, anyio.EndOfStream):
            pass
        return unacked_size

    def close(self) -> None:
        """Close the buffer."""
        sender = self._buffer[0]
        sender.close()

    async def aclose(self) -> None:
        """Asynchronously close the buffer."""
        sender = self._buffer[0]
        await sender.aclose()

    def append_nowait(self, data: bytes, ack_size: int = 0) -> None:
        """Append new data to the buffer without waiting."""
        if not data:
            return
        new_data = ReceivedData(data, ack_size)
        try:
            self._buffer[0].send_nowait(new_data)
        except (anyio.ClosedResourceError, anyio.WouldBlock, anyio.EndOfStream):
            raise RuntimeError("Buffer closed or full while appending data")

    async def get_data(self, max_bytes: int = -1) -> bytes:
        """Get data from the buffer, waiting if necessary."""
        chunks: list[memoryview] = []
        curr_bytes = 0
        total_ack_size = 0

        while True:
            # get data from the previous item if available
            if not self._prev_data.is_empty():
                view = self._prev_data.get_data(max_bytes - curr_bytes if max_bytes > 0 else -1)
                chunks.append(view)
                curr_bytes += len(view)
                total_ack_size += self._prev_data.ack_size

            # if we have enough data, return it
            if 0 <= max_bytes <= curr_bytes:
                break

            # try to get more data from the buffer
            try:
                if curr_bytes > 0:
                    try:
                        # We have received some, so we can try to get more without blocking
                        self._prev_data = self._buffer[1].receive_nowait()
                    except anyio.WouldBlock:
                        break
                else:
                    # block until new data arrives
                    self._prev_data = await self._buffer[1].receive()
            except (anyio.ClosedResourceError, anyio.EndOfStream):
                raise RuntimeError("Buffer closed while waiting for data")

        if total_ack_size > 0:
            await self._ack_callback(total_ack_size)

        return b"".join(chunks)

    async def get_data_exactly(self, exact_bytes: int) -> bytes:
        chunks: list[memoryview] = []
        curr_bytes = 0
        total_ack_size = 0

        while True:
            # get data from the previous item if available
            if not self._prev_data.is_empty():
                view = self._prev_data.get_data(exact_bytes - curr_bytes)
                chunks.append(view)
                curr_bytes += len(view)
                total_ack_size += self._prev_data.ack_size

            if curr_bytes >= exact_bytes:
                break

            # try to get more data from the buffer
            try:
                if total_ack_size > 0:
                    # send any unacknowledged data if we have some
                    await self._ack_callback(total_ack_size)
                    total_ack_size = 0

                self._prev_data = await self._buffer[1].receive()
            except (anyio.ClosedResourceError, anyio.EndOfStream) as e:
                raise RuntimeError("Buffer closed while waiting for data") from e

        if total_ack_size > 0:
            await self._ack_callback(total_ack_size)

        return b"".join(chunks)
