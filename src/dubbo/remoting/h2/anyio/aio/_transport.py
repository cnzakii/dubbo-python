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
import asyncio
from contextlib import AsyncExitStack
from typing import Optional

import anyio
import sniffio
from anyio import abc as anyio_abc
from h2.config import H2Configuration

from dubbo.common import URL, constants
from dubbo.exceptions import ExceptionMapping, map_exceptions
from dubbo.logger import logger
from dubbo.remoting.backend.exceptions import ConnectError, ConnectTimeout
from dubbo.remoting.h2 import (
    AsyncHttp2Client,
    AsyncHttp2ConnectionHandlerType,
    AsyncHttp2Server,
    AsyncHttp2StreamHandlerType,
    AsyncHttp2Transport,
)

from ._protocol import Http2Protocol

__all__ = ["AioHttp2Client", "AioOHttp2Server", "AioHttp2Transport"]


def get_running_loop() -> asyncio.AbstractEventLoop:
    """Get the currently running asyncio event loop."""
    backend = sniffio.current_async_library()
    if backend != "asyncio":
        raise RuntimeError(f"Expected asyncio backend, got {backend}")
    return asyncio.get_running_loop()


class AioHttp2Client(AsyncHttp2Client, Http2Protocol):
    """An HTTP/2 client connection using AsyncIO."""

    __slots__ = ("_stack",)

    _stack: AsyncExitStack

    def __init__(self):
        super().__init__(H2Configuration(client_side=True, validate_inbound_headers=False))
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        """Enter the context manager, returning the client instance."""
        await self._stack.__aenter__()
        await self._stack.enter_async_context(self.task_group)
        self._stack.callback(self.task_group.cancel_scope.cancel)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Exit the context manager, cleaning up resources."""
        if not self._closed_event.is_set():
            try:
                await self.aclose()
            except Exception:
                # Ignore errors during graceful closure in exit context
                pass
            finally:
                self._finalize(exc=exc_value)

        if self._transport and not self._transport.is_closing():
            try:
                self._transport.close()
            except Exception:
                # Ignore errors during transport closure
                pass

        return await self._stack.__aexit__(exc_type, exc_value, traceback)


class AioOHttp2Server(AsyncHttp2Server):
    """An HTTP/2 server connection using AsyncIO."""

    __slots__ = ("listener", "_connection_handler", "_stream_handler")

    listener: Optional[asyncio.AbstractServer]

    _tg: Optional[anyio_abc.TaskGroup]
    _connection_handler: Optional[AsyncHttp2ConnectionHandlerType]
    _stream_handler: Optional[AsyncHttp2StreamHandlerType]

    def __init__(self) -> None:
        self.listener: Optional[asyncio.AbstractServer] = None
        self._connection_handler = None
        self._stream_handler = None

    def _protocol_factory(self) -> Http2Protocol:
        """Factory method to create an HTTP/2 protocol instance."""
        assert self._tg is not None, "Task group must be initialized before creating protocol"
        return Http2Protocol(
            h2_config=H2Configuration(client_side=False, validate_inbound_headers=False),
            stream_handler=self._stream_handler,
            connection_handler=self._connection_handler,
            task_group=self._tg,
        )

    async def serve(
        self,
        stream_handler: AsyncHttp2StreamHandlerType,
        connection_handler: Optional[AsyncHttp2ConnectionHandlerType] = None,
    ) -> None:
        """Starts the server and listens for incoming connections.

        Args:
            stream_handler: Handler for HTTP/2 streams.
            connection_handler: Optional handler for HTTP/2 connections.
        """
        self._stream_handler = stream_handler
        self._connection_handler = connection_handler

        assert self.listener is not None, "Listener must be initialized before serving"

        async with anyio.create_task_group() as tg:
            self._tg = tg
            await self.listener.serve_forever()


_DEFAULT_CONNECTION_TIMEOUT = 10.0  # seconds


class AioHttp2Transport(AsyncHttp2Transport):
    """An HTTP/2 transport implementation using AnyIO."""

    async def connect(self, url: URL) -> AioHttp2Client:
        """Connects to the given URL and returns an HTTP/2 client connection."""
        timeout = url.get_param_float(constants.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)

        exc_map: ExceptionMapping = {
            TimeoutError: ConnectTimeout,
            ConnectionRefusedError: ConnectError,
            ConnectError: ConnectError,
        }
        with map_exceptions(exc_map):
            with anyio.fail_after(timeout):
                _, client = await get_running_loop().create_connection(
                    lambda: AioHttp2Client(),
                    host=url.host,
                    port=url.port,
                )
                return client

    async def bind(self, url: URL) -> AioOHttp2Server:
        """Binds to the given URL and returns an HTTP/2 server connection."""
        server = AioOHttp2Server()
        listener = await get_running_loop().create_server(
            protocol_factory=server._protocol_factory,
            host=url.host,
            port=url.port,
        )
        server.listener = listener
        logger.info("HTTP/2 server bound to %s", url.location)
        return server
