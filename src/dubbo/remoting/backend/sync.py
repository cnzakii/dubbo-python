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
import sys
from collections.abc import Iterable
from os import PathLike
from socketserver import BaseRequestHandler, ThreadingTCPServer, ThreadingUDPServer
from typing import Any, AnyStr, Callable, Generic, Optional, TypeVar, Union, cast

from dubbo.common.types import HostLike
from dubbo.common.utils import network as net_utils
from dubbo.exceptions import ExceptionMapping, map_exceptions

from .base import (
    DEFAULT_MAX_BYTES,
    SOCKET_OPTION,
    NetworkBackend,
    NetworkServer,
    NetworkStream,
    StreamHandlerType,
    UDPDatagramHandlerType,
    UDPPacketType,
    UNIXDatagramHandlerType,
    UNIXDatagramPacketType,
)
from .exceptions import (
    ConnectError,
    ConnectTimeout,
    ReceiveError,
    ReceiveTimeout,
    SendError,
    SendTimeout,
)

__all__ = [
    "SyncStream",
    "SyncStreamServer",
    "SyncDatagramServer",
    "SyncBackend",
]

# Generic type variables for stream and datagram handling
_ItemT = TypeVar("_ItemT", bound=Union[bytes, UDPPacketType, UNIXDatagramPacketType])
_DatagramT = TypeVar("_DatagramT", bound=Union[UDPPacketType, UNIXDatagramPacketType])
_DatagramHandlerT = Callable[[NetworkStream[_DatagramT], _DatagramT], None]


def _with_timeout(sock: socket.socket, timeout: Optional[float], op: Callable[[], Any]) -> Any:
    """Execute socket operation with timeout, optimized to avoid unnecessary changes."""
    original_timeout = sock.gettimeout()
    # Only set timeout if it's different from current timeout
    if timeout != original_timeout:
        try:
            sock.settimeout(timeout)
            return op()
        finally:
            sock.settimeout(original_timeout)
    else:
        return op()


def _wrapper_tls(
    ssl_context: ssl.SSLContext,
    sock: socket.socket,
    *,
    server_hostname: Optional[str] = None,
    server_side: bool = False,
    timeout: Optional[float] = None,
) -> ssl.SSLSocket:
    """Wrap socket in SSL context for TLS connections."""
    if isinstance(sock, ssl.SSLSocket):
        raise TypeError("The provided socket is already an SSLSocket.")

    return _with_timeout(
        sock,
        timeout,
        lambda: ssl_context.wrap_socket(sock, server_hostname=server_hostname, server_side=server_side),
    )


class SyncStream(NetworkStream[_ItemT], Generic[_ItemT]):
    """Synchronous NetworkStream implementation using blocking sockets.

    Supports both connected (TCP) and unconnected (UDP/Unix datagram) modes.
    """

    __slots__ = ("_sock", "_connected")

    def __init__(self, sock: socket.socket, connected: bool = True) -> None:
        """Initialize with socket and connection mode."""
        self._sock = sock
        self._connected = connected

    def _do_send(self, item: _ItemT) -> None:
        """Perform send operation based on connection mode."""
        if self._connected:
            assert isinstance(item, bytes), "Connected streams require bytes data"
            self._sock.sendall(item)
        else:
            assert isinstance(item, tuple), "Unconnected streams require (data, address) tuple"
            data, addr = item
            self._sock.sendto(data, addr)

    def send(self, item: _ItemT, timeout: Optional[float] = None) -> None:
        """Send item with optional timeout."""
        exc_map: ExceptionMapping = {socket.timeout: SendTimeout, OSError: SendError}
        with map_exceptions(exc_map):
            _with_timeout(self._sock, timeout, lambda: self._do_send(item))

    def _do_receive(self, max_bytes: int) -> _ItemT:
        """Perform receive operation based on connection mode."""
        if self._connected:
            return cast(_ItemT, self._sock.recv(max_bytes))
        else:
            return cast(_ItemT, self._sock.recvfrom(max_bytes))

    def receive(self, *, max_bytes: int = DEFAULT_MAX_BYTES, timeout: Optional[float] = None) -> _ItemT:
        """Receive item with optional timeout."""
        exc_map: ExceptionMapping = {socket.timeout: ReceiveTimeout, OSError: ReceiveError}
        with map_exceptions(exc_map):
            return _with_timeout(self._sock, timeout, lambda: self._do_receive(max_bytes))

    def close(self) -> None:
        """Close socket resources (idempotent)."""
        try:
            self._sock.close()
        except OSError:
            # Socket may already be closed
            pass

    def get_extra_info(self, info: str) -> Any:
        """Get socket address information."""
        try:
            if info == "local_address":
                return self._sock.getsockname()
            elif info == "remote_address":
                return self._sock.getpeername()
        except OSError:
            # Socket may not be connected or closed
            return None
        return None


