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
import ipaddress
from collections import deque
from typing import Any, Self

import anyio
import h11r

from dubbo.constants import (
    CONNECT_TIMEOUT_KEY,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_HEADER_LIST_SIZE,
    IDLE_TIMEOUT_KEY,
    MAX_HEADER_LIST_SIZE_KEY,
)
from dubbo.transport.http.h1.base import H1Client, H1Headers
from dubbo.transport.http.h1.exceptions import H1ConnectionError
from dubbo.transport.tls import get_ssl_context, server_hostname
from dubbo.url import URL
from dubbo.utils import close_then_abort, release_shield
from dubbo.utils.converters import milliseconds

from .connection import AioH1Connection
from .exchange import AioH1ClientExchange

__all__ = ["AioH1Client"]

_MAX_CONNECTIONS_KEY = "max-connections"
_DEFAULT_MAX_CONNECTIONS = 100
_DEFAULT_IDLE_TIMEOUT = 5.0  # Seconds.
_HAPPY_EYEBALLS_DELAY = 0.25


class AioH1Client(H1Client):
    """A pool of HTTP/1.1 connections to one server, through asyncio.

    Concurrent tasks may share the client, but after the first request all
    calls and returned exchanges must remain on that asyncio event loop. An
    exchange takes the most recently used idle connection, or a new one
    while fewer than ``max-connections`` are open, or waits in arrival order
    for one to free.

    ``url`` names the server host and port, with the parameters `H1Client`
    describes.

    Raises:
        ValueError: The URL has no host or port, or a parameter is invalid.
        OSError: A TLS file cannot be loaded.
    """

    def __init__(self, url: URL) -> None:
        super().__init__(url)

        if url.host is None or url.port is None:
            raise ValueError(f"an HTTP/1.1 client needs a host and a port: {url}")

        connect_timeout = url.get_parameter(CONNECT_TIMEOUT_KEY, milliseconds, DEFAULT_CONNECT_TIMEOUT)
        idle_timeout = url.get_parameter(IDLE_TIMEOUT_KEY, milliseconds, _DEFAULT_IDLE_TIMEOUT)
        max_connections = url.get_parameter(_MAX_CONNECTIONS_KEY, int, _DEFAULT_MAX_CONNECTIONS)
        max_head_bytes = url.get_parameter(MAX_HEADER_LIST_SIZE_KEY, int, DEFAULT_MAX_HEADER_LIST_SIZE)
        for key, value in (
            (CONNECT_TIMEOUT_KEY, connect_timeout),
            (_MAX_CONNECTIONS_KEY, max_connections),
            (MAX_HEADER_LIST_SIZE_KEY, max_head_bytes),
        ):
            if not value > 0:
                raise ValueError(f"URL parameter {key!r} must be positive")
        if idle_timeout < 0:
            raise ValueError(f"URL parameter {IDLE_TIMEOUT_KEY!r} must not be negative")

        self._host = url.host
        self._port = url.port
        self._ssl_context = get_ssl_context(url, server=False, alpn=("http/1.1",))
        self._server_hostname = server_hostname(url) if self._ssl_context is not None else None
        self._connect_timeout = connect_timeout
        self._idle_timeout = idle_timeout
        self._max_connections = max_connections
        self._max_head_bytes = max_head_bytes
        self._host_is_name = _is_name(url.host)

        # Every connection open, those idle among them most recently used last, and when each idle one expires.
        self._connections: set[AioH1Connection] = set()
        # The scope of each connection attempt under way, which closing the client cancels.
        self._attempts: set[anyio.CancelScope] = set()
        self._attempts_ended = asyncio.Event()
        self._idle: list[AioH1Connection] = []
        self._expiries: dict[AioH1Connection, asyncio.TimerHandle] = {}
        # Callers waiting for a connection: each is given an idle one, or ``None`` to open its own.
        self._waiters: deque[asyncio.Future[AioH1Connection | None]] = deque()
        self._closed = False
        self._shutdown: asyncio.Task[None] | None = None

    async def request(
        self, method: bytes, target: bytes, headers: H1Headers, *, end_stream: bool = False
    ) -> AioH1ClientExchange:
        connection = await self._acquire()
        engine = connection.engine
        try:
            connection.send(engine.send_request, method, target, headers)
            if end_stream:
                connection.send(engine.end_of_message)
            exchange = AioH1ClientExchange(connection, self, send_ended=end_stream)
            await connection.drain()
        except BaseException:
            # The connection holds part of a request, which only closing it abandons.
            connection.abort()
            raise
        return exchange

    async def aclose(self) -> None:
        # One shutdown task, which every concurrent aclose waits on.
        if self._shutdown is None:
            self._closed = True
            self._shutdown = asyncio.get_running_loop().create_task(self._shut_down())

        shutdown = self._shutdown
        try:
            await asyncio.wait((shutdown,))
        except BaseException:
            # A cancelled close stops at once: every connection is aborted, then the task is awaited.
            shutdown.cancel()
            for connection in tuple(self._connections):
                connection.abort()
            with release_shield():
                await asyncio.wait((shutdown,))
            raise

        if not shutdown.cancelled():
            shutdown.result()

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError("the HTTP/1.1 client is closed")
        return self

    async def _acquire(self) -> AioH1Connection:
        """Return an idle connection, a new one, or one freed while waiting."""
        while True:
            if self._closed:
                raise RuntimeError("the HTTP/1.1 client is closed")
            # The most recently used idle connection first; a stale one is aborted.
            while self._idle:
                connection = self._idle.pop()
                self._cancel_expiry(connection)
                if connection.usable:
                    return connection
                connection.abort()
            if len(self._connections) + len(self._attempts) < self._max_connections:
                return await self._open()

            # At the limit: wait for a released connection, or None when a place frees up.
            waiter: asyncio.Future[AioH1Connection | None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                connection = await waiter
            except BaseException:
                # Cancelled after being handed a connection or a place: pass it on to the next waiter.
                if waiter.done() and not waiter.cancelled():
                    given = waiter.result()
                    if given is None:
                        self._wake_waiter(None)
                    else:
                        self._release(given)
                else:
                    waiter.cancel()
                    if waiter in self._waiters:
                        self._waiters.remove(waiter)
                raise
            if connection is not None:
                return connection
            # A place freed up; another caller may have taken it first, then the loop waits again.
            if not self._closed and len(self._connections) + len(self._attempts) < self._max_connections:
                return await self._open()

    async def _open(self) -> AioH1Connection:
        """Establish a connection within the connect timeout.

        Raises:
            RuntimeError: The client closed during the attempt.
        """
        # Each attempt counts against the limit, and closing the client cancels it.
        with anyio.CancelScope() as attempt:
            self._attempts.add(attempt)
            try:
                connection = await self._connect()
            finally:
                self._attempts.discard(attempt)
                if not self._attempts:
                    self._attempts_ended.set()
        if attempt.cancelled_caught:
            raise RuntimeError("the HTTP/1.1 client is closed")

        # Added first, so that the abort below is counted when the connection closes.
        self._connections.add(connection)
        if self._closed:
            connection.abort()
            raise RuntimeError("the HTTP/1.1 client is closed")
        return connection

    async def _connect(self) -> AioH1Connection:
        connection = AioH1Connection(
            h11r.Role.CLIENT, max_head_bytes=self._max_head_bytes, on_closed=self._connection_closed
        )
        protocol_created = False

        def create_protocol() -> AioH1Connection:
            nonlocal protocol_created
            protocol_created = True
            return connection

        loop = asyncio.get_running_loop()
        # Only the standard library loops race addresses; uvloop rejects the argument.
        race = self._host_is_name and isinstance(loop, asyncio.BaseEventLoop)
        options: dict[str, Any] = {"happy_eyeballs_delay": _HAPPY_EYEBALLS_DELAY} if race else {}
        try:
            with anyio.fail_after(self._connect_timeout):
                await loop.create_connection(
                    create_protocol,
                    self._host,
                    self._port,
                    ssl=self._ssl_context,
                    server_hostname=self._server_hostname,
                    **options,
                )
        except BaseException as error:
            # The place this attempt held frees up for a waiter.
            self._wake_waiter(None)
            if protocol_created:
                connection.abort()
                with release_shield():
                    await connection.wait_closed()
            if isinstance(error, OSError):
                raise H1ConnectionError(f"failed to connect to {self._host}:{self._port}") from error
            raise
        return connection

    def _release(self, connection: AioH1Connection) -> None:
        """Take back a connection whose exchange completed, for the next caller or for later."""
        if self._closed or not connection.usable:
            connection.close()
            return

        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(connection)
                return
        if not self._idle_timeout:
            connection.close()
            return
        self._idle.append(connection)
        self._expiries[connection] = asyncio.get_running_loop().call_later(self._idle_timeout, connection.close)

    def _connection_closed(self, connection: AioH1Connection) -> None:
        self._cancel_expiry(connection)
        if connection in self._idle:
            self._idle.remove(connection)
        if connection in self._connections:
            self._connections.discard(connection)
            self._wake_waiter(None)

    def _cancel_expiry(self, connection: AioH1Connection) -> None:
        expiry = self._expiries.pop(connection, None)
        if expiry is not None:
            expiry.cancel()

    def _wake_waiter(self, connection: AioH1Connection | None) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(connection)
                return

    async def _shut_down(self) -> None:
        """Close idle connections, release waiters, cancel attempts, then wait for busy connections to close."""
        for connection in self._idle:
            connection.close()
        # Waiters wake with None, then find the client closed.
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)

        if self._attempts:
            self._attempts_ended.clear()
            for attempt in tuple(self._attempts):
                attempt.cancel()
            await self._attempts_ended.wait()

        async def wait_all_closed() -> None:
            for connection in tuple(self._connections):
                await connection.wait_closed()

        def abort_all() -> None:
            for connection in tuple(self._connections):
                connection.abort()

        await close_then_abort(wait_all_closed, lambda: not self._connections, abort_all)


def _is_name(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False
