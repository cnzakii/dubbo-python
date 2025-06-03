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

import collections
import threading
from typing import Callable, Optional

from ._tracker import Deadline

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

    __slots__ = ("_ack_callback", "_deque", "_is_empty", "_closed")

    _ack_callback: Callable[[int], None]

    _deque: collections.deque[ReceivedData]
    _is_empty: threading.Event
    _closed: bool

    def __init__(self, ack_callback: Callable[[int], None]):
        self._ack_callback = ack_callback

        self._deque = collections.deque()
        self._is_empty = threading.Event()
        self._closed = False

    def _check_closed(self):
        if self._closed:
            raise RuntimeError("Cannot operate on closed stream")

    def close(self) -> None:
        """Close the buffer"""
        if not self._closed:
            self._closed = True
            self._is_empty.set()

    def get_unacked_size(self) -> int:
        """Get the total size of unacknowledged data in the buffer."""
        return sum(item.ack_size for item in self._deque)

    def append(self, data: bytes, ack_size: int = 0) -> None:
        """Append new data to the buffer."""
        self._check_closed()
        self._deque.append(ReceivedData(data, ack_size))
        self._is_empty.set()

    def get_data(self, max_bytes: int = -1, timeout: Optional[float] = None) -> bytes:
        """Get data from the buffer, waiting if necessary."""
        chunks: list[memoryview] = []
        curr_bytes = 0
        deadline = Deadline(timeout)

        while True:
            while self._deque:
                item = self._deque.popleft()
                if item.is_empty():
                    continue
                data = item.get_data(max_bytes if max_bytes < 0 else max_bytes - curr_bytes)
                curr_bytes += len(data)
                chunks.append(data)
                self._ack_callback(item.ack_size)

                if 0 <= max_bytes <= curr_bytes:
                    self._deque.appendleft(item)

                    return b"".join(chunks)

            if curr_bytes > 0:
                return b"".join(chunks)

            # wait
            self._check_closed()
            self._is_empty.clear()
            self._is_empty.wait(timeout=deadline.remaining())

    def get_data_exactly(self, exact_bytes: int, timeout: Optional[float] = None) -> bytes:
        """Get exactly `size` bytes from the buffer, waiting if necessary."""
        chunks: list[memoryview] = []
        curr_bytes = 0
        deadline = Deadline(timeout)

        while True:
            while self._deque:
                item = self._deque.popleft()
                if item.is_empty():
                    continue
                data = item.get_data(exact_bytes - curr_bytes)
                curr_bytes += len(data)
                chunks.append(data)
                self._ack_callback(item.ack_size)

                if curr_bytes >= exact_bytes:
                    self._deque.appendleft(item)
                    return b"".join(chunks)
            # wait
            self._check_closed()
            self._is_empty.clear()
            self._is_empty.wait(deadline.remaining())
