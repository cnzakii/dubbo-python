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
import errno
import logging
import socket
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

import anyio
import ngquic
from ngquic import h3

if sys.platform != "win32":
    from .datagram import BatchedSocket

if TYPE_CHECKING:
    from .connection import AioH3Connection
    from .settings import H3ConnectionConfig

__all__ = ["AioQuicEndpoint", "open_endpoint"]

_logger = logging.getLogger(__name__)

# Bytes asked of the kernel for each socket buffer; a busy connection outruns the defaults of some systems.
_SOCKET_BUFFER_SIZE = 4 * 1024 * 1024
# The errors a connected socket reports for an ICMP unreachable message; Windows reports port unreachable as a reset.
_UNREACHABLE = frozenset({errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN})
# The same as Windows error codes, which an overlapped read reports: ERROR_CONNECTION_REFUSED,
# ERROR_NETWORK_UNREACHABLE, ERROR_HOST_UNREACHABLE and ERROR_PORT_UNREACHABLE.
_WINDOWS_UNREACHABLE = frozenset({1225, 1231, 1232, 1234})


class AioQuicEndpoint(asyncio.DatagramProtocol):
    """Runs one ngquic endpoint over a UDP socket that `open_endpoint` opens.

    A server's endpoint serves every connection on its listening socket. A
    client connection has an endpoint of its own, on a socket connected to
    the server address it tries, which closes once the connection finishes.

    asyncio invokes every callback synchronously on one event loop, so engine
    state needs no mutex. Datagrams are fed to the engine as they arrive;
    the connections they touch, and those whose streams queued output, are
    processed once at the end of the loop iteration, so that their output
    and acknowledgements share one pass. Each connection keeps one timer for
    the engine's next deadline.

    The engine reads the clock of the event loop where it is exact, and
    `time.monotonic` elsewhere: uvloop reads its clock once per iteration,
    in whole milliseconds.
    """

    def __init__(
        self, *, on_incoming: Callable[[ngquic.Incoming], None] | None = None, close_when_empty: bool = False
    ) -> None:
        self.engine: ngquic.Endpoint | None = None
        self.transport: asyncio.DatagramTransport | None = None
        # The transport, when it is ngquic's batched socket.
        self._socket: BatchedSocket | None = None
        self.local_address: tuple[Any, ...] | None = None
        self.write_paused = False
        self._on_incoming = on_incoming
        # Whether the socket belongs to the connections on it, and closes once the last finishes.
        self._close_when_empty = close_when_empty
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clock: Callable[[], float] = time.monotonic
        # Whether the socket is connected to one peer, so that sends name no address.
        self._connected = False
        # Keyed by the ngquic connection, until its engine has no state left.
        self._connections: dict[object, AioH3Connection] = {}
        # Connections to process at the end of this loop iteration, in arrival order.
        self._pending: dict[AioH3Connection, None] = {}
        self._flush_scheduled = False
        self._socket_lost = anyio.Event()

    @property
    def closed(self) -> bool:
        return self._socket_lost.is_set()

    @property
    def gso(self) -> bool:
        """Whether one transmit may carry several datagrams for the system to split."""
        return self._socket is not None and self._socket.gso

    def now(self) -> float:
        """Return the time the engine runs on, in seconds."""
        return self._clock()

    def call_at(self, when: float, callback: Callable[[], None]) -> asyncio.TimerHandle:
        """Run ``callback`` once the engine clock reaches ``when``, or shortly before."""
        assert self._loop is not None
        return self._loop.call_later(max(0.0, when - self._clock()), callback)

    def connect(self, settings: "H3ConnectionConfig") -> h3.H3Connection:
        """Start a client connection to the peer the socket is connected to."""
        assert self.engine is not None
        assert self.transport is not None
        return self.engine.connect(
            self.transport.get_extra_info("peername"),
            self.now(),
            tls=settings.tls,
            server_name=settings.server_name,
            transport=settings.transport,
            h3=settings.h3,
        )

    def add(self, connection: "AioH3Connection") -> None:
        """Route the datagrams of ``connection`` to it and process it soon."""
        self._connections[connection.engine] = connection
        self.schedule(connection)

    def remove(self, connection: "AioH3Connection") -> None:
        """Stop routing to ``connection``, whose engine holds no state any more."""
        self._connections.pop(connection.engine, None)
        self._pending.pop(connection, None)
        if self._close_when_empty and not self._connections:
            self.close()

    def schedule(self, connection: "AioH3Connection") -> None:
        """Process ``connection`` at the end of this loop iteration."""
        self._pending[connection] = None
        if not self._flush_scheduled and self._loop is not None:
            self._flush_scheduled = True
            self._loop.call_soon(self._flush)

    def send(self, transmit: ngquic.Transmit) -> None:
        if self._socket is not None:
            self._socket.send(transmit)
            return
        transport = self.transport
        if transport is None or transport.is_closing():
            return
        transport.sendto(transmit.data, None if self._connected else transmit.remote_addr)

    def close(self) -> None:
        """Close the socket; the connections still on it are abandoned."""
        if self.transport is not None:
            # Before Python 3.13.16 and 3.14.9, a proactor event loop never reports a datagram transport lost
            # that was closed with a send under way, python/cpython#156920.
            self.transport.abort()
        elif not self.closed:
            self.connection_lost(None)

    async def wait_closed(self) -> None:
        await self._socket_lost.wait()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast(asyncio.DatagramTransport, transport)
        if sys.platform != "win32" and isinstance(transport, BatchedSocket):
            self._socket = transport
        self._loop = loop = asyncio.get_running_loop()
        # The standard loops read time.monotonic, and a loop that redefines its clock, as a test does, means it.
        self._clock = loop.time if isinstance(loop, asyncio.BaseEventLoop) else time.monotonic
        self._connected = transport.get_extra_info("peername") is not None
        self.local_address = transport.get_extra_info("sockname")
        self.engine = ngquic.Endpoint(self.local_address)

    def datagram_received(self, data: bytes, addr: tuple[Any, ...]) -> None:
        engine = self.engine
        if engine is None:
            return

        try:
            result = engine.receive_datagram(data, addr, self._clock())
        except ngquic.QUICError:
            _logger.exception("Failed to process a QUIC datagram from %s", addr)
            return
        if result is not None:
            self._reach(result)
        # Stateless replies, such as a refusal, go out with the next flush.
        self._schedule_endpoint()

    def reached(self, results: Iterable[ngquic.BaseConnection | ngquic.Incoming]) -> None:
        """Handle the connections and attempts that datagrams the engine took in a batch reached."""
        for result in results:
            self._reach(result)
        # Stateless replies, such as a refusal, go out with the next flush.
        self._schedule_endpoint()

    def error_received(self, exc: Exception) -> None:
        # An ICMP error, such as port unreachable, reaches only a connected socket, so it concerns its one peer.
        # Other errors of a send, such as a full buffer, leave the handshake to its retransmissions.
        if self._connected and (
            getattr(exc, "errno", None) in _UNREACHABLE or getattr(exc, "winerror", None) in _WINDOWS_UNREACHABLE
        ):
            for connection in tuple(self._connections.values()):
                connection.network_error(exc)

    def pause_writing(self) -> None:
        self.write_paused = True

    def resume_writing(self) -> None:
        self.write_paused = False
        for connection in tuple(self._connections.values()):
            connection.writable()
            self.schedule(connection)

    def connection_lost(self, exc: Exception | None) -> None:
        if self.closed:
            return
        self._socket_lost.set()
        for connection in tuple(self._connections.values()):
            connection.socket_lost(exc)
        self._connections.clear()
        self._pending.clear()

    def _reach(self, result: ngquic.BaseConnection | ngquic.Incoming) -> None:
        if isinstance(result, ngquic.Incoming):
            if self._on_incoming is None:
                result.ignore()
            else:
                self._on_incoming(result)
        else:
            connection = self._connections.get(result)
            if connection is not None:
                self.schedule(connection)

    def _schedule_endpoint(self) -> None:
        if not self._flush_scheduled and self._loop is not None:
            self._flush_scheduled = True
            self._loop.call_soon(self._flush)

    def _flush(self) -> None:
        """End the loop iteration's batch: send stateless replies, then process each pending connection."""
        self._flush_scheduled = False
        engine = self.engine
        if engine is not None and not self.write_paused:
            for transmit in engine.datagrams_to_send():
                self.send(transmit)

        # What processing schedules again waits for the next iteration.
        pending, self._pending = self._pending, {}
        for connection in pending:
            if self.closed:
                break
            connection.process()


