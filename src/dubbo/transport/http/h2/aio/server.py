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

import anyio

from dubbo.transport.http.h2.base import H2ErrorCode, H2StreamHandler
from dubbo.transport.http.h2.exceptions import H2ConnectionError, H2Error, H2StreamResetError, H2UsageError
from dubbo.url import URL

from .connection import AioH2Connection
from .settings import h2_settings
from .stream import AioH2ServerStream

__all__ = ["AioH2Connections"]

_logger = logging.getLogger(__name__)


class AioH2Connections:
    """The HTTP/2 connections of a server and the handler tasks of their streams.

    Every connection takes the HTTP/2 parameters of ``url``, and each stream
    a client opens runs ``handler`` in its own task. An ordinary handler
    failure is logged and resets only its stream. Once `closing` is set,
    new connections are aborted and new streams refused.

    Raises:
        ValueError: A parameter is invalid.
    """

    def __init__(self, url: URL, handler: H2StreamHandler, *, require_h2_alpn: bool) -> None:
        self._settings = h2_settings(url, server=True)
        self._handler = handler
        self._require_h2_alpn = require_h2_alpn
        self.closing = False
        # The Alt-Svc field value of each final response that gives none.
        self.alt_svc: bytes | None = None
        self.connections: set[AioH2Connection] = set()
        self.handlers: set[asyncio.Task[None]] = set()

    def create_connection(self) -> AioH2Connection:
        """Return the protocol of a new server connection."""
        connection = AioH2Connection(
            self._settings,
            require_h2_alpn=self._require_h2_alpn,
            on_connected=self._connection_connected,
            on_stream=self._start_handler,
            on_closed=self.connections.discard,
        )
        connection.alt_svc = self.alt_svc
        return connection

    def announce_shutdown(self) -> None:
        for connection in tuple(self.connections):
            connection.announce_shutdown()

    async def aclose(self) -> None:
        """Close each connection once its queued responses are written, or abort it after a grace period."""
        async with anyio.create_task_group() as closing:
            for connection in tuple(self.connections):
                closing.start_soon(connection.aclose)

    def abort(self) -> None:
        for connection in tuple(self.connections):
            connection.abort()

    async def wait_closed(self) -> None:
        for connection in tuple(self.connections):
            await connection.wait_closed()

    def _connection_connected(self, connection: AioH2Connection) -> None:
        """Own an established connection, or abort one that arrives while the server shuts down, as it carries nothing."""
        if self.closing:
            connection.start_close()
            connection.abort()
        else:
            self.connections.add(connection)

    def _start_handler(self, stream: AioH2ServerStream) -> None:
        if self.closing:
            with suppress(H2Error):
                stream._reset(H2ErrorCode.REFUSED_STREAM)
            return

        task = asyncio.get_running_loop().create_task(self._run_handler(stream))
        self.handlers.add(task)
        task.add_done_callback(self.handlers.discard)

    async def _run_handler(self, stream: AioH2ServerStream) -> None:
        """Run the handler for one stream, resetting the stream if the handler leaves it unfinished."""
        try:
            async with stream:
                # The handler is cancelled alone when its stream is aborted, in a scope so that its shields hold.
                # The scope is made in the task: outside one, AnyIO may not tell which event loop runs.
                with anyio.CancelScope() as scope:
                    stream._cancel_on_abort(scope.cancel)
                    try:
                        await self._handler(stream)
                    except H2Error:
                        raise
                    except Exception:
                        # A failure of the server itself, which the peer must not take for a cancellation.
                        with suppress(H2Error):
                            await stream.reset(H2ErrorCode.INTERNAL_ERROR)
                        raise
        except (H2ConnectionError, H2StreamResetError):
            pass
        except H2UsageError:
            _logger.warning("The HTTP/2 stream handler used its stream out of order", exc_info=True)
        except H2Error:
            # A stream failed by the request, such as a malformed one.
            _logger.debug("The HTTP/2 stream handler ended on an error of its stream", exc_info=True)
        except Exception:
            _logger.exception("The HTTP/2 stream handler failed")
