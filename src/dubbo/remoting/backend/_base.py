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

__all___ = ["NetworkStream", "NetWorkBackend", "AsyncNetworkStream", "AsyncNetworkBackend"]

import ssl

from typing import Optional


class BaseNetworkStream(abc.ABC):
    """
    BaseNetworkStream is an abstract base class for all network streams.
    """

    @property
    @abc.abstractmethod
    def local_addr(self) -> tuple:
        """
        Get the local address of the network stream.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def remote_addr(self) -> tuple:
        """
        Get the remote address of the network stream.
        """
        raise NotImplementedError()


class NetworkStream(BaseNetworkStream, abc.ABC):
    """
    NetworkStream is an abstract base class for synchronous network streams.
    """

    def __enter__(self):
        """
        Enter the runtime context related to this object.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit the runtime context related to this object.
        """
        self.close()

    @abc.abstractmethod
    def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        """
        Send data over the network stream.

        :param data: The data to send.
        :param timeout: Optional timeout for the send operation.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def receive(self, max_size: int, timeout: Optional[float] = None) -> bytes:
        """
        Receive data from the network stream.

        :param max_size: The maximum size of data to receive.
        :param timeout: Optional timeout for the reception operation.
        :return: The received data.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """
        Close the network stream.
        """
        raise NotImplementedError()


class NetWorkBackend(abc.ABC):
    """
    NetworkBackend is an abstract base class for synchronous network backends.
    """

    pass


class AsyncNetworkStream(BaseNetworkStream, abc.ABC):
    """
    AsyncNetworkStream is an abstract base class for asynchronous network streams.
    """

    async def __aenter__(self):
        """
        Enter the asynchronous runtime context related to this object.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Exit the asynchronous runtime context related to this object.
        """
        await self.aclose()

    @abc.abstractmethod
    async def send(self, data: bytes, timeout: Optional[float] = None) -> None:
        """
        Send data over the network stream.

        :param data: The data to send.
        :param timeout: Optional timeout for the send operation.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive(self, max_size: int, timeout: Optional[float] = None) -> bytes:
        """
        Receive data from the network stream.

        :param max_size: The maximum size of data to receive.
        :param timeout: Optional timeout for the reception operation.
        :return: The received data.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """
        Close the network stream.
        """
        raise NotImplementedError()


class AsyncServer(abc.ABC):
    """
    AsyncServer is an abstract base class for asynchronous network servers.
    """

    pass


class AsyncNetworkBackend(abc.ABC):
    """
    AsyncNetworkBackend is an abstract base class for asynchronous network backends.
    """

    pass