async def open_endpoint(
    endpoint: AioQuicEndpoint,
    *,
    local: tuple[str, int] | None = None,
    remote: tuple[Any, ...] | None = None,
    family: int = socket.AF_UNSPEC,
) -> None:
    """Open the UDP socket of ``endpoint``: bound to the host and port ``local``, or connected to the address ``remote``.

    Where the loop has ``add_reader``, the socket is read and written by
    ngquic's batched system calls on its readiness. On Windows, it goes
    through the loop's datagram transport.

    Raises:
        OSError: No address of ``local`` can be bound, or ``remote`` cannot be connected to.
    """
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        if remote is not None:
            sock = _connected(remote, family)
        else:
            assert local is not None
            sock = await _bound(loop, *local)
        try:
            _set_buffer_sizes(sock)
            # Reads start on the loop's next turn, once the endpoint holds the transport.
            endpoint.connection_made(BatchedSocket(loop, sock, endpoint))
            return
        except BaseException as error:
            sock.close()
            if not isinstance(error, NotImplementedError):
                raise
    # The loop's own transport sends on a connected socket only when it connected the socket itself.
    if remote is not None:
        # The loop takes a host and a port alone, so an IPv6 scope goes in the host.
        host = f"{remote[0]}%{remote[3]}" if len(remote) == 4 and remote[3] else remote[0]
        transport, _ = await loop.create_datagram_endpoint(
            lambda: endpoint, remote_addr=(host, remote[1]), family=family
        )
    else:
        assert local is not None
        transport, _ = await loop.create_datagram_endpoint(lambda: endpoint, sock=await _bound(loop, *local))
    _set_buffer_sizes(transport.get_extra_info("socket"))


def _connected(remote: tuple[Any, ...], family: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        sock.setblocking(False)
        sock.connect(remote)
    except BaseException:
        sock.close()
        raise
    return sock


def _set_buffer_sizes(sock: Any) -> None:
    for option in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        # A system that caps the size keeps its own.
        with suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, option, _SOCKET_BUFFER_SIZE)


async def _bound(loop: asyncio.AbstractEventLoop, host: str, port: int) -> socket.socket:
    """Return a UDP socket bound to the first address of ``host`` and ``port`` that takes one, as asyncio binds."""
    error: OSError | None = None
    for family, kind, proto, _, address in await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM):
        sock = socket.socket(family, kind, proto)
        try:
            sock.setblocking(False)
            sock.bind(address)
        except OSError as failure:
            sock.close()
            error = failure
            continue
        return sock
    raise error or OSError(f"{host} resolved to no address")
