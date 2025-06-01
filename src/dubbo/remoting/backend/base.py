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
from os import PathLike
from typing import Any, AnyStr, Callable, Generic, Optional, TypeVar, Union

from dubbo.common.types import HostLike

__all__ = [
    "DEFAULT_MAX_BYTES",
    "SOCKET_OPTION",
    "UDPPacketType",
    "UNIXDatagramPacketType",
    "NetworkStream",
    "StreamHandlerType",
    "UDPDatagramHandlerType",
    "UNIXDatagramHandlerType",
    "NetworkServer",
    "NetworkBackend",
    "AsyncNetworkStream",
    "AsyncStreamHandlerType",
    "AsyncUDPDatagramHandlerType",
    "AsyncUNIXDatagramHandlerType",
    "AsyncNetworkServer",
    "AsyncNetworkBackend",
]

# Default maximum number of bytes to receive in a single operation (65535).
DEFAULT_MAX_BYTES = 2**16 - 1

"""
Type alias for socket options used with setsockopt().

Socket options are tuples containing (level, optname, value) where:
- level: Socket level (e.g., socket.SOL_SOCKET)
- optname: Option name (e.g., socket.SO_REUSEADDR)
- value: Option value (int, bytes, bytearray, or None with length for some options)
"""
SOCKET_OPTION = Union[
    tuple[int, int, int],
    tuple[int, int, Union[bytes, bytearray]],
    tuple[int, int, None, int],
]


# Type alias for UDP datagram packets: (data, (host, port)).
UDPPacketType = tuple[bytes, tuple[str, int]]
# Type alias for Unix datagram packets: (data, path).
UNIXDatagramPacketType = tuple[bytes, str]


_T_Item = TypeVar("_T_Item", bound=Union[bytes, UDPPacketType, UNIXDatagramPacketType])
_T_Stream = TypeVar("_T_Stream")
_T_Handler = TypeVar("_T_Handler", bound=Callable)


class NetworkStream(abc.ABC, Generic[_T_Item]):
    """Abstract base class representing a network stream.

    This class provides a generic interface for network communication streams that can
    handle different types of data items. The type parameter _ItemT determines the format
    of data transmitted through the stream.

    For connection-oriented streams (created via connect_* methods), _ItemT is typically
    bytes representing raw data. For connectionless streams (created via create_* methods),
    _ItemT is a tuple containing (data, address) where data is the payload and address
    identifies the peer endpoint.

    Type Parameters:
        _ItemT: The type of items transmitted through the stream. Can be bytes for
            connection-oriented streams, or tuples like (bytes, address) for
            connectionless protocols.

    Example:
        # For TCP streams.
        stream: NetworkStream[bytes] = backend.connect_tcp("localhost", 8080)
        stream.send(b"Hello")
        response = stream.receive()

        # For UDP streams
        udp_stream: NetworkStream[UDPPacketType] = backend.create_udp()
        udp_stream.send((b"Hello", ("localhost", 8080)))
    """

    @abc.abstractmethod
    def send(self, item: _T_Item, timeout: Optional[float] = None) -> None:
        """Send an item through the network stream.

        Args:
            item (_ItemT): The data item to send. For connection-oriented streams, this is
                typically bytes. For connectionless streams, this is a tuple
                containing (data, address).
            timeout (Optional[float]): Maximum time in seconds to wait for the send operation to
                complete. If None, the operation may block indefinitely.

        Raises:
            SendTimeout: If the operation times out (synchronous backends only).
            SendError: If the send operation fails due to network issues.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def receive(self, *, max_bytes: int = DEFAULT_MAX_BYTES, timeout: Optional[float] = None) -> _T_Item:
        """Receive an item from the network stream.

        Args:
            max_bytes (int): Maximum number of bytes to receive in a single operation.
                Defaults to DEFAULT_MAX_BYTES (65535).
            timeout (Optional[float]): Maximum time in seconds to wait for data to arrive. If None,
                the operation may block indefinitely.

        Returns:
            _ItemT: The received data item. For connection-oriented streams, this is bytes.
            For connectionless streams, this is a tuple containing (data, address).

        Raises:
            ReceiveTimeout: If the operation times out (synchronous backends only).
            ReceiveError: If the reception operation fails due to network issues.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """Close the network stream and release associated resources.

        After calling this method, the stream should not be used for further
        send or receive operations.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_extra_info(self, info: str) -> Any:
        """Get extra information about the network stream.

        Args:
            info (str): The name of the information to retrieve.

        Returns:
            Any: The requested information, or None if not available.
        """
        raise NotImplementedError()


StreamHandlerType = Callable[[NetworkStream[bytes]], None]
UDPDatagramHandlerType = Callable[[NetworkStream[UDPPacketType], UDPPacketType], None]
UNIXDatagramHandlerType = Callable[[NetworkStream[UNIXDatagramPacketType], UNIXDatagramPacketType], None]


class NetworkServer(abc.ABC, Generic[_T_Stream, _T_Handler]):
    """Abstract base class representing a network server.

    This class provides an interface for network servers that can accept and handle
    incoming connections. The server can handle different types of streams and
    uses handler functions to process client connections.

    Type Parameters:
        _StreamT: The type of stream objects created for client connections.
        _HandlerT: The type of handler function used to process connections.

    Example:
        def handle_client(stream: NetworkStream[bytes]) -> None:
            data = stream.receive()
            stream.send(b"Echo: " + data)
            stream.close()

        server = backend.create_tcp_server("localhost", 8080)
        server.serve(handle_client)
    """

    @abc.abstractmethod
    def serve(self, handler: _T_Handler) -> None:
        """Start serving client connections using the provided handler.

        This method begins accepting incoming connections and processes each one
        using the specified handler function. The method typically blocks until
        the server is explicitly closed.

        Args:
            handler (_HandlerT): A callable that processes incoming connections. The exact
                signature depends on the server type (e.g., stream handler for
                TCP, datagram handler for UDP).
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """Stop the server and release associated resources.

        This method stops accepting new connections and closes the server socket.
        Existing connections may continue to be processed until they complete.
        """
        raise NotImplementedError()


