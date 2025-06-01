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
from contextlib import AsyncExitStack
from os import PathLike
from typing import Any, AnyStr, Callable, Generic, Optional, TypeVar, Union

import anyio
from anyio import abc as anyio_abc
from anyio.streams.tls import TLSListener, TLSStream

from dubbo.common.types import HostLike
from dubbo.exceptions import ExceptionMapping, map_exceptions

from .base import (
    DEFAULT_MAX_BYTES,
    SOCKET_OPTION,
    AsyncNetworkBackend,
    AsyncNetworkServer,
    AsyncNetworkStream,
    AsyncStreamHandlerType,
    AsyncUDPDatagramHandlerType,
    AsyncUNIXDatagramHandlerType,
    UDPPacketType,
    UNIXDatagramPacketType,
)
from .exceptions import (
    ConnectError,
    ReceiveError,
    SendError,
)

__all__ = [
    "AnyIOStream",
    "AnyIODatagramStream",
    "AnyIOStreamServer",
    "AnyIODatagramServer",
    "AnyIOBackend",
]

_T_Item = TypeVar("_T_Item", bound=Union[bytes, UDPPacketType, UNIXDatagramPacketType])
_T_DatagramHandler = Callable[[AsyncNetworkStream[_T_Item], _T_Item], Awaitable[None]]


class AnyIOStream(AsyncNetworkStream[bytes]):
    """Asynchronous NetworkStream implementation using AnyIO ByteStream.

    Provides async send/receive operations with TLS support.
    """

    __slots__ = ("_stream",)

    _stream: anyio_abc.ByteStream

    def __init__(self, stream: anyio_abc.ByteStream) -> None:
        """Initialize with AnyIO ByteStream."""
        self._stream = stream

    async def send(self, data: bytes) -> None:
        """Send bytes data asynchronously."""
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: SendError,
            anyio.ClosedResourceError: SendError,
        }
        with map_exceptions(exc_map):
            await self._stream.send(data)

    async def receive(self, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
        """Receive bytes data asynchronously."""
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: ReceiveError,
            anyio.ClosedResourceError: ReceiveError,
            anyio.EndOfStream: ReceiveError,
        }

        with map_exceptions(exc_map):
            return await self._stream.receive(max_bytes=max_bytes)

    async def aclose(self) -> None:
        """Close stream resources asynchronously."""
        await self._stream.aclose()

    def get_extra_info(self, info: str) -> Any:
        """Get socket address information."""
        if info == "local_address":
            return self._stream.extra(anyio_abc.SocketAttribute.local_address, None)
        elif info == "remote_address":
            return self._stream.extra(anyio_abc.SocketAttribute.remote_address, None)
        else:
            return None


class AnyIODatagramStream(AsyncNetworkStream[_T_Item], Generic[_T_Item]):
    """Asynchronous datagram stream using AnyIO UnreliableObjectStream.

    Handles UDP and Unix datagram packet transmission.
    """

    __slots__ = ("_stream",)

    _stream: anyio_abc.UnreliableObjectStream[_T_Item]

    def __init__(self, stream: anyio_abc.UnreliableObjectStream[_T_Item]) -> None:
        """Initialize with AnyIO UnreliableObjectStream."""
        self._stream = stream

    async def send(self, item: _T_Item) -> None:
        """Send datagram item asynchronously."""
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: SendError,
            anyio.ClosedResourceError: SendError,
        }
        with map_exceptions(exc_map):
            await self._stream.send(item)

    async def receive(self, *, max_bytes: int = DEFAULT_MAX_BYTES) -> _T_Item:
        """Receive datagram item asynchronously.

        Note: max_bytes parameter is ignored for AnyIO UnreliableObjectStream.
        """
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: ReceiveError,
            anyio.ClosedResourceError: ReceiveError,
            anyio.EndOfStream: ReceiveError,
        }

        with map_exceptions(exc_map):
            return await self._stream.receive()

    async def aclose(self) -> None:
        """Close datagram stream resources asynchronously."""
        await self._stream.aclose()

    def get_extra_info(self, info: str) -> Any:
        """Get socket address information."""
        if info == "local_address":
            return self._stream.extra(anyio_abc.SocketAttribute.local_address, None)
        elif info == "remote_address":
            return self._stream.extra(anyio_abc.SocketAttribute.remote_address, None)
        else:
            return None


