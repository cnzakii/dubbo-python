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
import functools
import socket
import ssl
import time
from collections.abc import Iterable
from socketserver import BaseRequestHandler, ThreadingTCPServer
from typing import Any, Callable, Optional, Union

from _base import DEFAULT_MAX_BYTES, SOCKET_OPTION, NetworkBackend, NetworkStream

from dubbo.common.types import IPAddressType
from dubbo.common.utils import network as net_utils

from ._base import Server
from .exceptions import (
    ConnectError,
    ConnectTimeout,
    ExceptionMapping,
    ReceiveError,
    ReceiveTimeout,
    SendError,
    SendTimeout,
    map_exceptions,
)

__all__ = [
    "SyncStream",
    "SyncTCPServer",
    "SyncBackend",
]


class SyncStream(NetworkStream):
    """
    A synchronous implementation of the NetworkStream interface using a blocking socket.
    """

    __slots__ = ("_instance",)

    _instance: Union[socket.socket, ssl.SSLSocket]

    def __init__(self, instance: Union[socket.socket, ssl.SSLSocket]) -> None:
        self._instance = instance

    def __enter__(self) -> "SyncStream":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _with_timeout(self, timeout: Optional[float], op: Callable[[], Any]) -> Any:
        """
        Execute a socket operation with a temporary timeout.

        :param timeout: Timeout in seconds for the operation.
        :param op: Operation to perform.
        :return: The result of the operation.
        """
        original_timeout = self._instance.gettimeout()
        try:
            self._instance.settimeout(timeout)
            return op()
        finally:
            self._instance.settimeout(original_timeout)

    def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        """
        Send data to the connected peer.

        :param data: Data bytes to send.
        :param timeout: Optional timeout in seconds.
        :raises SendTimeout: If the send operation times out.
        :raises SendError: If another socket error occurs.
        """
        if not data:
            return
        exc_map: ExceptionMapping = {socket.timeout: SendTimeout, OSError: SendError}
        with map_exceptions(exc_map):
            self._with_timeout(timeout, lambda: self._instance.sendall(data))

    def receive(self, max_bytes: int = DEFAULT_MAX_BYTES, timeout: Optional[float] = None) -> bytes:
        """
        Receive data from the socket.

        :param max_bytes: Maximum number of bytes to read.
        :param timeout: Optional timeout in seconds.
        :return: The received bytes.
        :raises ReceiveTimeout: If the reception operation times out.
        :raises ReceiveError: If another socket error occurs.
        """
        exc_map: ExceptionMapping = {socket.timeout: ReceiveTimeout, OSError: ReceiveError}
        with map_exceptions(exc_map):
            return self._with_timeout(timeout, lambda: self._instance.recv(max_bytes))

    def close(self) -> None:
        """
        Close the network stream.
        """
        try:
            self._instance.close()
        except OSError:
            pass


