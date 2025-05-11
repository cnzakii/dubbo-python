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
from typing import Awaitable, Callable, Optional

DEFAULT_MAX_BYTES = 2 ^ 16 - 1


class AsyncNetworkStream:
    """
    AsyncNetworkStream is an abstract base class for asynchronous network streams.
    """

    @abc.abstractmethod
    async def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        """
        Asynchronously send data to the network stream.

        :param data: The data to send.
        :type data: bytes
        :param timeout: The timeout for the send operation.
        :type timeout: Optional[float]
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive(self, max_bytes: int = DEFAULT_MAX_BYTES, timeout: Optional[float] = None) -> bytes:
        """
        Asynchronously receive data from the network stream.

        :param max_bytes: The maximum number of bytes to receive.
        :type max_bytes: int
        :param timeout: The timeout for the reception operation.
        :type timeout: Optional[float]
        :return: The received data.
        :rtype: bytes
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """
        Asynchronously close the network stream.
        """
        raise NotImplementedError()


class AsyncServer(abc.ABC):
    """
    AsyncServer is an abstract base class for asynchronous servers.
    """

    @abc.abstractmethod
    async def serve(self, handler: Callable[[AsyncNetworkStream], Awaitable[None]]) -> None:
        """
        Start serving client connections asynchronously.
        This should typically block until the server is closed.

        :param handler: A callable that will handle incoming connections.
        :type handler: Callable[[AsyncNetworkStream], Awaitable[None]]
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """
        Asynchronously close the server.
        """
        raise NotImplementedError()


class AsyncNetworkBackend:
    """
    AsyncNetworkBackend is an abstract base class for asynchronous network backends.
    """

    @abc.abstractmethod
    async def connect_tcp(
        self, remote_host: str, remote_port: int, timeout: Optional[float] = None, local_host: Optional[str] = None
    ) -> AsyncNetworkStream:
        """
        Asynchronously connect to a TCP server.

        :param remote_host: The host of the server.
        :type remote_host: str
        :param remote_port: The port of the server.
        :type remote_port: int
        :param timeout: The timeout for the connection operation.
        :type timeout: Optional[float]
        :param local_host: The local host to bind to.
        :type local_host: Optional[str]
        :return: An AsyncNetworkStream object representing the connection.
        :rtype: AsyncNetworkStream
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_tcp_server(
        self,
        *,
        local_host: Optional[str] = None,
        local_port: int = 0,
        timeout: Optional[float] = None,
    ) -> AsyncServer:
        raise NotImplementedError()
