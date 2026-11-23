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
import logging
import math
from contextlib import suppress

import anyio
import h11r

from dubbo.constants import (
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_MAX_HEADER_LIST_SIZE,
    IDLE_TIMEOUT_KEY,
    MAX_HEADER_LIST_SIZE_KEY,
)
from dubbo.transport.http.h1.base import H1ExchangeHandler
from dubbo.transport.http.h1.exceptions import H1ConnectionError, H1Error, H1ExchangeError
from dubbo.url import URL
from dubbo.utils import close_then_abort
from dubbo.utils.converters import milliseconds

from .connection import AioH1Connection
from .exchange import AioH1ServerExchange

__all__ = ["AioH1Connections"]

_logger = logging.getLogger(__name__)

_REQUEST_HEADER_TIMEOUT_KEY = "request-header-timeout"
_DEFAULT_REQUEST_HEADER_TIMEOUT = 10.0  # Seconds.
_REQUEST_BODY_TIMEOUT_KEY = "request-body-timeout"
_DEFAULT_REQUEST_BODY_TIMEOUT = 30.0  # Seconds.


class AioH1Connections:
    """The HTTP/1.1 connections of a server, each served by a task that runs the handler for one request at a time.

    ``url`` gives ``max-header-list-size``, ``idle-timeout``,
    ``request-header-timeout`` and ``request-body-timeout`` as
    `dubbo.transport.http.HttpServer` describes them. Once `closing` is set, new connections are aborted and
    each response under way asks to close its connection.

    Attributes:
        idle_timeout: Seconds a connection may wait for a request to begin.
        header_timeout: Seconds a request head may take once it begins.
        body_timeout: Seconds a request body may take once its head arrived.

    Raises:
        ValueError: A parameter is invalid.
    """

    def __init__(self, url: URL, handler: H1ExchangeHandler) -> None:
        self._handler = handler
        self._max_head_bytes = url.get_parameter(MAX_HEADER_LIST_SIZE_KEY, int, DEFAULT_MAX_HEADER_LIST_SIZE)
        idle_timeout = url.get_parameter(IDLE_TIMEOUT_KEY, milliseconds, DEFAULT_IDLE_TIMEOUT)
        header_timeout = url.get_parameter(_REQUEST_HEADER_TIMEOUT_KEY, milliseconds, _DEFAULT_REQUEST_HEADER_TIMEOUT)
        body_timeout = url.get_parameter(_REQUEST_BODY_TIMEOUT_KEY, milliseconds, _DEFAULT_REQUEST_BODY_TIMEOUT)
        for key, value in (
            (IDLE_TIMEOUT_KEY, idle_timeout),
            (_REQUEST_HEADER_TIMEOUT_KEY, header_timeout),
            (_REQUEST_BODY_TIMEOUT_KEY, body_timeout),
        ):
            if value < 0:
                raise ValueError(f"URL parameter {key!r} must not be negative")
        if not self._max_head_bytes > 0:
            raise ValueError(f"URL parameter {MAX_HEADER_LIST_SIZE_KEY!r} must be positive")
        self.idle_timeout = idle_timeout or math.inf
        self.header_timeout = header_timeout or math.inf
        self.body_timeout = body_timeout or math.inf

        self.closing = False
        # The Alt-Svc field value of each final response that gives none.
        self.alt_svc: bytes | None = None
        # Each connection and the exchange it runs, if any.
        self._connections: dict[AioH1Connection, AioH1ServerExchange | None] = {}
        # The connections told to close after their current or next response.
        self._draining: set[AioH1Connection] = set()
        self.handlers: set[asyncio.Task[None]] = set()

    def create_connection(self) -> AioH1Connection:
        """Return the protocol of a new server connection."""
        return AioH1Connection(
            h11r.Role.SERVER,
            max_head_bytes=self._max_head_bytes,
            on_connected=self._connection_made,
            on_closed=self._connection_closed,
        )

    def announce_shutdown(self) -> None:
        for connection, exchange in tuple(self._connections.items()):
            self._draining.add(connection)
            if exchange is not None:
                exchange.close_after_response()
            elif not connection.engine.buffered_nbytes:
                # Waiting for a request that has not begun; one under way is answered first.
                connection.close()

    async def aclose(self) -> None:
        """Close the connections still open once their tasks have ended, each aborted after a grace period."""
        async with anyio.create_task_group() as closing:
            for connection in tuple(self._connections):
                connection.close()
                closing.start_soon(
                    close_then_abort, connection.wait_closed, lambda c=connection: c.closed, connection.abort
                )

    def abort(self) -> None:
        for connection in tuple(self._connections):
            connection.abort()

    async def wait_closed(self) -> None:
        for connection in tuple(self._connections):
            await connection.wait_closed()

    def _connection_made(self, connection: AioH1Connection) -> None:
        if self.closing:
            connection.abort()
            return
        self._connections[connection] = None
        task = asyncio.get_running_loop().create_task(self._serve(connection))
        self.handlers.add(task)
        task.add_done_callback(self.handlers.discard)

    def _connection_closed(self, connection: AioH1Connection) -> None:
        self._connections.pop(connection, None)
        self._draining.discard(connection)

    async def _serve(self, connection: AioH1Connection) -> None:
        """Run the handler for each request of ``connection`` in turn, then close it."""
        try:
            while not connection.closed and connection not in self._draining:
                request = await self._receive_request(connection)
                if request is None:
                    break
                exchange = AioH1ServerExchange(
                    connection,
                    request,
                    closing=connection in self._draining,
                    alt_svc=self.alt_svc,
                    body_deadline=asyncio.get_running_loop().time() + self.body_timeout,
                )
                self._connections[connection] = exchange
                await self._run_handler(connection, exchange)
                if connection in self._connections:
                    self._connections[connection] = None

                tunnel = exchange.tunnel_taken
                if tunnel is not None:
                    await tunnel.aclose()
                    return
                if not exchange.response_ended:
                    # A response missing or cut short can only be abandoned with the connection.
                    connection.abort()
                    return
                exchange.take_buffered_end()
                engine = connection.engine
                # A request body left unread, or either side asking to close, ends the connection after the response.
                if (
                    not exchange.complete
                    or not connection.usable
                    or engine.local_state is not h11r.State.DONE
                    or engine.peer_state is not h11r.State.DONE
                ):
                    break
                engine.start_next_cycle()
        except H1Error:
            pass
        finally:
            connection.close()
            await close_then_abort(connection.wait_closed, lambda: connection.closed, connection.abort)

    async def _receive_request(self, connection: AioH1Connection) -> h11r.Request | None:
        """Wait for the next request head within the timeouts, or return ``None`` when the connection ends instead."""
        with anyio.move_on_after(self.idle_timeout):
            await connection.wait_for_bytes()
        if not connection.engine.buffered_nbytes and not connection.peer_finished:
            # Idle past its timeout, or closed by a shutdown.
            return None

        try:
            with anyio.fail_after(self.header_timeout):
                event = await connection.next_event()
        except TimeoutError:
            return None
        except H1ConnectionError as error:
            if error.status is not None:
                self._refuse(connection, error.status)
            return None

        if type(event) is h11r.Request:
            return event
        return None

    @staticmethod
    def _refuse(connection: AioH1Connection, status: int) -> None:
        """Answer a request that broke the protocol, before the connection closes."""
        engine = connection.engine
        with suppress(H1Error):
            connection.send(engine.send_response, status, ((b"content-length", b"0"), (b"connection", b"close")))
            connection.send(engine.end_of_message)

    async def _run_handler(self, connection: AioH1Connection, exchange: AioH1ServerExchange) -> None:
        try:
            # The handler is cancelled when the client goes, in a scope so that its shields hold.
            with anyio.CancelScope() as scope:
                connection.on_lost = scope.cancel
                try:
                    await self._handler(exchange)
                finally:
                    connection.on_lost = None
        except H1ExchangeError:
            _logger.warning("The HTTP/1.1 exchange handler used its exchange out of order", exc_info=True)
        except H1Error:
            pass
        except Exception:
            _logger.exception("The HTTP/1.1 exchange handler failed")
