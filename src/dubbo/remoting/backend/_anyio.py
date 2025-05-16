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
import ssl
from collections.abc import Awaitable, Iterable
from typing import Callable, Optional, Union

import anyio
from anyio.abc import SocketStream
from anyio.streams.stapled import MultiListener
from anyio.streams.tls import TLSStream

from dubbo.common.types import IPAddressType

from ._base import DEFAULT_MAX_BYTES, SOCKET_OPTION, AsyncNetworkBackend, AsyncNetworkStream, AsyncServer
from .exceptions import (
    ConnectError,
    ExceptionMapping,
    ReceiveError,
    SendError,
    map_exceptions,
)


class AnyIOStream(AsyncNetworkStream):
    """
    An implementation of AsyncNetworkStream using Anyio.
    """

    __slots__ = ("_instance",)

    _instance: Union[SocketStream, TLSStream]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._instance.aclose()

    def __init__(self, stream: Union[SocketStream, TLSStream]) -> None:
        self._instance = stream

    async def send(self, data: bytes) -> None:
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: SendError,
            anyio.ClosedResourceError: SendError,
        }
        with map_exceptions(exc_map):
            await self._instance.send(data)

    async def receive(self, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: ReceiveError,
            anyio.ClosedResourceError: ReceiveError,
            anyio.EndOfStream: ReceiveError,
        }

        with map_exceptions(exc_map):
            return await self._instance.receive(max_bytes=max_bytes)

    async def aclose(self) -> None:
        """
        Asynchronously close the network stream.
        """
        await self._instance.aclose()


class AnyIOTCPServer(AsyncServer):
    """
    An implementation of AsyncServer using Anyio.
    """

    __slots__ = ("_listener", "_ssl_context", "_handler")

    _listener: MultiListener[SocketStream]
    _ssl_context: Optional[ssl.SSLContext]
    _handler: Optional[Callable[[AsyncNetworkStream], Awaitable[None]]]

    def __aenter__(self):
        return self

    def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.aclose()

    def __init__(self, listener: MultiListener[SocketStream], ssl_context: Optional[ssl.SSLContext] = None) -> None:
        self._listener = listener
        self._ssl_context = ssl_context
        self._handler = None

    async def _handler_wrapper(self, stream) -> None:
        """
        A wrapper for the handler to ensure it is called with the correct arguments.

        :param stream: The stream to pass to the handler.
        :type stream: SocketStream
        """
        try:
            if self._ssl_context:
                # wrap the stream in a TLSStream if ssl_context is provided
                stream = await TLSStream.wrap(
                    stream,
                    ssl_context=self._ssl_context,
                    server_side=True,
                )
            # Call the user-defined handler with the wrapped stream
            assert self._handler is not None
            await self._handler(AnyIOStream(stream))
        except Exception as exc:
            await stream.aclose()
            raise exc

    async def serve(self, handler: Callable[[AsyncNetworkStream], Awaitable[None]]) -> None:
        self._handler = handler
        await self._listener.serve(self._handler_wrapper)

    async def aclose(self) -> None:
        """
        Asynchronously close the server.
        """
        await self._listener.aclose()


class AnyIOBackend(AsyncNetworkBackend):
    """
    An implementation of NetworkBackend using Anyio.
    """

    async def connect_tcp(
        self,
        remote_host: IPAddressType,
        remote_port: int,
        *,
        local_host: Optional[IPAddressType] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        ssl_server_hostname: Optional[str] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream:
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: ConnectError,
            anyio.EndOfStream: ConnectError,
            ssl.SSLError: ConnectError,
        }

        with map_exceptions(exc_map):
            stream: Union[SocketStream, TLSStream]
            # Create a TCP connection
            stream = await anyio.connect_tcp(
                remote_host=remote_host,
                remote_port=remote_port,
                local_host=local_host,
            )

            # Set socket options
            for option in socket_options:
                stream._raw_socket.setsockopt(*option)  # type: ignore[arg-type]

            # Use TLS if ssl_context is provided
            if ssl_context:
                try:
                    stream = await TLSStream.wrap(
                        stream, ssl_context=ssl_context, hostname=ssl_server_hostname, server_side=False
                    )
                except Exception as exc:
                    await stream.aclose()
                    raise exc

            return AnyIOStream(stream)

    async def create_tcp_server(
        self,
        local_host: IPAddressType,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> AsyncServer:
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            # `reuse_addr` is True by default in anyio and cannot be set to False
            server = await anyio.create_tcp_listener(
                local_host=local_host,
                local_port=local_port,
                reuse_port=reuse_port,
            )
            return AnyIOTCPServer(server, ssl_context=ssl_context)
