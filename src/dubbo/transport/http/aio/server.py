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
import math
import typing
from contextlib import suppress
from types import TracebackType
from typing import TYPE_CHECKING, Self, cast

import anyio
from anyio.abc import TaskGroup

from dubbo.constants import DEFAULT_SHUTWAIT, HTTP3_KEY, SHUTWAIT_KEY
from dubbo.transport.http.alt_svc import AltService, format_alt_svc
from dubbo.transport.http.base import HttpServer
from dubbo.transport.http.h1.aio.connection import AioH1Connection
from dubbo.transport.http.h1.aio.server import AioH1Connections
from dubbo.transport.http.h1.base import H1ExchangeHandler
from dubbo.transport.http.h2.aio.connection import AioH2Connection
from dubbo.transport.http.h2.aio.server import AioH2Connections
from dubbo.transport.http.h2.base import H2StreamHandler
from dubbo.transport.http.h3.base import H3StreamHandler
from dubbo.transport.listener import abort_handshakes, create_listener
from dubbo.transport.tls import get_ssl_context, ssl_handshake_timeout
from dubbo.url import URL
from dubbo.utils import join_shielded, release_shield, wait_then_cancel
from dubbo.utils.converters import milliseconds

if TYPE_CHECKING:
    from dubbo.transport.http.h3.aio.server import AioH3Connections

__all__ = ["AioHttpServer"]

_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
# Tries at an ephemeral port that is free for TCP but taken for UDP.
_BIND_ATTEMPTS = 8
# The errors of a UDP port that is taken, or that Windows reserves (an excluded port range, reported as forbidden).
_PORT_TAKEN = frozenset({errno.EADDRINUSE, errno.EACCES})
_H3_PREFIX = f"{HTTP3_KEY}."


class _Part(typing.Protocol):
    """The connections of one HTTP version and the handler tasks they run, as the server drives them."""

    closing: bool
    handlers: set[asyncio.Task[None]]

    def announce_shutdown(self) -> None: ...

    async def aclose(self) -> None: ...

    def abort(self) -> None: ...

    async def wait_closed(self) -> None: ...


