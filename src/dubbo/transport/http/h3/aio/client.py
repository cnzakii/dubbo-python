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
from typing import Any, Self

import anyio

from dubbo.constants import CONNECT_TIMEOUT_KEY, DEFAULT_CONNECT_TIMEOUT
from dubbo.transport.http.h3.base import H3Client, H3Headers
from dubbo.transport.http.h3.exceptions import H3ConnectionError
from dubbo.url import URL
from dubbo.utils import release_shield
from dubbo.utils.converters import milliseconds
from dubbo.utils.dialer import Dialer

from .connection import AioH3Connection
from .endpoint import AioQuicEndpoint, open_endpoint
from .settings import h3_settings
from .stream import AioH3ClientStream

__all__ = ["AioH3Client"]


class AioH3Client(H3Client):
    """Connects to one HTTP/3 endpoint through asyncio.

    The client owns one active connection plus any predecessor draining after
    GOAWAY, each on a UDP socket of its own. An attempt tries the addresses
    the host resolves to in order, moving to the next only when one answers
    with an ICMP error, as a closed port does. Concurrent tasks may share the
    client, but after the first connection attempt all asynchronous calls and
    returned streams must remain on that asyncio event loop.

    Entering the client starts connecting in the background. One attempt
    runs at a time and lasts at most the connect timeout; callers that open
    streams meanwhile wait for it, and one that stops waiting leaves it
    running. After an attempt fails, the connection is lost, or its path
    stops carrying data, the client connects again in the background after
    a backoff, and the streams of that connection go on; after the connection
    closes on its idle timeout or a graceful close, it connects again for the
    next stream.

    ``url`` names the server host and port, with the parameters `H3Client`
    describes, plus ``stream-send-buffer-size``: bytes of a stream's body
    held unsent or unacknowledged before a send waits, 1 MiB when absent.

    Raises:
        ValueError: The URL has no host or port, TLS is not enabled, a
            parameter is invalid, or the connect timeout is not positive.
        OSError: A TLS file cannot be loaded.
    """

    def __init__(self, url: URL) -> None:
        super().__init__(url)

        if url.host is None or url.port is None:
            raise ValueError(f"an HTTP/3 client needs a host and a port: {url}")

        connect_timeout = url.get_parameter(CONNECT_TIMEOUT_KEY, milliseconds, DEFAULT_CONNECT_TIMEOUT)
        if not connect_timeout > 0:
            raise ValueError(f"URL parameter {CONNECT_TIMEOUT_KEY!r} must be positive")

        self._host = url.host
        self._port = url.port
        self._settings = h3_settings(url, server=False)
        self._connect_timeout = connect_timeout

        # The sockets of the connections, each closing once its connection finishes.
        self._endpoints: set[AioQuicEndpoint] = set()
        self._active_connection: AioH3Connection | None = None
        self._draining_connections: set[AioH3Connection] = set()
        self._dialer = Dialer(self._open_connection)
        self._closed = False
        self._shutdown: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return not self._closed and self._dialer.connected

    @property
    def connected(self) -> bool:
        current = self._active_connection
        return self.available and current is not None and current.accepting_streams

    async def connect(self) -> None:
        if self._closed:
            raise RuntimeError("the HTTP/3 client is closed")
        if not self.connected and await self._dialer.connect() is None:
            raise RuntimeError("the HTTP/3 client is closed")

    async def open_stream(self, headers: H3Headers, *, end_stream: bool = False) -> AioH3ClientStream:
        replaced = False
        while True:
            connection = await self._get_or_open_connection()
            stream = await connection.open_stream(headers, end_stream=end_stream)
            if stream is not None:
                return stream
            # The connection stopped taking streams, as after a GOAWAY; a new one is tried once.
            if replaced:
                connection.raise_if_failed()
                raise H3ConnectionError("the HTTP/3 connection became unavailable before opening a stream")
            replaced = True

    async def drain(self) -> None:
        if self._shutdown is None:
            self._closed = True
            # A connection lost meanwhile is not replaced.
            self._dialer.close()
            connections = self._owned()
            for connection in connections:
                connection.start_close()
            await asyncio.gather(*(connection.wait_closed() for connection in connections))
        await self.aclose()

    async def aclose(self) -> None:
        """Close the client, its connections and its socket for good.

        The connections close together, each once its open streams have
        finished, or after a second. A connection attempt under way is
        abandoned. A cancelled close aborts the connections at once. Closing
        again waits for the close under way.
        """
        if self._shutdown is None:
            self._closed = True
            self._shutdown = asyncio.get_running_loop().create_task(self._shut_down())

        shutdown = self._shutdown
        try:
            await asyncio.wait((shutdown,))
        except BaseException:
            # A cancelled close aborts at once and waits only for that.
            shutdown.cancel()
            self._dialer.close()
            for connection in self._owned():
                connection.abort()
            endpoints = tuple(self._endpoints)
            for endpoint in endpoints:
                endpoint.close()
            with release_shield():
                await asyncio.wait((shutdown,))
                for endpoint in endpoints:
                    await endpoint.wait_closed()
            raise

        if not shutdown.cancelled():
            shutdown.result()

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError("the HTTP/3 client is closed")
        self._dialer.start()
        return self

    async def _get_or_open_connection(self) -> AioH3Connection:
        if self._closed:
            raise RuntimeError("the HTTP/3 client is closed")

        current = self._active_connection
        if current is not None and current.accepting_streams:
            return current

        connection = await self._dialer.connect()
        if connection is None:
            raise RuntimeError("the HTTP/3 client is closed")
        return connection

    async def _open_connection(self) -> AioH3Connection:
        """Establish and publish one replacement connection within the connect timeout."""
        current = self._active_connection
        if current is not None and not current.closed:
            self._draining_connections.add(current)
            current.start_close()
        self._active_connection = None
        self._endpoints = {endpoint for endpoint in self._endpoints if not endpoint.closed}

        loop = asyncio.get_running_loop()
        connection: AioH3Connection | None = None
        try:
            # TimeoutError is an OSError.
            with anyio.fail_after(self._connect_timeout):
                addresses = await loop.getaddrinfo(self._host, self._port, type=socket.SOCK_DGRAM)
                for index, (family, _, _, _, address) in enumerate(addresses):
                    connection = await self._try_address(family, address)
                    try:
                        await connection.wait_established()
                    except H3ConnectionError:
                        if not connection.unreachable or index == len(addresses) - 1:
                            raise
                    else:
                        return connection
        except BaseException as error:
            if connection is not None:
                # The connection carried no stream, so it is released at once, even when cancelled.
                connection.abort()
            if isinstance(error, OSError):
                raise H3ConnectionError(f"failed to connect to {self._host}:{self._port}") from error
            raise
        raise H3ConnectionError(f"{self._host} resolved to no address")

    async def _try_address(self, family: int, address: tuple[Any, ...]) -> AioH3Connection:
        """Start a connection to ``address`` on a socket of its own, which aborts if it does not complete."""
        endpoint = AioQuicEndpoint(close_when_empty=True)
        await open_endpoint(endpoint, remote=address, family=family)
        self._endpoints.add(endpoint)
        if self._closed:
            endpoint.close()
            raise RuntimeError("the HTTP/3 client is closed")

        try:
            engine = endpoint.connect(self._settings)
        except BaseException:
            endpoint.close()
            raise

        connection = AioH3Connection(
            endpoint, engine, on_closed=self._connection_closed, on_degrading=self._connection_degrading
        )
        # Published before awaiting so a concurrent close owns this connection.
        self._active_connection = connection
        endpoint.add(connection)
        return connection

    async def _shut_down(self) -> None:
        for connection in self._owned():
            connection.start_close()
        # An attempt under way is abandoned; a connection it completed meanwhile is among those closed below.
        await self._dialer.aclose()

        try:
            async with anyio.create_task_group() as closing:
                for connection in self._owned():
                    closing.start_soon(connection.aclose)
        finally:
            # A connection in its closing period needs its socket no more once the client is gone.
            endpoints = tuple(self._endpoints)
            for endpoint in endpoints:
                endpoint.close()
            with release_shield():
                for endpoint in endpoints:
                    await endpoint.wait_closed()

    def _owned(self) -> tuple[AioH3Connection, ...]:
        active = () if self._active_connection is None else (self._active_connection,)
        return (*self._draining_connections, *active)

    def _connection_closed(self, connection: AioH3Connection) -> None:
        """Forget a closed connection; one lost to a failure makes the client connect again after a backoff."""
        self._draining_connections.discard(connection)
        if self._active_connection is connection:
            self._active_connection = None
            if connection.lost:
                # Ignored while the attempt that opened it is still under way, which reports the failure itself.
                self._dialer.lost()

    def _connection_degrading(self, connection: AioH3Connection) -> None:
        """Connect again after a backoff, as the path of ``connection`` stopped carrying data; its streams go on."""
        if self._active_connection is connection:
            self._dialer.lost()