class PlaceholderHandler(BaseRequestHandler):
    """
    A placeholder request handler that does nothing.
    Used as a stub before a real handler is assigned.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def handle(self):
        pass


class SyncTCPHandler(BaseRequestHandler):
    """
    A request handler for the synchronous TCP server.

    Wraps the raw socket in a SyncStream and delegates handling
    to a user-defined function.
    """

    _handler: Callable[[NetworkStream], None]
    _ssl_context: Optional[ssl.SSLContext]
    _net_stream: Optional[SyncStream]

    def __init__(
        self, handler: Callable[[NetworkStream], None], ssl_context: Optional[ssl.SSLContext], *args: Any, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._ssl_context = ssl_context
        self._handler = handler
        self._net_stream = None

    def setup(self) -> None:
        """
        Set up the request by optionally wrapping the socket with SSL and initializing the SyncNetworkStream.
        """
        _socket = self.request
        exc_map: ExceptionMapping = {
            socket.timeout: ConnectTimeout,
            OSError: ConnectError,
        }
        if self._ssl_context:
            with map_exceptions(exc_map):
                _socket = self._ssl_context.wrap_socket(
                    _socket,
                    server_side=True,
                )
        self._net_stream = SyncStream(_socket)

    def handle(self) -> None:
        """
        Handle the connection using the provided handler.
        """
        assert self._net_stream is not None
        with self._net_stream:
            self._handler(self._net_stream)


class SyncTCPServer(Server):
    """
    A synchronous TCP server using threading to handle multiple client connections concurrently.
    """

    __slots__ = ("_server", "_ssl_context", "_handler")

    _server: ThreadingTCPServer
    _ssl_context: Optional[ssl.SSLContext]
    _handler: Optional[Callable[[NetworkStream], None]]

    def __init__(self, server: ThreadingTCPServer, ssl_context: Optional[ssl.SSLContext] = None) -> None:
        self._server = server
        self._ssl_context = ssl_context
        self._handler = None

    def serve(self, handler: Callable[[NetworkStream], None]) -> None:
        """
        Start the server and begin handling incoming TCP connections.

        :param handler: A callable to handle each new connection.
        """
        self._handler = handler
        func = functools.partial(SyncTCPHandler, self._handler, self._ssl_context)
        self._server.RequestHandlerClass = func
        self._server.serve_forever()

    def close(self) -> None:
        """
        Shut down the server and release any resources.
        """
        self._server.shutdown()
        self._server.server_close()


class SyncBackend(NetworkBackend):
    """
    A synchronous backend for establishing TCP connections and servers.
    """

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
        Connect to a remote TCP server.

        :param remote_host: Remote host IP or hostname.
        :param remote_port: Remote port number.
        :param local_host: Optional local host for binding.
        :param ssl_context: Optional SSL context for secure connection.
        :param ssl_server_hostname: Server hostname for SSL validation.
        :param socket_options: Optional list of socket options.
        :param timeout: Optional timeout for connection.
        :return: A SyncNetworkStream instance.
        """
        socket_options = socket_options or []
        remote_address = (str(remote_host), remote_port)
        local_address = (local_host, 0) if local_host else None

        exc_map: ExceptionMapping = {
            socket.timeout: ConnectTimeout,
            OSError: ConnectError,
        }

        start_time = time.time()
        with map_exceptions(exc_map):
            sock = socket.create_connection(
                remote_address,
                timeout,
                source_address=local_address,
            )
            for option in socket_options:
                sock.setsockopt(*option)  # type: ignore[arg-type]

            if ssl_context:
                timeout = timeout - (time.time() - start_time) if timeout else None
                try:
                    sock.settimeout(timeout)
                    sock = ssl_context.wrap_socket(
                        sock,
                        server_hostname=ssl_server_hostname,
                    )
                finally:
                    sock.settimeout(None)

        return SyncStream(sock)

    def create_tcp_server(
        self,
        local_host: IPAddressType,
        local_port: int,
        *,
        ssl_context: Optional[ssl.SSLContext] = None,
        reuse_port: bool = False,
        reuse_addr: bool = False,
    ) -> Server:
        local_address = (str(local_host), local_port)

        exc_map: ExceptionMapping = {
            OSError: ConnectError,
        }

        with map_exceptions(exc_map):
            server = ThreadingTCPServer(
                local_address,
                PlaceholderHandler,  # Placeholder handler, will be replaced later
                bind_and_activate=False,
            )

            # If the local address is IPv6, set the address family to AF_INET6
            if net_utils.is_valid_ipv6(local_address[0]):
                server.address_family = socket.AF_INET6
                server.socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)

            # Set the socket options
            server.allow_reuse_address = reuse_addr
            server.allow_reuse_port = reuse_port

            # Bind the server to the address and activate it
            try:
                server.server_bind()
                server.server_activate()
            except Exception as e:
                server.server_close()
                raise e

            return SyncTCPServer(server)
