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
from typing import Awaitable, Callable, Optional, Union

import anyio
from anyio.abc import SocketStream
from anyio.streams.stapled import MultiListener
from anyio.streams.tls import TLSStream

from ._base import DEFAULT_MAX_BYTES, AsyncNetworkBackend, AsyncNetworkStream, AsyncServer
from .exceptions import (
    ConnectError,
    ConnectTimeout,
    ReceiveError,
    ReceiveTimeout,
    SendError,
    SendTimeout,
    map_exc,
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
        """
        Initialize the AnyioNetworkStream instance.

        :param stream: The Anyio stream to wrap.
        :type stream: Any
        """
        self._instance = stream

    async def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        exc_map = {
            TimeoutError: SendTimeout,
            anyio.BrokenResourceError: SendError,
            anyio.ClosedResourceError: SendError,
        }
        with map_exc(exc_map):
            with anyio.fail_after(timeout):
                await self._instance.send(data)

    async def receive(self, max_bytes: int = DEFAULT_MAX_BYTES, timeout: Optional[float] = None) -> bytes:
        exc_map = {
            TimeoutError: ReceiveTimeout,
            anyio.BrokenResourceError: ReceiveError,
            anyio.ClosedResourceError: ReceiveError,
            anyio.EndOfStream: ReceiveError,
        }

        with map_exc(exc_map):
            with anyio.fail_after(timeout):
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

    __slots__ = ("_listener", "_handler")

    _listener: MultiListener[SocketStream]
    _handler: Callable[[AsyncNetworkStream], Awaitable[None]]

    def __aenter__(self):
        return self

    def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.aclose()

    def __init__(self, listener: MultiListener[SocketStream]) -> None:
        """
        Initialize the AnyioServer instance.

        :param listener: The Anyio server to wrap.
        :type listener: MultiListener[SocketStream]
        """
        self._listener = listener

    async def _handler_wrapper(self, stream) -> None:
        """
        A wrapper for the handler to ensure it is called with the correct arguments.

        :param stream: The stream to pass to the handler.
        :type stream: SocketStream
        """
        # Call the user-defined handler with the wrapped stream
        await self._handler(AnyIOStream(stream))

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
        self, remote_host: str, remote_port: int, timeout: Optional[float] = None, local_host: Optional[str] = None
    ) -> AsyncNetworkStream:
        exc_map = {
            TimeoutError: ConnectTimeout,
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }

        with map_exc(exc_map):
            with anyio.fail_after(timeout):
                stream = await anyio.connect_tcp(
                    remote_host=remote_host,
                    remote_port=remote_port,
                    local_host=local_host,
                )
                return AnyIOStream(stream)

    async def create_tcp_server(
        self,
        *,
        local_host: Optional[str] = None,
        local_port: int = 0,
        timeout: Optional[float] = None,
    ) -> AsyncServer:
        exec_map = {
            TimeoutError: ConnectTimeout,
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exc(exec_map):
            with anyio.fail_after(timeout):
                server = await anyio.create_tcp_listener(
                    local_host=local_host,
                    local_port=local_port,
                )
                return AnyIOTCPServer(server)