class PlaceholderHandler(BaseRequestHandler):
    """Placeholder handler used during server initialization."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def handle(self) -> None:
        """No-op handler implementation."""
        pass


class SyncStreamServer(NetworkServer[NetworkStream[bytes], StreamHandlerType], ThreadingTCPServer):
    """Synchronous TCP server using ThreadingTCPServer with optional TLS support."""

    __slots__ = ("_handler", "_ssl_context")

    def __init__(
        self,
        server_address: Any,
        *,
        family: socket.AddressFamily,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> None:
        self._handler: Optional[StreamHandlerType] = None
        self._ssl_context = ssl_context
        self.allow_reuse_port = reuse_port
        self.allow_reuse_address = reuse_addr
        self.address_family = family

        # Initialize ThreadingTCPServer
        ThreadingTCPServer.__init__(self, server_address, PlaceholderHandler, bind_and_activate=True)

    def finish_request(self, request: Union[socket.socket, tuple[bytes, socket.socket]], client_address: Any) -> None:
        """Process incoming TCP connection with optional TLS wrapping."""
        assert isinstance(request, socket.socket), "Expected socket object for TCP connections"
        sock: socket.socket = request

        # Apply TLS wrapping if configured
        if self._ssl_context:
            try:
                sock = _wrapper_tls(self._ssl_context, request, server_side=True)
            except Exception:
                # Close original socket if TLS setup fails
                try:
                    request.close()
                except OSError:
                    pass
                raise

        # Create stream and call handler
        net_stream: SyncStream[bytes] = SyncStream(sock)

        assert self._handler is not None, "Stream handler must be set before serving"
        try:
            self._handler(net_stream)
        finally:
            # Ensure stream cleanup even if handler raises
            net_stream.close()

    def serve(self, handler: StreamHandlerType) -> None:
        """Start serving with the specified handler (blocks indefinitely)."""
        self._handler = handler
        self.serve_forever()

    def close(self) -> None:
        """Shutdown server and release resources."""
        self.shutdown()
        self.server_close()


class SyncDatagramServer(
    NetworkServer[NetworkStream[_DatagramT], _DatagramHandlerT[_DatagramT]], ThreadingUDPServer, Generic[_DatagramT]
):
    """Synchronous UDP/Unix datagram server using ThreadingUDPServer."""

    __slots__ = ("_handler",)

    def __init__(
        self, server_address: Any, *, reuse_port: bool = False, reuse_addr: bool = False, family: socket.AddressFamily
    ) -> None:
        self._handler: Optional[_DatagramHandlerT[_DatagramT]] = None
        self.allow_reuse_port = reuse_port
        self.allow_reuse_address = reuse_addr
        self.address_family = family

        # Initialize ThreadingUDPServer
        ThreadingUDPServer.__init__(self, server_address, PlaceholderHandler, bind_and_activate=True)

    def finish_request(self, request: Union[socket.socket, tuple[bytes, socket.socket]], client_address: Any) -> None:
        """Process incoming datagram request."""
        assert isinstance(request, tuple), "Expected tuple (data, socket) for datagram connections"
        data, sock = request

        # Create datagram stream and call handler
        net_stream: NetworkStream[_DatagramT] = SyncStream(sock, connected=False)

        assert self._handler is not None, "Datagram handler must be set before serving"
        self._handler(net_stream, (data, client_address))  # type: ignore

    def serve(self, handler: _DatagramHandlerT[_DatagramT]) -> None:
        """Start serving with the specified handler (blocks indefinitely)."""
        self._handler = handler
        self.serve_forever()

    def close(self) -> None:
        """Shutdown server and release resources."""
        self.shutdown()
        self.server_close()


class SyncBackend(NetworkBackend):
    """Synchronous network backend using standard Python sockets.

    All operations are blocking. Supports TCP, UDP, and Unix domain sockets
    with optional SSL/TLS for stream connections.
    """

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
        """Establish TCP connection with optional TLS."""
        socket_options = socket_options or []
        remote_address = (str(remote_host), remote_port)
        local_address = (str(local_host), 0) if local_host else None

        exc_map: ExceptionMapping = {
            socket.timeout: ConnectTimeout,
            OSError: ConnectError,
            ssl.SSLError: ConnectError,
        }

        with map_exceptions(exc_map):
            sock = socket.create_connection(
                remote_address,
                timeout=timeout,
                source_address=local_address,
            )

            # Apply socket options
            for option in socket_options:
                sock.setsockopt(*option)  # type: ignore[arg-type]

            # Apply TLS if requested
            if ssl_context:
                sock = _wrapper_tls(
                    ssl_context,
                    sock,
                    server_hostname=server_hostname or str(remote_host),
                    server_side=False,
                    timeout=timeout,
                )
            return SyncStream(sock)

    def create_tcp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> NetworkServer[NetworkStream[bytes], StreamHandlerType]:
        """Create TCP server with optional TLS."""
        local_address = (str(local_host), local_port)

        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            return SyncStreamServer(
                family=net_utils.get_address_family(local_host),
                server_address=local_address,
                reuse_port=reuse_port,
                reuse_addr=reuse_addr,
                ssl_context=ssl_context,
            )

    def connect_udp(
        self,
        remote_host: HostLike,
        remote_port: int,
        *,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[bytes]:
        """Create connected UDP socket (sends raw bytes)."""
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            family = net_utils.get_address_family(remote_host)
            sock = socket.socket(family, socket.SOCK_DGRAM)

            try:
                # Bind to local address if specified
                if local_host is not None or local_port != 0:
                    local_addr = (str(local_host) if local_host else "", local_port)
                    sock.bind(local_addr)

                # Apply socket options
                for option in socket_options:
                    sock.setsockopt(*option)  # type: ignore[arg-type]

                # Connect UDP socket
                sock.connect((str(remote_host), remote_port))

                return SyncStream(sock)
            except Exception:
                sock.close()
                raise

    def create_udp(
        self,
        local_host: Optional[HostLike] = None,
        local_port: int = 0,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[UDPPacketType]:
        """Create unconnected UDP socket (sends/receives packet tuples)."""
        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            family = socket.AF_INET
            if local_host is not None:
                family = net_utils.get_address_family(local_host)

            sock = socket.socket(family, socket.SOCK_DGRAM)

            try:
                # Bind to local address if specified
                if local_host is not None or local_port != 0:
                    local_addr = (str(local_host) if local_host else "", local_port)
                    sock.bind(local_addr)

                # Apply socket options
                for option in socket_options:
                    sock.setsockopt(*option)  # type: ignore[arg-type]

                return SyncStream[UDPPacketType](sock, connected=False)
            except Exception:
                sock.close()
                raise

    def create_udp_server(
        self,
        local_host: HostLike,
        local_port: int,
        *,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> NetworkServer[NetworkStream[UDPPacketType], UDPDatagramHandlerType]:
        """Create UDP server for handling datagrams."""
        local_address = (str(local_host), local_port)

        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            return SyncDatagramServer[UDPPacketType](
                family=net_utils.get_address_family(local_host),
                server_address=local_address,
                reuse_port=reuse_port,
                reuse_addr=reuse_addr,
            )

    def connect_unix(
        self,
        remote_path: PathLike[AnyStr],
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
        timeout: Optional[float] = None,
    ) -> NetworkStream[bytes]:
        """Connect to Unix domain socket (blocking operation)."""
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets not supported on Windows")

        socket_options = socket_options or []

        exc_map: ExceptionMapping = {
            socket.timeout: ConnectTimeout,
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                if timeout is not None:
                    sock.settimeout(timeout)

                # Apply socket options
                for option in socket_options:
                    sock.setsockopt(*option)  # type: ignore[arg-type]

                sock.connect(str(remote_path))
                return SyncStream[bytes](sock)
            except Exception:
                sock.close()
                raise

    def create_unix_server(
        self,
        local_path: PathLike[AnyStr],
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> NetworkServer[NetworkStream[bytes], StreamHandlerType]:
        """Create Unix domain socket server with optional TLS."""
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets not supported on Windows")

        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            return SyncStreamServer(
                family=socket.AF_UNIX,
                server_address=str(local_path),
                reuse_port=False,
                reuse_addr=False,
                ssl_context=ssl_context,
            )

    def connect_unix_datagram(
        self,
        remote_path: PathLike[AnyStr],
        *,
        local_path: Optional[PathLike[AnyStr]] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[bytes]:
        """Connect to Unix datagram socket (sends raw bytes)."""
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets not supported on Windows")

        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

            try:
                # Bind to local path if specified
                if local_path:
                    sock.bind(str(local_path))

                # Apply socket options
                for option in socket_options:
                    sock.setsockopt(*option)  # type: ignore[arg-type]

                # Connect to the remote path
                sock.connect(str(remote_path))

                return SyncStream[bytes](sock)
            except Exception:
                sock.close()
                raise

    def create_unix_datagram(
        self,
        local_path: Optional[PathLike] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> NetworkStream[UNIXDatagramPacketType]:
        """Create unconnected Unix datagram socket (sends/receives packet tuples)."""
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets not supported on Windows")

        socket_options = socket_options or []
        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

            try:
                # Bind to local path if specified
                if local_path:
                    sock.bind(str(local_path))

                # Apply socket options
                for option in socket_options:
                    sock.setsockopt(*option)  # type: ignore[arg-type]

                return SyncStream[UNIXDatagramPacketType](sock, connected=False)
            except Exception:
                sock.close()
                raise

    def create_unix_datagram_server(
        self,
        local_path: PathLike[AnyStr],
    ) -> NetworkServer[NetworkStream[UNIXDatagramPacketType], UNIXDatagramHandlerType]:
        """Create Unix datagram socket server."""
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets not supported on Windows")

        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            server = SyncDatagramServer[UNIXDatagramPacketType](
                server_address=str(local_path),
                reuse_port=False,
                reuse_addr=False,
                family=socket.AF_UNIX,
            )
            return server