class NetworkBackend(abc.ABC):
    """Abstract base class for network backend implementations.

    This class defines the interface for network backends that provide factory methods
    to create network connections and servers. Implementations may use different
    underlying libraries (e.g., standard library socket, asyncio, trio) while
    maintaining a consistent interface.

    The backend supports various network protocols including TCP, UDP, and Unix domain
    sockets, with optional SSL/TLS encryption and custom socket options.

    Example:
        backend = SomeConcreteBackend()

        # Create TCP connection.
        stream = backend.connect_tcp("example.com", 443, ssl_context=ssl_context)

        # Create UDP server.
        server = backend.create_udp_server("localhost", 8080)
        server.serve(handle_udp_packet)
    """

    @abc.abstractmethod
    def connect_tcp(
        self,
        remote_host: HostLike,
        remote_port: int,
        *,
        local_host: Optional[HostLike] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        server_hostname: Optional[str] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
        timeout: Optional[float] = None,
    ) -> NetworkStream[bytes]:
        """Establish a TCP connection to a remote host.

        Args:
            remote_host (HostLike): The hostname or IP address of the remote server.
            remote_port (int): The port number on the remote server.
            local_host (Optional[HostLike]): Local interface to bind to. If None, the system chooses
                an appropriate interface.
            ssl_context (Optional[ssl.SSLContext]): SSL context for encrypted connections. If provided,
                the connection will use TLS/SSL.
            server_hostname (Optional[str]): Expected hostname for SSL certificate verification.
                Required when using SSL with IP addresses.
            socket_options (Optional[Iterable[SOCKET_OPTION]]): List of socket options to apply to the connection.
                Each option is a tuple of (level, optname, value).
            timeout (Optional[float]): Maximum time in seconds to wait for the connection to
                establish. If None, uses system default.

        Returns:
            NetworkStream[bytes]: A NetworkStream for sending and receiving bytes over the TCP connection.

        Raises:
            ConnectTimeout: If the connection times out (synchronous backends only).
            ConnectError: If the connection cannot be established or SSL/TLS
                negotiation fails.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_tcp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> NetworkServer[NetworkStream[bytes], StreamHandlerType]:
        """Create a TCP server that listens for incoming connections.

        Args:
            local_host (HostLike): Local interface to bind the server to. Use "0.0.0.0"
                to bind to all interfaces.
            local_port (int): Port number to listen on. Use 0 to let the system
                choose an available port.
            ssl_context (Optional[ssl.SSLContext]): SSL context for encrypted connections. If provided,
                all client connections will use TLS/SSL.
            reuse_port (bool): Allow multiple sockets to bind to the same port.
                Useful for load balancing across multiple processes.
            reuse_addr (bool): Allow reuse of local addresses. Helpful for quick
                server restarts.

        Returns:
            NetworkServer[NetworkStream[bytes], StreamHandlerType]: A NetworkServer that can accept
            TCP connections and process them with StreamHandlerType functions.

        Raises:
            ConnectError: If the server cannot bind to the specified address/port.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def connect_udp(
        self,
        remote_host: HostLike,
        remote_port: int,
        *,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[bytes]:
        """Create a connected UDP socket for communication with a specific remote endpoint.

        Args:
            remote_host (HostLike): The hostname or IP address of the remote endpoint.
            remote_port (int): The port number on the remote endpoint.
            local_host (Optional[HostLike]): Local interface to bind to. If None, the system chooses
                an appropriate interface.
            local_port (int): Local port to bind to. Use 0 to let the system choose
                an available port.
            socket_options (Optional[Iterable[SOCKET_OPTION]]): List of socket options to apply to the socket.

        Returns:
            NetworkStream[bytes]: A NetworkStream for sending and receiving bytes. Since this is a
            connected UDP socket, send/receive operations work with raw bytes
            rather than (data, address) tuples.

        Raises:
            ConnectError: If the socket cannot be created or connected.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_udp(
        self,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[UDPPacketType]:
        """Create an unconnected UDP socket for sending/receiving datagrams.

        Args:
            local_host (Optional[HostLike]): Local interface to bind to. If None, binds to all
                available interfaces.
            local_port (int): Local port to bind to. Use 0 to let the system choose
                an available port.
            socket_options (Optional[Iterable[SOCKET_OPTION]]): List of socket options to apply to the socket.

        Returns:
            NetworkStream[UDPPacketType]: A NetworkStream that handles UDPPacketType items. Each send/receive
            operation works with (data, address) tuples where address is a
            (host, port) tuple specifying the remote endpoint.

        Raises:
            ConnectError: If the socket cannot be created or bound.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_udp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> NetworkServer[NetworkStream[UDPPacketType], UDPDatagramHandlerType]:
        """Create a UDP server that listens for incoming datagrams.

        Args:
            local_host (HostLike): Local interface to bind the server to. Use "0.0.0.0"
                to bind to all interfaces.
            local_port (int): Port number to listen on. Use 0 to let the system
                choose an available port.
            reuse_port (bool): Allow multiple sockets to bind to the same port.
            reuse_addr (bool): Allow reuse of local addresses.

        Returns:
            NetworkServer[NetworkStream[UDPPacketType], UDPDatagramHandlerType]: A NetworkServer
            that can receive UDP datagrams and process them with UDPDatagramHandlerType
            functions. The handler receives both the stream and the individual datagram packet.

        Raises:
            ConnectError: If the server cannot bind to the specified address/port.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def connect_unix(
        self,
        remote_path: PathLike[AnyStr],
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
        timeout: Optional[float] = None,
    ) -> NetworkStream[bytes]:
        """Establish a connection to a Unix domain socket.

        Args:
            remote_path (PathLike[AnyStr]): Path to the Unix domain socket file on the filesystem.
            socket_options (Optional[Iterable[SOCKET_OPTION]]): List of socket options to apply to the connection.
            timeout (Optional[float]): Maximum time in seconds to wait for the connection to
                establish. If None, uses system default.

        Returns:
            NetworkStream[bytes]: A NetworkStream for sending and receiving bytes over the Unix
            domain socket connection.

        Raises:
            ConnectTimeout: If the connection times out (synchronous backends only).
            ConnectError: If the connection cannot be established.
            FileNotFoundError: If the socket file does not exist.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_unix_server(
        self,
        local_path: PathLike[AnyStr],
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> NetworkServer[NetworkStream[bytes], StreamHandlerType]:
        """Create a Unix domain socket server that listens for incoming connections.

        Args:
            local_path (PathLike[AnyStr]): Path where the Unix domain socket file will be created.
                If the file already exists, it will be removed first.
            ssl_context (Optional[ssl.SSLContext]): SSL context for encrypted connections. If provided,
                all client connections will use TLS/SSL over the Unix socket.

        Returns:
            NetworkServer[NetworkStream[bytes], StreamHandlerType]: A NetworkServer that can accept
            Unix domain socket connections and process them with StreamHandlerType functions.

        Raises:
            ConnectError: If the server cannot bind to the specified path or
                if there are insufficient permissions to create the socket file.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def connect_unix_datagram(
        self,
        remote_path: PathLike[AnyStr],
        *,
        local_path: Optional[PathLike[AnyStr]] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[bytes]:
        """Create a connected Unix datagram socket for communication with a specific endpoint.

        Args:
            remote_path (PathLike[AnyStr]): Path to the remote Unix datagram socket file.
            local_path (Optional[PathLike[AnyStr]]): Local path to bind the socket to. If None, uses an
                anonymous socket.
            socket_options (Optional[Iterable[SOCKET_OPTION]]): List of socket options to apply to the socket.

        Returns:
            NetworkStream[bytes]: A NetworkStream for sending and receiving bytes. Since this is a
            connected datagram socket, send/receive operations work with raw
            bytes rather than (data, path) tuples.

        Raises:
            ConnectError: If the connection cannot be established or if the
                remote socket file does not exist.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_unix_datagram(
        self,
        local_path: Optional[PathLike] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[UNIXDatagramPacketType]:
        """Create an unconnected Unix datagram socket for sending/receiving datagrams.

        Args:
            local_path (Optional[PathLike]): Local path to bind the socket to. If None, creates an
                anonymous socket that can only send datagrams.
            socket_options (Optional[Iterable[SOCKET_OPTION]]): List of socket options to apply to the socket.

        Returns:
            NetworkStream[UNIXDatagramPacketType]: A NetworkStream that handles UNIXDatagramPacketType items. Each
            send/receive operation works with (data, path) tuples where path
            is the filesystem path of the remote endpoint.

        Raises:
            ConnectError: If the socket cannot be created or bound.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_unix_datagram_server(
        self,
        local_path: PathLike[AnyStr],
    ) -> NetworkServer[NetworkStream[UNIXDatagramPacketType], UNIXDatagramHandlerType]:
        """Create a Unix datagram socket server that listens for incoming datagrams.

        Args:
            local_path (PathLike[AnyStr]): Path where the Unix datagram socket file will be created.
                If the file already exists, it will be removed first.

        Returns:
            NetworkServer[NetworkStream[UNIXDatagramPacketType], UNIXDatagramHandlerType]:
                A NetworkServer that can receive Unix datagram packets and process
            them with UNIXDatagramHandlerType functions. The handler receives
            both the stream and the individual datagram packet.

        Raises:
            ConnectError: If the server cannot bind to the specified path.
        """
        raise NotImplementedError()


# ---------------------------------------------------------------
# Asynchronous Interface
# ---------------------------------------------------------------


class AsyncNetworkStream(abc.ABC, Generic[_T_Item]):
    """Asynchronous version of NetworkStream.

    Provides the same interface as NetworkStream but with async/await semantics.
    All operations are non-blocking and must be awaited.
    """

    @abc.abstractmethod
    async def send(self, item: _T_Item) -> None:
        """Asynchronously send an item through the network stream.

        Args:
            item (_ItemT): The data item to send.

        Raises:
            SendError: If the send operation fails due to network issues.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive(self, *, max_bytes: int = DEFAULT_MAX_BYTES) -> _T_Item:
        """Asynchronously receive an item from the network stream.

        Args:
            max_bytes (int): Maximum number of bytes to receive in a single operation.

        Returns:
            _ItemT: The received data item.

        Raises:
            ReceiveError: If the reception operation fails due to network issues.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Asynchronously close the network stream and release associated resources."""
        raise NotImplementedError()

    @abc.abstractmethod
    def get_extra_info(self, info: str) -> Any:
        """Get extra information about the network stream.

        Args:
            info (str): The name of the information to retrieve.

        Returns:
            Any: The requested information, or None if not available.
        """
        raise NotImplementedError()


AsyncStreamHandlerType = Callable[[AsyncNetworkStream[bytes]], Awaitable[None]]
AsyncUDPDatagramHandlerType = Callable[[AsyncNetworkStream[UDPPacketType], UDPPacketType], Awaitable[None]]
AsyncUNIXDatagramHandlerType = Callable[
    [AsyncNetworkStream[UNIXDatagramPacketType], UNIXDatagramPacketType], Awaitable[None]
]


class AsyncNetworkServer(abc.ABC, Generic[_T_Stream, _T_Handler]):
    """Asynchronous version of NetworkServer.

    Provides the same interface as NetworkServer but with async/await semantics.
    """

    @abc.abstractmethod
    async def serve(self, handler: _T_Handler) -> None:
        """Asynchronously start serving client connections using the provided handler.

        Args:
            handler (_HandlerT): An async callable that processes incoming connections.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Asynchronously stop the server and release associated resources."""
        raise NotImplementedError()


class AsyncNetworkBackend(abc.ABC):
    """Asynchronous version of NetworkBackend.

    Provides the same interface as NetworkBackend but with async/await semantics.
    All connection and server creation methods are asynchronous.
    """

    @abc.abstractmethod
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
        """Asynchronous version of NetworkBackend.connect_tcp().

        Raises:
            ConnectError: If the connection cannot be established or SSL/TLS
                negotiation fails.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_tcp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> AsyncNetworkServer[AsyncNetworkStream[bytes], AsyncStreamHandlerType]:
        """Asynchronous version of NetworkBackend.create_tcp_server().

        Raises:
            ConnectError: If the server cannot bind to the specified address/port.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def connect_udp(
        self,
        remote_host: HostLike,
        remote_port: int,
        *,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[bytes]:
        """Asynchronous version of NetworkBackend.connect_udp().

        Raises:
            ConnectError: If the socket cannot be created or connected.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_udp(
        self,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[UDPPacketType]:
        """Asynchronous version of NetworkBackend.create_udp().

        Raises:
            ConnectError: If the socket cannot be created or bound.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_udp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> AsyncNetworkServer[AsyncNetworkStream[UDPPacketType], AsyncUDPDatagramHandlerType]:
        """Asynchronous version of NetworkBackend.create_udp_server().

        Raises:
            ConnectError: If the server cannot bind to the specified address/port.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def connect_unix(
        self,
        remote_path: PathLike[AnyStr],
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[bytes]:
        """Asynchronous version of NetworkBackend.connect_unix().

        Raises:
            ConnectError: If the connection cannot be established or if the
                socket file does not exist.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_unix_server(
        self,
        local_path: PathLike[AnyStr],
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> AsyncNetworkServer[AsyncNetworkStream[bytes], AsyncStreamHandlerType]:
        """Asynchronous version of NetworkBackend.create_unix_server().

        Raises:
            ConnectError: If the server cannot bind to the specified path.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def connect_unix_datagram(
        self,
        remote_path: PathLike[AnyStr],
        *,
        local_path: Optional[PathLike[AnyStr]] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[bytes]:
        """Asynchronous version of NetworkBackend.connect_unix_datagram().

        Raises:
            ConnectError: If the connection cannot be established.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_unix_datagram(
        self,
        local_path: Optional[PathLike] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream[UNIXDatagramPacketType]:
        """Asynchronous version of NetworkBackend.create_unix_datagram().

        Raises:
            ConnectError: If the socket cannot be created or bound.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_unix_datagram_server(
        self,
        local_path: PathLike[AnyStr],
    ) -> AsyncNetworkServer[AsyncNetworkStream[UNIXDatagramPacketType], AsyncUNIXDatagramHandlerType]:
        """Asynchronous version of NetworkBackend.create_unix_datagram_server().

        Raises:
            ConnectError: If the server cannot bind to the specified path.
        """
        raise NotImplementedError()
