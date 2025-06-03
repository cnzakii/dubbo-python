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
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Optional, cast

import anyio
from anyio import abc as anyio_abc
from h2.config import H2Configuration

from dubbo.common import URL, constants
from dubbo.common.types import TypeAlias
from dubbo.exceptions import ExceptionMapping, map_exceptions
from dubbo.logger import logger
from dubbo.remoting.backend import (
    AnyIOBackend,
    AsyncNetworkBackend,
    AsyncNetworkServer,
    AsyncNetworkStream,
    AsyncStreamHandlerType,
)
from dubbo.remoting.backend.exceptions import ConnectTimeout

from ..base import (
    AsyncHttp2Client,
    AsyncHttp2ConnectionHandlerType,
    AsyncHttp2Server,
    AsyncHttp2StreamHandlerType,
    AsyncHttp2Transport,
)
from .connection import AnyIOH2Connection

__all__ = ["AnyIOH2Client", "AnyIOH2Server", "AnyIOH2Transport"]

_ServerType: TypeAlias = AsyncNetworkServer[AsyncNetworkStream[bytes], AsyncStreamHandlerType]


class AnyIOH2Client(AsyncHttp2Client, AnyIOH2Connection):
    """
    An AnyIO-based HTTP/2 client implementation.
    """

    _stack: AsyncExitStack

    def __init__(self, net_stream: AsyncNetworkStream[bytes]) -> None:
        super().__init__(
            task_group=anyio.create_task_group(),
            net_stream=net_stream,
            h2_config=H2Configuration(client_side=True, validate_inbound_headers=False),
        )
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        await self._stack.__aenter__()
        await self._stack.enter_async_context(self._tg)
        self._stack.callback(self._tg.cancel_scope.cancel)
        await super().__aenter__()
        return self

    async def __aexit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        await super().__aexit__(exc_type, exc_value, traceback)
        await self._stack.__aexit__(exc_type, exc_value, traceback)


class AnyIOH2Server(AsyncHttp2Server):
    """
    An AnyIO-based HTTP/2 server implementation.
    """

    __slots__ = ("_server", "_tg", "_connection_handler", "_stream_handler")

    _server: _ServerType
    _tg: Optional[anyio_abc.TaskGroup]
    _connection_handler: Optional[AsyncHttp2ConnectionHandlerType]
    _stream_handler: Optional[AsyncHttp2StreamHandlerType]

    def __init__(self, server: _ServerType) -> None:
        self._server = server
        self._tg = None
        self._connection_handler = None
        self._stream_handler = None

    async def _net_stream_wrapper(self, stream: AsyncNetworkStream[bytes]) -> None:
        """Wraps the stream with an HTTP/2 connection and handles incoming requests.

        Args:
            stream: The network stream to wrap.
        """
        if self._tg is None:
            logger.error("Task group is not initialized. Cannot handle incoming stream.")
            return

        async with AnyIOH2Connection(
            task_group=self._tg,
            net_stream=stream,
            h2_config=H2Configuration(client_side=False, validate_inbound_headers=False),
            stream_handler=self._stream_handler,
        ) as conn:
            logger.debug("New HTTP/2 connection established: %s", stream.get_extra_info("remote_address"))
            if self._connection_handler:
                await self._connection_handler(conn)

            # wait until the connection is closed
            await cast(AnyIOH2Connection, conn).wait_closed()

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

        async with AsyncExitStack() as stack:
            self._tg = await stack.enter_async_context(anyio.create_task_group())
            await self._server.serve(self._net_stream_wrapper)

    async def aclose(self) -> None:
        """Close the server and release resources."""
        if self._server:
            await self._server.aclose()
            logger.info("HTTP/2 server closed")
        else:
            logger.warning("Attempted to close an uninitialized HTTP/2 server")


_DEFAULT_CONNECTION_TIMEOUT = 10.0  # seconds


class AnyIOH2Transport(AsyncHttp2Transport):
    """An HTTP/2 transport implementation using AnyIO."""

    __slots__ = ("_backend",)

    _backend: AsyncNetworkBackend

    def __init__(self):
        self._backend = AnyIOBackend()

    async def connect(self, url: URL) -> AnyIOH2Client:
        """Connects to the given URL and returns an HTTP/2 client connection."""
        timeout = url.get_param_float(constants.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)

        exc_map: ExceptionMapping = {
            TimeoutError: ConnectTimeout,
        }
        with map_exceptions(exc_map):
            with anyio.fail_after(timeout):
                net_stream = await self._backend.connect_tcp(url.host, url.port)
                client = AnyIOH2Client(net_stream)
                logger.info("HTTP/2 connection established to %s", net_stream.get_extra_info("remote_address"))
                return client

    async def bind(self, url: URL) -> AnyIOH2Server:
        """Binds to the given URL and returns an HTTP/2 server connection."""
        timeout = url.get_param_float(constants.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)

        exc_map: ExceptionMapping = {
            TimeoutError: ConnectTimeout,
        }
        with map_exceptions(exc_map):
            with anyio.fail_after(timeout):
                server = await self._backend.create_tcp_server(local_host=url.host, local_port=url.port)
                logger.info("HTTP/2 server bound to %s", url.location)
                return AnyIOH2Server(server)
