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
from typing import Optional

from dubbo.common import URL

from .common import (
    AsyncHttp2ConnectionHandlerType,
    AsyncHttp2StreamHandlerType,
    Http2ConnectionHandlerType,
    Http2StreamHandlerType,
)
from .connections import AsyncHttp2Connection, Http2Connection


class Http2Client(Http2Connection, abc.ABC):
    """
    HTTP/2 client connection.

    Synchronous version of AsyncHttp2Client for client-side operations.

    Example:
        def main():
            with transport.connect(...) as client:
                stream = client.create_stream()
                stream.send_headers([(':method', 'GET'), (':path', '/')])
                response_headers = stream.receive_headers()

        main()
    """

    pass


class Http2Server(abc.ABC):
    """HTTP/2 server implementation."""

    @abc.abstractmethod
    def serve(
        self,
        stream_handler: Http2StreamHandlerType,
        connection_handler: Optional[Http2ConnectionHandlerType] = None,
    ) -> None:
        """Serve incoming connections with the provided handlers.

        Args:
            stream_handler: Callback for each new incoming stream.
            connection_handler: Optional callback for connection events.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """Close the server connection."""
        raise NotImplementedError()


class Http2Transport(abc.ABC):
    """HTTP/2 transport.

    Provides methods to create client and server instances.
    """

    @abc.abstractmethod
    def connect(self, url: URL) -> Http2Client:
        """Connect to a server using the provided URL.

        Args:
            url: Target URL for connection establishment.

        Returns:
            A configured HTTP/2 client connection.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def bind(self, url: URL) -> Http2Server:
        """Bind to a URL for incoming connections.

        Args:
            url: Binding URL for server configuration.

        Returns:
            A configured HTTP/2 server.
        """
        raise NotImplementedError()


class AsyncHttp2Client(AsyncHttp2Connection, abc.ABC):
    """Async HTTP/2 client connection.

    Async version of Http2Client for client-side operations.


    Example:
        async def main():
            async with await transport.connect(...) as client:
                stream = await connection.create_stream()
                await stream.send_headers([(':method', 'GET'), (':path', '/')])
                response_headers = await stream.receive_headers()

        asyncio.run(main())
    """

    pass


class AsyncHttp2Server(abc.ABC):
    """Async HTTP/2 server.

    Async version of Http2Server for handling incoming connections.
    """

    @abc.abstractmethod
    async def serve(
        self,
        stream_handler: AsyncHttp2StreamHandlerType,
        connection_handler: Optional[AsyncHttp2ConnectionHandlerType] = None,
    ) -> None:
        """Async version of serve.

        Args:
            stream_handler: Async callback for each new incoming stream.
            connection_handler: Optional async callback for connection events.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Close the server asynchronously."""
        raise NotImplementedError()


class AsyncHttp2Transport(abc.ABC):
    """Async version of Http2Transport for creating client and server instances."""

    @abc.abstractmethod
    async def connect(self, url: URL) -> AsyncHttp2Client:
        """Async version of connect.

        Args:
            url: Target URL for connection establishment.

        Returns:
            A configured async HTTP/2 client connection.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def bind(self, url: URL) -> AsyncHttp2Server:
        """Async version of bind.

        Args:
            url: Binding URL for server configuration.

        Returns:
            A configured async HTTP/2 server.
        """
        raise NotImplementedError()
