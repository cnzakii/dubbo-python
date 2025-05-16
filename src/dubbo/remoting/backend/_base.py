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
import abc
import ssl
from collections.abc import Awaitable, Iterable
from typing import Callable, Optional, Union

from dubbo.common.types import IPAddressType

__all__ = [
    "DEFAULT_MAX_BYTES",
    "SOCKET_OPTION",
    "NetworkStream",
    "Server",
    "NetworkBackend",
    "AsyncNetworkStream",
    "AsyncServer",
    "AsyncNetworkBackend",
]

DEFAULT_MAX_BYTES = 2 ^ 16 - 1

SOCKET_OPTION = Union[
    tuple[int, int, int],
    tuple[int, int, Union[bytes, bytearray]],
    tuple[int, int, None, int],
]


class NetworkStream(abc.ABC):
    """
    Abstract base class representing a network stream.

    Defines the interface for sending, receiving, and closing a connection,
    with optional support for timeouts.
    """

    @abc.abstractmethod
    def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        """
        Send data over the stream.

        :param data: Data to send.
        :param timeout: Optional timeout in seconds.
        :raises SendTimeout: If the send operation times out.
        :raises SendError: If there is an error during sending.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def receive(self, max_bytes: int = DEFAULT_MAX_BYTES, timeout: Optional[float] = None) -> bytes:
        """
        Receive data from the stream.

        :param max_bytes: Maximum number of bytes to read.
        :param timeout: Optional timeout in seconds.
        :return: Received bytes.
        :raises ReceiveTimeout: If the reception operation times out.
        :raises ReceiveError: If there is an error during receiving.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """
        Close the stream and release any associated resources.

        :raises NotImplementedError: Must be implemented by subclass.
        """
        raise NotImplementedError()


class Server(abc.ABC):
    """
    Abstract base class representing a TCP server.

    Defines methods to start serving client connections and to shut down the server.
    """

    @abc.abstractmethod
    def serve(self, handler: Callable[[NetworkStream], None]) -> None:
        """
        Start serving incoming client connections.

        :param handler: A callable that handles each client as a NetworkStream.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """
        Close the server and free any associated resources.
        """
        raise NotImplementedError()


class NetworkBackend(abc.ABC):
    """
    Abstract base class for a network backend implementation.

    Provides factory methods to establish TCP connections or create TCP servers,
    potentially using SSL and configurable socket options.
    """

    @abc.abstractmethod
    def connect_tcp(
        self,
        remote_host: IPAddressType,
        remote_port: int,
        *,
        local_host: Optional[IPAddressType] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        ssl_server_hostname: Optional[str] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
        timeout: Optional[float] = None,
    ) -> NetworkStream:
        """
        Connect to a remote TCP host.

        :param remote_host: Remote host IP address or hostname.
        :param remote_port: Remote port number.
        :param local_host: Optional local address to bind the socket to.
        :param ssl_context: Optional SSL context for encrypted connections.
        :param ssl_server_hostname: Hostname for SSL validation (SNI).
        :param socket_options: List of socket option tuples.
        :param timeout: Optional connection timeout.
        :return: An instance of NetworkStream.
        :raises ConnectTimeout: If the connection times out.
        :raises ConnectError: If there is an error during connection.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_tcp_server(
        self,
        local_host: IPAddressType,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> Server:
        """
        Create a TCP server bound to a local address.

        :param local_host: Hostname or IP address to bind.
        :param local_port: Port number to listen on.
        :param ssl_context: Optional SSL context for secure communication.
        :param reuse_port: Whether to allow multiple sockets to bind to the same port.
        :param reuse_addr: Whether to allow address reuse.
        :return: An instance of Server.
        :raises ConnectError: If there is an error during server creation.
        """
        raise NotImplementedError()


# ---------------------------------------------------------------
# Asynchronous Interface
# ---------------------------------------------------------------


class AsyncNetworkStream:
    """
    Asynchronous version of NetworkStream.
    """

    @abc.abstractmethod
    async def send(self, data: bytes) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive(self, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        raise NotImplementedError()


class AsyncServer(abc.ABC):
    """
    Asynchronous version of Server.
    """

    @abc.abstractmethod
    async def serve(self, handler: Callable[[AsyncNetworkStream], Awaitable[None]]) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        raise NotImplementedError()


class AsyncNetworkBackend:
    """
    Asynchronous version of NetworkBackend.
    """

    @abc.abstractmethod
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
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_tcp_server(
        self,
        local_host: IPAddressType,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> AsyncServer:
        raise NotImplementedError()