class AnyIOStreamServer(AsyncNetworkServer[AsyncNetworkStream[bytes], AsyncStreamHandlerType]):
    """Asynchronous TCP server using AnyIO Listener with automatic connection handling."""

    __slots__ = ("_listener", "_handler")

    _listener: anyio_abc.Listener[anyio_abc.SocketStream]
    _handler: Optional[Callable[[AsyncNetworkStream], Awaitable[None]]]

    def __init__(self, listener: anyio_abc.Listener[anyio_abc.SocketStream]) -> None:
        """Initialize with AnyIO listener."""
        self._listener = listener
        self._handler = None

    async def _handler_wrapper(self, stream: anyio_abc.ByteStream) -> None:
        """Wrap handler to ensure proper resource cleanup on exceptions.

        Args:
            stream: The raw AnyIO ByteStream to wrap and pass to handler.
        """
        try:
            assert self._handler is not None, "Handler must be set before serving"
            await self._handler(AnyIOStream(stream))
        except Exception:
            await stream.aclose()
            raise

    async def serve(self, handler: AsyncStreamHandlerType) -> None:
        """Start serving with the specified handler (blocks indefinitely)."""
        self._handler = handler
        await self._listener.serve(self._handler_wrapper)

    async def aclose(self) -> None:
        """Close server and release resources."""
        await self._listener.aclose()


class AnyIODatagramServer(
    AsyncNetworkServer[AsyncNetworkStream[_T_Item], _T_DatagramHandler[_T_Item]], Generic[_T_Item]
):
    """Asynchronous datagram server using AnyIO with concurrent connection handling."""

    __slots__ = ("_stream",)

    _stream: AnyIODatagramStream[_T_Item]

    def __init__(self, stream: anyio_abc.UnreliableObjectStream[_T_Item]) -> None:
        """Initialize with AnyIO UnreliableObjectStream."""
        self._stream: AnyIODatagramStream[_T_Item] = AnyIODatagramStream(stream)

    async def serve(self, handler: _T_DatagramHandler[_T_Item]) -> None:
        """Start serving with concurrent handler execution for each datagram."""
        async with AsyncExitStack() as stack:
            task_group = await stack.enter_async_context(anyio.create_task_group())
            try:
                while True:
                    item = await self._stream.receive()
                    task_group.start_soon(handler, self._stream, item)
            except Exception:
                await self.aclose()
                raise

    async def aclose(self) -> None:
        """Close datagram server resources (idempotent)."""
        try:
            await self._stream.aclose()
        except anyio.BrokenResourceError:
            # Stream already closed, ignore
            pass