class AioHttpServer(HttpServer):
    """Serves HTTP/1.1, HTTP/2 and HTTP/3 through asyncio.

    Entering the server starts listening; it is single-use and owns its
    listeners, connections, stream contexts and handler tasks, all of which
    must stay on the event loop it was entered on. ``url`` names the
    interface and port to bind, with the parameters `HttpServer` describes,
    plus ``stream-send-buffer-size`` as `AioH2Client` and `AioH3Client`
    describe it.
    """

    def __init__(
        self,
        url: URL,
        *,
        h1: H1ExchangeHandler | None = None,
        h2: H2StreamHandler | None = None,
        h3: H3StreamHandler | None = None,
    ) -> None:
        super().__init__(url, h1=h1, h2=h2, h3=h3)
        if url.host is None or url.port is None:
            raise ValueError(f"an HTTP server needs a host and a port: {url}")

        self._host = url.host
        self._port = url.port
        self._shutwait = url.get_parameter(SHUTWAIT_KEY, milliseconds, DEFAULT_SHUTWAIT)

        # TLS offers HTTP/2 first.
        offered: list[str] = []
        if h2 is not None:
            offered.append("h2")
        if h1 is not None:
            offered.append("http/1.1")
        self._ssl_context = get_ssl_context(url, server=True, alpn=offered) if offered else None
        self._ssl_handshake_timeout = ssl_handshake_timeout(url)

        self._h1_connections = None if h1 is None else AioH1Connections(url, h1)
        self._h2_connections: AioH2Connections | None = None
        if h2 is not None:
            # Served alone over TLS, HTTP/2 is reached through ALPN only.
            require_h2_alpn = h1 is None and self._ssl_context is not None
            self._h2_connections = AioH2Connections(url, h2, require_h2_alpn=require_h2_alpn)
        self._h3_connections: AioH3Connections | None = None
        if h3 is not None:
            # Imported only here, as HTTP/3 has an optional dependency.
            from dubbo.transport.http.h3.aio.server import AioH3Connections as Connections

            self._h3_connections = Connections(_h3_url(url), h3)
        parts = (self._h1_connections, self._h2_connections, self._h3_connections)
        self._parts: tuple[_Part, ...] = tuple(part for part in parts if part is not None)

        self._listener: asyncio.Server | None = None
        self._address: tuple[str, int] | None = None
        # TCP connections that have not yet shown which version they speak.
        self._detecting: set[_Detector] = set()
        self._started = False
        self._closing = False
        self._shutdown: asyncio.Task[None] | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._address is None or self._closing:
            raise RuntimeError("the HTTP server is not listening")
        return self._address

    def announce_shutdown(self) -> None:
        for part in self._parts:
            part.announce_shutdown()

    async def aclose(self) -> None:
        if self._shutdown is None:
            self._closing = True
            for part in self._parts:
                part.closing = True
            self._shutdown = asyncio.get_running_loop().create_task(self._shut_down())

        shutdown = self._shutdown
        try:
            await asyncio.wait((shutdown,))
        except BaseException:
            # A cancelled close shuts down at once and waits only for that.
            shutdown.cancel()
            with suppress(asyncio.CancelledError):
                await join_shielded(shutdown)
            raise

        # A shutdown cancelled by an earlier close has finished at once.
        if not shutdown.cancelled():
            shutdown.result()

    async def __aenter__(self) -> Self:
        if self._started or self._closing:
            raise RuntimeError("the HTTP server can only be started once")

        self._started = True

        try:
            await self._bind()
            if self._closing:
                raise RuntimeError("the HTTP server closed while it started")
            self._advertise_h3()
            if self._listener is not None:
                await self._listener.start_serving()
        except BaseException:
            # No one owns the server until it has started, so a start that fails releases what it bound.
            self._closing = True
            await self._release_bound()
            raise
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        # Leaving under a cancellation stops at once, whether or not the cancellation is still delivered.
        if exc_type is not None and issubclass(exc_type, anyio.get_cancelled_exc_class()):
            with anyio.CancelScope() as scope:
                scope.cancel()
                await self.aclose()
            return

        await self.aclose()

    async def _bind(self) -> None:
        """Bind the TCP listener, then UDP at each of its addresses and its port, retrying an ephemeral port taken for UDP."""
        h3 = self._h3_connections
        if self._h1_connections is None and self._h2_connections is None:
            assert h3 is not None
            local = await h3.bind(self._host, self._port)
            self._address = (local[0], local[1])
            return

        attempts = _BIND_ATTEMPTS if self._port == 0 else 1
        for attempt in range(1, attempts + 1):
            # Accepting waits until the server owns the listener.
            listener, address = await create_listener(
                self._create_connection, self._host, self._port, self._ssl_context, self._ssl_handshake_timeout
            )
            self._listener, self._address = listener, address
            if h3 is None:
                return

            try:
                for sock in listener.sockets:
                    await h3.bind(sock.getsockname()[0], address[1])
                return
            except OSError as error:
                await self._release_bound()
                if error.errno not in _PORT_TAKEN or attempt == attempts:
                    raise

    def _advertise_h3(self) -> None:
        """Have the HTTP/1.1 and HTTP/2 responses name the HTTP/3 port, RFC 7838."""
        if self._h3_connections is None or self._address is None:
            return

        alt_svc = format_alt_svc((AltService(protocol="h3", port=self._address[1]),))
        if self._h1_connections is not None:
            self._h1_connections.alt_svc = alt_svc
        if self._h2_connections is not None:
            self._h2_connections.alt_svc = alt_svc

    async def _release_bound(self) -> None:
        listener, self._listener, self._address = self._listener, None, None
        if listener is not None:
            listener.close()
            with release_shield():
                await listener.wait_closed()
        if self._h3_connections is not None:
            await self._h3_connections.close_sockets()

    def _create_connection(self) -> asyncio.BaseProtocol:
        if self._h1_connections is None:
            assert self._h2_connections is not None
            return self._h2_connections.create_connection()
        if self._h2_connections is None:
            return self._h1_connections.create_connection()
        return _Detector(self._detecting, self._h1_connections, self._h2_connections)

    def _handlers(self) -> set[asyncio.Task[None]]:
        return {task for part in self._parts for task in part.handlers}

    async def _shut_down(self) -> None:
        try:
            await self._shut_down_gracefully()
        except BaseException:
            # Cancelled: what is still open closes at once.
            await self._abort()
            raise
        finally:
            if self._h3_connections is not None:
                await self._h3_connections.close_sockets()

    async def _shut_down_gracefully(self) -> None:
        """Stop accepting, let the handlers finish within the shutwait, then close the connections."""
        listener = self._listener
        async with anyio.create_task_group() as task_group:
            if listener is not None:
                await _stop_listening(listener, task_group)

            # Connections still choosing their version carry no request.
            for detector in tuple(self._detecting):
                detector.close()
            for part in self._parts:
                part.announce_shutdown()

            await wait_then_cancel(self._handlers(), self._shutwait)

            # Each closes once its queued responses are written, or aborts after a grace period.
            async with anyio.create_task_group() as closing:
                for part in self._parts:
                    closing.start_soon(part.aclose)

            if listener is not None:
                abort_handshakes(listener)

    async def _abort(self) -> None:
        """Close the listener and every connection at once, then wait for the handlers and the connections to end."""
        listener = self._listener
        if listener is not None:
            listener.close()
            abort_handshakes(listener)
        for detector in tuple(self._detecting):
            detector.abort()
        for part in self._parts:
            part.abort()

        with release_shield():
            handlers = self._handlers()
            if handlers:
                await asyncio.wait(handlers)
            for part in self._parts:
                await part.wait_closed()


