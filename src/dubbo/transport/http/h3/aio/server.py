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
from contextlib import suppress
from typing import Any

import anyio
import ngquic

from dubbo.transport.http.h3.base import H3ErrorCode, H3StreamHandler
from dubbo.transport.http.h3.exceptions import H3ConnectionError, H3Error, H3StreamResetError, H3UsageError
from dubbo.url import URL
from dubbo.utils import release_shield

from .connection import AioH3Connection
from .endpoint import AioQuicEndpoint, open_endpoint
from .settings import h3_settings
from .stream import AioH3ServerStream

__all__ = ["AioH3Connections"]

_logger = logging.getLogger(__name__)


class AioH3Connections:
    """The UDP sockets of a server, the HTTP/3 connections they accept and the handler tasks of their streams.

    Every connection takes the TLS and HTTP/3 parameters of ``url``, and
    each request stream runs ``handler`` in its own task. An ordinary
    handler failure is logged and aborts only its stream. Once `closing` is
    set, new connections and streams are refused.

    Raises:
        ValueError: TLS is not enabled, a certificate is missing, or a
            parameter is invalid.
        OSError: A TLS file cannot be loaded.
    """

    def __init__(self, url: URL, handler: H3StreamHandler) -> None:
        self._handler = handler
        self._settings = h3_settings(url, server=True)
        self.closing = False
        self._endpoints: list[AioQuicEndpoint] = []
        self.connections: set[AioH3Connection] = set()
        self.handlers: set[asyncio.Task[None]] = set()

    async def bind(self, host: str, port: int) -> tuple[Any, ...]:
        """Listen on UDP at ``host`` and ``port``, and return the address bound.

        Raises:
            OSError: The address cannot be bound.
        """
        endpoint = AioQuicEndpoint(on_incoming=lambda incoming: self._accept(endpoint, incoming))
        await open_endpoint(endpoint, local=(host, port))
        self._endpoints.append(endpoint)
        assert endpoint.local_address is not None
        return endpoint.local_address

    def announce_shutdown(self) -> None:
        for connection in tuple(self.connections):
            connection.announce_shutdown()

    async def aclose(self) -> None:
        """Close each connection once its responses are acknowledged, or abort it after a grace period."""
        async with anyio.create_task_group() as closing:
            for connection in tuple(self.connections):
                closing.start_soon(connection.aclose)

    def abort(self) -> None:
        for connection in tuple(self.connections):
            connection.abort()

    async def wait_closed(self) -> None:
        for connection in tuple(self.connections):
            await connection.wait_closed()

    async def close_sockets(self) -> None:
        """Close the UDP sockets, which ends every connection still on them."""
        endpoints, self._endpoints = self._endpoints, []
        for endpoint in endpoints:
            endpoint.close()
        with release_shield():
            for endpoint in endpoints:
                await endpoint.wait_closed()

    def _accept(self, endpoint: AioQuicEndpoint, incoming: ngquic.Incoming) -> None:
        """Accept a connection attempt, or refuse it while the server shuts down."""
        if self.closing:
            incoming.refuse()
            return

        settings = self._settings
        try:
            engine = incoming.accept(tls=settings.tls, transport=settings.transport, h3=settings.h3)
        except ngquic.QUICError:
            _logger.exception("Failed to accept an HTTP/3 connection from %s", incoming.remote_addr)
            return
        # None when ngtcp2 dropped the attempt or asked for address validation with a Retry.
        if engine is None:
            return

        connection = AioH3Connection(
            endpoint, engine, on_stream=self._start_handler, on_closed=self.connections.discard
        )
        self.connections.add(connection)
        endpoint.add(connection)

    def _start_handler(self, stream: AioH3ServerStream) -> None:
        if self.closing:
            with suppress(H3Error):
                stream._reset(H3ErrorCode.H3_REQUEST_REJECTED)
            return

        task = asyncio.get_running_loop().create_task(self._run_handler(stream))
        self.handlers.add(task)
        task.add_done_callback(self.handlers.discard)

    async def _run_handler(self, stream: AioH3ServerStream) -> None:
        """Run the handler for one stream, aborting the stream if the handler leaves it unfinished."""
        try:
            async with stream:
                # The handler is cancelled alone when its stream is aborted, in a scope so that its shields hold.
                # The scope is made in the task: outside one, AnyIO may not tell which event loop runs.
                with anyio.CancelScope() as scope:
                    stream._cancel_on_abort(scope.cancel)
                    try:
                        await self._handler(stream)
                    except H3Error:
                        raise
                    except Exception:
                        # A failure of the server itself, which the peer must not take for a cancellation.
                        with suppress(H3Error):
                            await stream.reset(H3ErrorCode.H3_INTERNAL_ERROR)
                        raise
        except (H3ConnectionError, H3StreamResetError):
            pass
        except H3UsageError:
            _logger.warning("The HTTP/3 stream handler used its stream out of order", exc_info=True)
        except H3Error:
            # A stream failed by the request, such as a malformed one.
            _logger.debug("The HTTP/3 stream handler ended on an error of its stream", exc_info=True)
        except Exception:
            _logger.exception("The HTTP/3 stream handler failed")
