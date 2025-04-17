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
import socketserver
import ssl
from typing import Any, Callable, Optional, Union

from ...loggers import loggerFactory
from ..types import IPAddressType
from ._base import NetWorkBackend, NetworkStream

_logger = loggerFactory.get_logger()


class SyncTCPStream(NetworkStream):
    _sock: Union[socket.socket, ssl.SSLSocket]

    def __init__(self, sock: Union[socket.socket, ssl.SSLSocket]):
        self._sock = sock

    @property
    def local_addr(self) -> tuple:
        """
        Get the local address of the network stream.
        """
        return self._sock.getsockname()

    @property
    def remote_addr(self) -> tuple:
        """
        Get the remote address of the network stream.
        """
        return self._sock.getpeername()

    def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        if timeout:
            self._sock.settimeout(timeout)
        self._sock.sendall(data)

    def receive(self, max_size: int, timeout: Optional[float] = None) -> bytes:
        if timeout:
            self._sock.settimeout(timeout)

        return self._sock.recv(max_size)

    def close(self) -> None:
        self._sock.close()


class SyncTCPServer(socketserver.TCPServer):
    """
    A synchronous TCP server that uses a handler to process incoming connections.
    This server can be used with or without SSL.
    """

    def __init__(
        self,
        handler: Callable[[NetworkStream], Any],
        local_host: IPAddressType,
        local_port: int,
        ssl_context: Optional[ssl.SSLContext],
        family: socket.AddressFamily,
        backlog: int,
        reuse_port: bool,
        bind_and_activate: bool = True,
    ):
        self.raw_handler = handler
        self.ssl_context = ssl_context
        self.address_family = family
        self.request_queue_size = backlog
        self.allow_reuse_address = reuse_port
        server_address = (str(local_host), local_port)
        super().__init__(server_address, self._handler_wrapper, bind_and_activate)

    def _handler_wrapper(self, request, client_address, server):
        """
        Wrap the handler to convert the request to SyncTCPStream.
        """
        return SyncTCPServer.HandlerWrapper(self.raw_handler, request, client_address, server)

    def server_bind(self):
        """
        Bind the server to the specified address and port.
        """
        super().server_bind()
        _logger.info(f"Server bound to {self.server_address[0]}:{self.server_address[1]}")

    def get_request(self) -> tuple[Union[socket.socket, ssl.SSLSocket], tuple[str, int]]:
        """
        Get a request from the socket.
        """
        sock, addr = super().get_request()
        # Wrap the socket with SSL if an SSL context is provided
        if self.ssl_context:
            sock = self.ssl_context.wrap_socket(sock, server_side=True)
        return sock, addr

    class HandlerWrapper(socketserver.BaseRequestHandler):
        """
        A wrapper for the handler to convert the request to SyncTCPStream.
        """

        def __init__(self, handler: Callable[[NetworkStream], Any], request, client_address, server):
            # It must be set the handler attribute before calling the super constructor
            # because the super constructor will call the handle method
            self.raw_handler = handler
            super().__init__(request, client_address, server)

        def handle(self):
            """
            Handle the incoming connection.
            """
            self.raw_handler(SyncTCPStream(self.request))


class SyncBackend(NetWorkBackend):
    def connect_tcp(
        self,
        host: IPAddressType,
        port: int,
        ssl_context: Optional[ssl.SSLContext] = None,
        timeout: Optional[float] = None,
    ) -> NetworkStream:
        host_str = str(host)

        sock = socket.socket()
        if timeout:
            sock.settimeout(timeout)
        sock.connect((host_str, port))

        # Wrap the socket with SSL if an SSL context is provided
        if ssl_context:
            sock = ssl_context.wrap_socket(sock, server_hostname=host_str)

        return SyncTCPStream(sock)

    def listen_tcp(
        self,
        handler: Callable[[NetworkStream], Any],
        local_host: Optional[IPAddressType] = None,
        local_port: int = 0,
        ssl_context: Optional[ssl.SSLContext] = None,
        family: socket.AddressFamily = socket.AF_INET,
        backlog: int = 65535,
        reuse_port: bool = False,
    ) -> Any:
        local_host = socket.gethostname() if local_host is None else local_host
        return SyncTCPServer(handler, local_host, local_port, ssl_context, family, backlog, reuse_port)
