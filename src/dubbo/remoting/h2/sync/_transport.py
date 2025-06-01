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
from typing import Optional

from h2.config import H2Configuration

from dubbo.common import URL, constants
from dubbo.common.types import TypeAlias
from dubbo.logger import logger
from dubbo.remoting.backend import NetworkBackend, NetworkServer, NetworkStream, StreamHandlerType, SyncBackend
from dubbo.remoting.h2 import (
    Http2Client,
    Http2ConnectionHandlerType,
    Http2Server,
    Http2StreamHandlerType,
    Http2Transport,
)

from ._connection import SyncHttp2Connection

__all__ = ["SyncHttp2Client", "SyncHttp2Server", "SyncHttp2Transport"]

_ServerType: TypeAlias = NetworkServer[NetworkStream[bytes], StreamHandlerType]


class SyncHttp2Client(Http2Client, SyncHttp2Connection):
    def __init__(self, net_stream: NetworkStream):
        super().__init__(net_stream, H2Configuration(client_side=True, validate_inbound_headers=False))

    def __enter__(self) -> "SyncHttp2Client":
        SyncHttp2Connection.__enter__(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        SyncHttp2Connection.__exit__(self, exc_type, exc_value, traceback)


class SyncHttp2Server(Http2Server):
    __slots__ = ("_server", "_connection_handler", "_stream_handler")

    _server: _ServerType
    _connection_handler: Optional[Http2ConnectionHandlerType]
    _stream_handler: Optional[Http2StreamHandlerType]

    def __init__(self, server: _ServerType) -> None:
        self._server = server
        self._connection_handler = None
        self._stream_handler = None

    def _net_stream_wrapper(self, stream: NetworkStream) -> None:
        """
        Wrap the stream with the HTTP/2 connection and handle incoming requests.
        """
        with SyncHttp2Connection(
            net_stream=stream,
            h2_config=H2Configuration(client_side=False, validate_inbound_headers=False),
            stream_handler=self._stream_handler,
        ) as conn:
            logger.debug("New HTTP/2 connection established: %s", stream.get_extra_info("remote_address"))
            if self._connection_handler:
                self._connection_handler(conn)

            # wait until the connection is closed
            conn.wait_until_closed()
            logger.debug("HTTP/2 Connection closed: %s", stream.get_extra_info("remote_address"))

    def serve(
        self,
        stream_handler: Http2StreamHandlerType,
        connection_handler: Optional[Http2ConnectionHandlerType] = None,
    ) -> None:
        """
        Start the server and listen for incoming connections.
        """
        self._connection_handler = connection_handler
        self._stream_handler = stream_handler
        self._server.serve(self._net_stream_wrapper)


_DEFAULT_CONNECTION_TIMEOUT = 10.0  # seconds


class SyncHttp2Transport(Http2Transport):
    """
    An HTTP/2 transport using synchronous I/O.
    """

    __slots__ = ("_backend",)

    _backend: NetworkBackend

    def __init__(self):
        self._backend = SyncBackend()

    def connect(self, url: URL) -> SyncHttp2Client:
        """Connect to the given URL and return an HTTP/2 client connection."""
        timeout = url.get_param_float(constants.TIMEOUT_KEY, _DEFAULT_CONNECTION_TIMEOUT)

        net_stream = self._backend.connect_tcp(url.host, url.port, timeout=timeout)
        client = SyncHttp2Client(net_stream)
        logger.info("HTTP/2 connection established to %s", net_stream.get_extra_info("remote_address"))
        return client

    def bind(self, url: URL) -> Http2Server:
        """Bind to the given URL and return an HTTP/2 server connection."""
        server = self._backend.create_tcp_server(local_host=url.host, local_port=url.port)
        logger.info("HTTP/2 server bound to %s", url.location)
        return SyncHttp2Server(server)
