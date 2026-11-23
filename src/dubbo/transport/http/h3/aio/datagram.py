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

import asyncio
import socket
from collections import deque
from typing import TYPE_CHECKING

from ngquic import Transmit
from ngquic.udp import UDPSocket

if TYPE_CHECKING:
    from .endpoint import AioQuicEndpoint

__all__ = ["BatchedSocket"]

# Batched reads at most each time the socket is ready, each of ngquic's default number of buffers, so that the
# other callbacks of the loop get their turn.
_READS = 8
# Bytes queued that pause the endpoint, and below which it resumes.
_WRITE_HIGH = 64 * 1024
_WRITE_LOW = 16 * 1024


class BatchedSocket(asyncio.DatagramTransport):
    """The UDP socket of an endpoint, read and written by ngquic's batched system calls on the loop's readiness.

    Each time the socket is ready, every datagram waiting, up to a bound,
    goes straight to the endpoint's engine, and the endpoint is handed the
    connections and attempts they reached. A transmit goes out as one
    segmented send where the system offers GSO.

    Raises:
        NotImplementedError: The loop has no ``add_reader``.
        OSError: The system refuses a socket option ngquic needs.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, sock: socket.socket, endpoint: "AioQuicEndpoint") -> None:
        try:
            peer = sock.getpeername()
        except OSError:
            peer = None
        super().__init__({"socket": sock, "sockname": sock.getsockname(), "peername": peer})
        self._loop = loop
        self._sock = sock
        self._udp = UDPSocket(sock)
        self._endpoint = endpoint
        # Transmits the socket refused for now, to send in order once it is writable.
        self._queue: deque[Transmit] = deque()
        # Bytes of the queue not sent yet.
        self._queued = 0
        self._paused = False
        self._closing = False
        loop.add_reader(sock, self._read_ready)

    @property
    def gso(self) -> bool:
        """Whether one transmit may carry several datagrams for the system to split."""
        return self._udp.gso

    def is_closing(self) -> bool:
        return self._closing

    def get_write_buffer_size(self) -> int:
        return self._queued

    def send(self, transmit: Transmit) -> None:
        """Send ``transmit``, or queue what the socket refuses for now after those waiting."""
        if self._closing:
            return

        if not self._queue:
            if self._send(transmit):
                return
            self._loop.add_writer(self._sock, self._write_ready)
        self._queue.append(transmit)
        self._queued += len(transmit.data)
        if not self._paused and self._queued > _WRITE_HIGH:
            self._paused = True
            self._endpoint.pause_writing()

    def close(self) -> None:
        self.abort()

    def abort(self) -> None:
        if self._closing:
            return

        self._closing = True
        self._loop.remove_reader(self._sock)
        if self._queue:
            self._loop.remove_writer(self._sock)
            self._queue.clear()
            self._queued = 0
        self._loop.call_soon(self._lost)

    def _lost(self) -> None:
        try:
            self._endpoint.connection_lost(None)
        finally:
            self._sock.close()

    def _read_ready(self) -> None:
        endpoint = self._endpoint
        engine = endpoint.engine
        assert engine is not None
        for _ in range(_READS):
            if self._closing:
                return
            try:
                reached = self._udp.receive_into(engine, endpoint.now())
            except OSError as error:
                # An ICMP error of a connected socket, such as port unreachable, which the next read reports.
                endpoint.error_received(error)
                return
            # Datagrams that reach nothing may still call for a stateless reply.
            endpoint.reached(reached)
            if not reached:
                return

    def _write_ready(self) -> None:
        """Send the queued transmits in order until the socket refuses more for now."""
        queue = self._queue
        while queue:
            transmit = queue[0]
            if not self._send(transmit):
                return
            queue.popleft()
            self._queued -= len(transmit.data)
        self._loop.remove_writer(self._sock)
        if self._paused and self._queued <= _WRITE_LOW:
            self._paused = False
            self._endpoint.resume_writing()

    def _send(self, transmit: Transmit) -> bool:
        """Send ``transmit``; return False if the socket refused it for now, having sent none of it.

        The socket drops the datagrams after one it fails to send, and a failure on the first drops them all, as QUIC
        recovers from their loss.
        """
        try:
            self._udp.send(transmit)
        except BlockingIOError:
            return False
        except OSError as error:
            self._endpoint.error_received(error)
        return True
