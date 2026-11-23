# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""What the asyncio streams of the HTTP versions share: the guard of each direction, the body a stream receives, and the
turns of the tasks waiting to open a stream."""

import asyncio
from collections import deque
from collections.abc import Callable

__all__ = ["BufferedReceive", "OperationGuard", "StreamAdmission"]


class OperationGuard:
    """Rejects overlapping operations in one direction instead of hiding them behind a lock.

    Args:
        overlap: Makes the error an overlapping operation raises.
    """

    __slots__ = ("_busy", "_overlap")

    def __init__(self, overlap: Callable[[], Exception]) -> None:
        self._overlap = overlap
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def __enter__(self) -> None:
        if self._busy:
            raise self._overlap()
        self._busy = True

    def __exit__(self, *exc_info: object) -> None:
        self._busy = False


class StreamAdmission:
    """Lets the tasks waiting to open a stream on one connection open one in arrival order, as streams become free.

    A waiter told it may open one holds a stream set aside for it, a grant,
    until it opens the stream or gives up.

    Args:
        available: Returns how many more streams the connection may open now.
        accepting: Returns whether the connection opens streams at all.
    """

    def __init__(self, available: Callable[[], int], accepting: Callable[[], bool]) -> None:
        self._available = available
        self._accepting = accepting
        self._waiters: deque[asyncio.Future[bool]] = deque()
        self.granted = 0

    @property
    def free(self) -> int:
        """How many more streams may open, besides those set aside."""
        return self._available() - self.granted

    def ready(self, *, first: bool) -> bool:
        """Return whether a task may open a stream now; ``first`` for one that waited, ahead of the others."""
        if first:
            # The waiters told after this one wake after it, so the streams set aside for them come second.
            return self._available() > 0
        # Nobody passes those already waiting.
        return not self._waiters and self.free > 0

    async def wait(self, *, first: bool) -> bool:
        """Wait for a turn, ahead of the others when ``first``; return ``False`` once the connection opens no more."""
        waiter = asyncio.get_running_loop().create_future()
        if first:
            self._waiters.appendleft(waiter)
        else:
            self._waiters.append(waiter)
        try:
            allowed = await waiter
        except BaseException:
            if not waiter.done() or waiter.cancelled():
                waiter.cancel()
                # A cancelled waiter may already have been passed over.
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            elif waiter.result():
                # The stream set aside goes to the next waiter.
                self.granted -= 1
                self.notify()
            raise

        if allowed:
            self.granted -= 1
        return allowed

    def notify(self) -> None:
        """Set aside the free streams for the waiters in arrival order, or tell them all the connection opens no more."""
        waiters = self._waiters
        if not self._accepting():
            while waiters:
                waiter = waiters.popleft()
                if not waiter.done():
                    waiter.set_result(False)
            return

        while waiters and self.free > 0:
            waiter = waiters.popleft()
            # A cancelled waiter is passed over; it takes nothing.
            if not waiter.done():
                waiter.set_result(True)
                self.granted += 1


class BufferedReceive:
    """The body a stream receives, queued as it arrives and read in order, its flow-control credit returned as read.

    The stream sets the attributes below, calls `_on_data` as body arrives,
    and provides the checks, the waking of its receiver and the return of
    credit.

    An exact receive that is cancelled puts the bytes it collected back at
    the front, without returning their credit again.
    """

    _receive_guard: OperationGuard
    _inbound_chunks: deque[bytes]
    _chunk_offset: int
    # Bytes at the front of the queue whose credit has already been returned.
    _credited: int
    _receive_ended: bool
    _receive_waiter: "asyncio.Future[None] | None"

    async def receive_data(self, max_bytes: int = 64 * 1024) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        with self._receive_guard:
            self._check_receivable()
            while not self._inbound_chunks:
                self._raise_if_receive_failed()
                if self._receive_ended:
                    return b""
                await self._wait_to_receive()

            return self._take_data(max_bytes)

    async def receive_data_exactly(self, nbytes: int) -> bytes:
        if nbytes < 0:
            raise ValueError("nbytes must not be negative")

        with self._receive_guard:
            self._check_receivable()
            chunks = self._inbound_chunks
            # Served from the first buffered chunk alone when it is long enough.
            if nbytes == 0 or (chunks and len(chunks[0]) - self._chunk_offset >= nbytes):
                return self._take_data(nbytes) if nbytes else b""

            parts: list[bytes] = []
            remaining = nbytes
            try:
                while remaining:
                    # An ended body returns what arrived, which may be fewer than nbytes.
                    while not chunks:
                        self._raise_if_receive_failed()
                        if self._receive_ended:
                            return b"".join(parts)
                        await self._wait_to_receive()

                    part = self._take_data(remaining)
                    parts.append(part)
                    remaining -= len(part)
            except asyncio.CancelledError:
                # The bytes taken so far go back to the buffer, so a cancelled read loses none.
                if parts:
                    self._put_back(b"".join(parts))
                raise

            return b"".join(parts)

    def _check_receivable(self) -> None:
        """Raise when the role allows no body or trailers to be received yet."""

    def _raise_if_receive_failed(self) -> None:
        raise NotImplementedError

    def _consume(self, amount: int) -> None:
        """Return the flow-control credit of ``amount`` bytes read or dropped."""
        raise NotImplementedError

    def _wake_receiver(self) -> None:
        raise NotImplementedError

    def _on_data(self, data: bytes, *, end_stream: bool) -> None:
        if data:
            self._inbound_chunks.append(data)
        self._receive_ended = self._receive_ended or end_stream
        self._wake_receiver()

    async def _wait_to_receive(self) -> None:
        waiter = self._receive_waiter = asyncio.get_running_loop().create_future()
        await waiter

    def _take_data(self, max_bytes: int) -> bytes:
        """Take at most max_bytes without copying a whole queued chunk, returning their flow-control credit."""
        chunk = self._inbound_chunks[0]
        available = len(chunk) - self._chunk_offset
        if available <= max_bytes:
            self._inbound_chunks.popleft()
            data = chunk if self._chunk_offset == 0 else chunk[self._chunk_offset :]
            self._chunk_offset = 0
        else:
            end = self._chunk_offset + max_bytes
            data = chunk[self._chunk_offset : end]
            self._chunk_offset = end

        # Credit follows application consumption, not socket reception.
        credit = len(data)
        if self._credited:
            returned = min(self._credited, credit)
            self._credited -= returned
            credit -= returned
        self._consume(credit)
        return data

    def _put_back(self, data: bytes) -> None:
        """Queue ``data`` taken from the front of the input in front again, its credit kept returned."""
        chunks = self._inbound_chunks
        if self._chunk_offset:
            chunks[0] = chunks[0][self._chunk_offset :]
            self._chunk_offset = 0
        chunks.appendleft(data)
        self._credited += len(data)

    def _discard_unread(self) -> None:
        """Discard the body received but not read, returning its flow-control credit, so that the peer sends on."""
        unread = self._drop_unread()
        if unread:
            self._consume(unread)

    def _drop_unread(self) -> int:
        """Drop queued input and return how many bytes were never read."""
        amount = sum(map(len, self._inbound_chunks)) - self._chunk_offset - self._credited
        self._inbound_chunks.clear()
        self._chunk_offset = 0
        self._credited = 0
        return amount
