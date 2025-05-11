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
from typing import Awaitable, Callable, Optional

from h2.config import H2Configuration

from dubbo import logger
from dubbo.common import URL, constant
from dubbo.remoting.backend import AnyIOBackend, AsyncNetworkBackend, AsyncNetworkStream, AsyncServer

from ._connection import AnyIOHttp2Connection
from ._stream import AnyIOHttp2Stream

_LOGGER = logger.get_instance()

_DEFAULT_CONNECTION_TIMEOUT = 10  # seconds


class AnyIOHttp2Client(AnyIOHttp2Connection):
    """
    An HTTP/2 client connection using AnyIO.
    """

    def __init__(self, net_stream: AsyncNetworkStream):
        super().__init__(net_stream, H2Configuration(client_side=True, validate_inbound_headers=False))

    async def start(self) -> None:
        await self.__aenter__()

    async def stop(self) -> None:
        await self.__aexit__(None, None, None)


class AnyIOHttp2Server:
    """
    An HTTP/2 server connection using AnyIO.
    """

    _server: AsyncServer
    _handle_connection: Optional[Callable[[AnyIOHttp2Connection], Awaitable[None]]]
    _handle_stream: Optional[Callable[[AnyIOHttp2Stream], Awaitable[None]]]

    def __init__(self, server: AsyncServer) -> None:
        self._server = server
        self._handle_connection = None
        self._handle_stream = None

    async def _net_stream_wrapper(self, stream: AsyncNetworkStream) -> None:
        """
        Wrap the stream with the HTTP/2 connection and handle incoming requests.
        """
        async with AnyIOHttp2Connection(
            net_stream=stream,
            h2_config=H2Configuration(client_side=False, validate_inbound_headers=False),
            stream_handler=self._handle_stream,
        ) as conn:
            if self._handle_connection:
                await self._handle_connection(conn)

            # wait until the connection is closed
            await conn.wait_until_closed()
            _LOGGER.debug("HTTP/2 connection closed")

    async def serve(
        self,
        stream_handler: Callable[[AnyIOHttp2Stream], Awaitable[None]],
        connection_handler: Optional[Callable[[AnyIOHttp2Connection], Awaitable[None]]] = None,
    ) -> None:
        """
        Start the server and listen for incoming connections.
        """
        self._handle_connection = connection_handler
        self._handle_stream = stream_handler
        await self._server.serve(self._net_stream_wrapper)


class AnyIOHttp2Transport:
    """
    An HTTP/2 transport using AnyIO.
    """

    __slots__ = ("_backend",)

    _backend: AsyncNetworkBackend

    def __init__(self):
        self._backend = AnyIOBackend()

    async def connect(self, url: URL) -> AnyIOHttp2Client:
        """
        Connect to the given URL and return an HTTP/2 client connection.

        :param url: URL object containing host, port, and query parameters
        :return: An established AnyIOHttp2Client connection
        :raises ConnectTimeout: if connection times out
        :raises ConnectError: for other connection errors
        """
        timeout = url.get_param_float(constant.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)

        net_stream = await self._backend.connect_tcp(url.host, url.port, timeout=timeout)
        _LOGGER.info(f"HTTP/2 connection established to {url.location}")
        return AnyIOHttp2Client(net_stream)

    async def bind(self, url: URL) -> AnyIOHttp2Server:
        """
        Bind to the given URL and return an HTTP/2 server connection.

        :param url: URL object containing host, port, and query parameters
        :return: An established AnyIOHttp2Server connection
        """
        timeout = url.get_param_float(constant.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)
        server = await self._backend.create_tcp_server(local_host=url.host, local_port=url.port, timeout=timeout)
        _LOGGER.info(f"HTTP/2 server established to {url.location}")
        return AnyIOHttp2Server(server)
