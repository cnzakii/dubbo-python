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
from typing import Optional

import anyio
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
from dubbo.remoting.h2 import (
    AsyncHttp2Client,
    AsyncHttp2ConnectionHandlerType,
    AsyncHttp2Server,
    AsyncHttp2StreamHandlerType,
    AsyncHttp2Transport,
)

from ._connection import AnyIOHttp2Connection

__all__ = ["AnyIOHttp2Client", "AnyIOHttp2Server", "AnyIOHttp2Transport"]


_ServerType: TypeAlias = AsyncNetworkServer[AsyncNetworkStream[bytes], AsyncStreamHandlerType]


class AnyIOHttp2Client(AsyncHttp2Client, AnyIOHttp2Connection):
    """An HTTP/2 client connection using AnyIO."""

    def __init__(self, net_stream: AsyncNetworkStream):
        super().__init__(net_stream, H2Configuration(client_side=True, validate_inbound_headers=False))

    async def __aenter__(self) -> "AnyIOHttp2Client":
        await AnyIOHttp2Connection.__aenter__(self)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await AnyIOHttp2Connection.__aexit__(self, exc_type, exc_value, traceback)


class AnyIOHttp2Server(AsyncHttp2Server):
    """An HTTP/2 server connection using AnyIO."""

    __slots__ = ("_server", "_connection_handler", "_stream_handler")

    _server: _ServerType

    _connection_handler: Optional[AsyncHttp2ConnectionHandlerType]

    _stream_handler: Optional[AsyncHttp2StreamHandlerType]

    def __init__(self, server: _ServerType) -> None:
        self._server = server
        self._connection_handler = None
        self._stream_handler = None

    async def _net_stream_wrapper(self, stream: AsyncNetworkStream[bytes]) -> None:
        """Wraps the stream with an HTTP/2 connection and handles incoming requests.

        Args:
            stream: The network stream to wrap.
        """
        async with AnyIOHttp2Connection(
            net_stream=stream,
            h2_config=H2Configuration(client_side=False, validate_inbound_headers=False),
            stream_handler=self._stream_handler,
        ) as conn:
            logger.debug("New HTTP/2 connection established: %s", stream.get_extra_info("remote_address"))
            if self._connection_handler:
                await self._connection_handler(conn)

            # wait until the connection is closed
            await conn.wait_until_closed()
            logger.debug("HTTP/2 Connection closed: %s", stream.get_extra_info("remote_address"))

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
        await self._server.serve(self._net_stream_wrapper)


_DEFAULT_CONNECTION_TIMEOUT = 10.0  # seconds


class AnyIOHttp2Transport(AsyncHttp2Transport):
    """An HTTP/2 transport implementation using AnyIO."""

    __slots__ = ("_backend",)

    _backend: AsyncNetworkBackend

    def __init__(self):
        self._backend = AnyIOBackend()

    async def connect(self, url: URL) -> AnyIOHttp2Client:
        """Connects to the given URL and returns an HTTP/2 client connection."""
        timeout = url.get_param_float(constants.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)

        exc_map: ExceptionMapping = {
            TimeoutError: ConnectTimeout,
        }
        with map_exceptions(exc_map):
            with anyio.fail_after(timeout):
                net_stream = await self._backend.connect_tcp(url.host, url.port)
                client = AnyIOHttp2Client(net_stream)
                logger.info("HTTP/2 connection established to %s", net_stream.get_extra_info("remote_address"))
                return client

    async def bind(self, url: URL) -> AnyIOHttp2Server:
        """Binds to the given URL and returns an HTTP/2 server connection."""
        timeout = url.get_param_float(constants.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)

        exc_map: ExceptionMapping = {
            TimeoutError: ConnectTimeout,
        }
        with map_exceptions(exc_map):
            with anyio.fail_after(timeout):
                server = await self._backend.create_tcp_server(local_host=url.host, local_port=url.port)
                logger.info("HTTP/2 server bound to %s", url.location)
                return AnyIOHttp2Server(server)