class AnyIOBackend(AsyncNetworkBackend):
    """Asynchronous network backend using AnyIO library.

    Provides async TCP, UDP, and Unix domain socket operations with SSL/TLS support.
    """

    async def connect_tcp(
        self,
        remote_host: HostLike,
        remote_port: int,
        *,
        local_host: Optional[HostLike] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        server_hostname: Optional[str] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[bytes]:
        """Establish TCP connection with optional TLS."""
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: ConnectError,
            anyio.EndOfStream: ConnectError,
            ssl.SSLError: ConnectError,
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            stream = await anyio.connect_tcp(
                remote_host=remote_host,
                remote_port=remote_port,
                local_host=local_host,
            )

            # Apply socket options before TLS
            for option in socket_options:
                stream._raw_socket.setsockopt(*option)  # type: ignore[arg-type]

            # Apply TLS if requested
            if ssl_context:
                stream = await TLSStream.wrap(
                    stream,
                    server_side=False,
                    hostname=server_hostname or str(remote_host),
                    ssl_context=ssl_context,
                    standard_compatible=False,
                )  # type: ignore[assignment]

            return AnyIOStream(stream)

    async def create_tcp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> AsyncNetworkServer[AsyncNetworkStream[bytes], AsyncStreamHandlerType]:
        """Create TCP server with optional TLS (reuse_addr ignored - always True in AnyIO)."""
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            listener = await anyio.create_tcp_listener(
                local_host=local_host,
                local_port=local_port,
                reuse_port=reuse_port,
            )
            if ssl_context:
                listener = TLSListener(listener, ssl_context=ssl_context, standard_compatible=False)  # type: ignore[assignment]

            return AnyIOStreamServer(listener)

    async def connect_udp(
        self,
        remote_host: HostLike,
        remote_port: int,
        *,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[bytes]:
        """Create connected UDP socket (sends raw bytes)."""
        socket_options = socket_options or []

        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: ConnectError,
            OSError: ConnectError,
        }
        with map_exceptions(exc_map):
            stream = await anyio.create_connected_udp_socket(
                remote_host=remote_host,
                remote_port=remote_port,
                local_host=local_host,
                local_port=local_port,
            )
            # Apply socket options
            for option in socket_options:
                stream._raw_socket.setsockopt(*option)  # type: ignore[arg-type]

            return AnyIODatagramStream(stream)

    async def create_udp(
        self,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[UDPPacketType]:
        """Create unconnected UDP socket (sends/receives packet tuples)."""
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            anyio.BrokenResourceError: ConnectError,
            OSError: ConnectError,
        }
        with map_exceptions(exc_map):
            stream = await anyio.create_udp_socket(
                local_host=local_host,
                local_port=local_port,
            )
            # Apply socket options
            for option in socket_options:
                stream._raw_socket.setsockopt(*option)  # type: ignore[arg-type]
            return AnyIODatagramStream(stream)

    async def create_udp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> AsyncNetworkServer[AsyncNetworkStream[UDPPacketType], AsyncUDPDatagramHandlerType]:
        """Create UDP server for handling datagrams (reuse_addr ignored - always True in AnyIO)."""
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            stream = await anyio.create_udp_socket(
                local_host=local_host,
                local_port=local_port,
                reuse_port=reuse_port,
            )
            return AnyIODatagramServer(stream)

    async def connect_unix(
        self,
        remote_path: PathLike[AnyStr],
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[bytes]:
        """Connect to Unix domain socket."""
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            stream = await anyio.connect_unix(remote_path)
            for option in socket_options:
                stream._raw_socket.setsockopt(*option)  # type: ignore[arg-type]
            return AnyIOStream(stream)

    async def create_unix_server(
        self,
        local_path: PathLike[AnyStr],
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> AsyncNetworkServer[AsyncNetworkStream[bytes], AsyncStreamHandlerType]:
        """Create Unix domain socket server with optional TLS."""
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            listener = await anyio.create_unix_listener(path=local_path)

            if ssl_context:
                listener = TLSListener(
                    listener,
                    ssl_context=ssl_context,
                    standard_compatible=False,
                )  # type: ignore[assignment]

            return AnyIOStreamServer(listener)

    async def connect_unix_datagram(
        self,
        remote_path: PathLike[AnyStr],
        *,
        local_path: Optional[PathLike[AnyStr]] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[bytes]:
        """Connect to Unix datagram socket (sends raw bytes)."""
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            stream = await anyio.create_connected_unix_datagram_socket(
                remote_path=remote_path,
                local_path=local_path,
            )
            for option in socket_options:
                stream._raw_socket.setsockopt(*option)  # type: ignore[arg-type]
            return AnyIODatagramStream(stream)

    async def create_unix_datagram(
        self,
        local_path: Optional[PathLike] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[UNIXDatagramPacketType]:
        """Create unconnected Unix datagram socket (sends/receives packet tuples)."""
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            stream = await anyio.create_unix_datagram_socket(local_path=local_path)
            for option in socket_options:
                stream._raw_socket.setsockopt(*option)  # type: ignore[arg-type]
            return AnyIODatagramStream(stream)

    async def create_unix_datagram_server(
        self,
        local_path: PathLike[AnyStr],
    ) -> AsyncNetworkServer[AsyncNetworkStream[UNIXDatagramPacketType], AsyncUNIXDatagramHandlerType]:
        """Create Unix datagram socket server."""
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
            anyio.BrokenResourceError: ConnectError,
        }
        with map_exceptions(exc_map):
            stream = await anyio.create_unix_datagram_socket(local_path=local_path)
            return AnyIODatagramServer(stream)