def _h3_url(url: URL) -> URL:
    """Return ``url`` with each parameter ``http3.<key>`` in place of ``<key>``."""
    parameters = {key: value for key, value in url.parameters.items() if not key.startswith(_H3_PREFIX)}
    parameters |= {
        key.removeprefix(_H3_PREFIX): value for key, value in url.parameters.items() if key.startswith(_H3_PREFIX)
    }
    return url.replace(parameters=parameters)


async def _stop_listening(listener: asyncio.Server, task_group: TaskGroup) -> None:
    """Close ``listener``, leaving in ``task_group`` a task that waits until its connections have closed."""
    waiting = anyio.Event()
    loop = asyncio.get_running_loop()

    async def wait_closed() -> None:
        # The callback runs only after wait_closed() reaches its first suspension.
        loop.call_soon(waiting.set)
        await listener.wait_closed()

    # Started before the close: older asyncio versions stop tracking clients once the listener closes.
    task_group.start_soon(wait_closed)
    await waiting.wait()
    listener.close()


class _Detector(asyncio.BufferedProtocol):
    """Hands a TCP connection to HTTP/2 or HTTP/1.1: by ALPN over TLS, else by whether it opens with the HTTP/2 preface.

    Over TLS, HTTP/2 needs ALPN to select ``h2``, RFC 9113 section 3.3; a
    connection that selects nothing gets HTTP/1.1.

    It reads at most the length of the preface before it chooses; the
    transport then calls the chosen protocol, except that a proactor event
    loop feeds the rest of a read under way to this one, which passes it on.
    It stays in ``detecting`` until it chooses.
    """

    def __init__(self, detecting: set["_Detector"], h1: AioH1Connections, h2: AioH2Connections) -> None:
        self._detecting = detecting
        self._h1_connections = h1
        self._h2_connections = h2
        self._buffer = bytearray(len(_PREFACE))
        self._received = 0
        self._chosen: AioH1Connection | AioH2Connection | None = None
        self._transport: asyncio.Transport | None = None
        self._timer: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = cast(asyncio.Transport, transport)
        if self._h1_connections.closing:
            transport.close()
            return

        # Over TLS, ALPN picks the version, HTTP/1.1 without it; in cleartext, the first bytes tell.
        ssl_object = transport.get_extra_info("ssl_object")
        alpn = None if ssl_object is None else ssl_object.selected_alpn_protocol()
        if alpn == "h2":
            self._hand_over(self._h2_connections.create_connection())
        elif ssl_object is not None:
            self._hand_over(self._h1_connections.create_connection())
        else:
            self._detecting.add(self)
            self._arm_timer(self._h1_connections.idle_timeout)

    def get_buffer(self, sizehint: int) -> memoryview:
        if self._chosen is not None:
            return self._chosen.get_buffer(sizehint)
        return memoryview(self._buffer)[self._received :]

    def buffer_updated(self, nbytes: int) -> None:
        if self._chosen is not None:
            self._chosen.buffer_updated(nbytes)
            return

        start = self._received
        self._received = end = start + nbytes

        if self._buffer[start:end] != _PREFACE[start:end]:
            self._hand_over(self._h1_connections.create_connection())
        elif end == len(_PREFACE):
            self._hand_over(self._h2_connections.create_connection())
        elif start == 0:
            # The version is being sent; the rest of it gets the time of a request head.
            self._cancel_timer()
            self._arm_timer(self._h1_connections.header_timeout)

    def eof_received(self) -> bool:
        # A peer that stops partway through the preface sent nothing to answer.
        return False

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel_timer()
        self._detecting.discard(self)

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()

    def abort(self) -> None:
        if self._transport is not None:
            self._transport.abort()

    def _hand_over(self, protocol: AioH1Connection | AioH2Connection) -> None:
        """Make ``protocol`` the connection's, replaying to it the bytes read so far."""
        self._cancel_timer()
        self._detecting.discard(self)

        transport = self._transport
        assert transport is not None
        self._chosen = protocol
        transport.set_protocol(protocol)
        protocol.connection_made(transport)

        received = self._received
        if received and not transport.is_closing():
            memoryview(protocol.get_buffer(received))[:received] = self._buffer[:received]
            protocol.buffer_updated(received)

    def _arm_timer(self, timeout: float) -> None:
        if timeout != math.inf:
            self._timer = asyncio.get_running_loop().call_later(timeout, self.close)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
