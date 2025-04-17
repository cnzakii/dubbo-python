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

import socket
import ssl
from ssl import SSLContext
from typing import Any, Callable, Optional, Union

import anyio
from anyio.abc import SocketStream, SocketAttribute
from anyio.streams.tls import TLSListener, TLSStream

from ..types import IPAddressType
from ._base import AsyncNetworkBackend, AsyncNetworkStream


class AnyIOTCPStream(AsyncNetworkStream):
    """
    AnyIOTCPStream is an asynchronous network stream implementation using AnyIO.
    """

    _socket: Union[SocketStream, TLSStream]

    def __init__(self, stream: Union[SocketStream, TLSStream]) -> None:
        self._socket = stream

    @property
    def local_addr(self) -> tuple:
        return self._socket.extra(SocketAttribute.local_address)

    @property
    def remote_addr(self) -> tuple:
        return self._socket.extra(SocketAttribute.remote_address)

    async def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        with anyio.fail_after(timeout):
            await self._socket.send(data)

    async def receive(self, max_size: int, timeout: Optional[float] = None) -> bytes:
        with anyio.fail_after(timeout):
            try:
                return await self._socket.receive(max_size)
            except anyio.EndOfStream:
                # It means no more data can be read from the stream.
                return b""

    async def aclose(self) -> None:
        await self._socket.aclose()


class AnyIOTCPServer:
    def __init__(self, listener, handler: Callable[[AsyncNetworkStream], Any]) -> None:
        self.listener = listener
        self.raw_handler = handler

    async def _wrap_handler(self, stream: SocketStream) -> None:
        """
        Wrap the handler to convert the stream to AnyIOTCPStream.
        """
        stream = AnyIOTCPStream(stream)
        await self.raw_handler(stream)

    async def serve_forever(self):
        """
        Serve the TCP server forever.
        """
        await self.listener.serve(self._wrap_handler)


class AnyIOBackend(AsyncNetworkBackend):
    async def connect_tcp(
        self,
        host: IPAddressType,
        port: int,
        ssl_context: Optional[SSLContext] = None,
        timeout: Optional[float] = None,
    ) -> AnyIOTCPStream:
        """
        Connect to a TCP server.
        """
        with anyio.fail_after(timeout):
            stream = await anyio.connect_tcp(host, port, ssl_context=ssl_context)
            return AnyIOTCPStream(stream)

    async def listen_tcp(
        self,
        handler: Callable[[AsyncNetworkStream], Any],
        local_host: Optional[IPAddressType] = None,
        local_port: int = 0,
        ssl_context: Optional[ssl.SSLContext] = None,
        family: socket.AddressFamily = socket.AddressFamily.AF_UNSPEC,
        backlog: int = 65535,
        reuse_port: bool = False,
    ) -> Any:
        """
        Listen for incoming TCP connections.
        """
        listener = await anyio.create_tcp_listener(
            local_host=local_host, local_port=local_port, family=family, backlog=backlog, reuse_port=reuse_port
        )
        if ssl_context:
            listener = TLSListener(listener, ssl_context)
        return AnyIOTCPServer(listener, handler)
