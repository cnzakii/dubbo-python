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
from collections.abc import Awaitable
from typing import Callable

from dubbo.common import URL, AsyncNode, Node

__all__ = ["Registry", "AsyncRegistry", "NotifyListenerType", "AsyncNotifyListenerType"]

NotifyListenerType = Callable[[list[URL]], None]
AsyncNotifyListenerType = Callable[[list[URL]], Awaitable[None]]


class Registry(Node, abc.ABC):
    """
    The base class for all registry implementations.
    """

    @abc.abstractmethod
    def register(self, url: URL) -> None:
        """
        Register a URL to the registry.

        Args:
            url (URL): The URL to register.
        """
        pass

    @abc.abstractmethod
    def unregister(self, url: URL) -> None:
        """
        Unregister a URL from the registry.

        Args:
            url (URL): The URL to unregister.
        """
        pass

    @abc.abstractmethod
    def subscribe(self, url: URL, listener: NotifyListenerType) -> None:
        """
        Subscribe to a URL in the registry.

        Args:
            url (URL): The URL to subscribe.
            listener (NotifyListenerType): The listener to notify when the URL changes.
        """
        pass

    @abc.abstractmethod
    def unsubscribe(self, url: URL, listener: NotifyListenerType) -> None:
        """
        Unsubscribe from a URL in the registry.

        Args:
            url (URL): The URL to unsubscribe.
            listener (NotifyListenerType): The listener to remove.
        """
        pass


class AsyncRegistry(AsyncNode, abc.ABC):
    """
    The base class for all asynchronous registry implementations.
    """

    @abc.abstractmethod
    async def initialize(self) -> None:
        """
        Asynchronously initialize the registry.
        This method should be called before any other operations.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def register(self, url: URL) -> None:
        """Asynchronously register a URL to the registry."""
        pass

    @abc.abstractmethod
    async def unregister(self, url: URL) -> None:
        """Asynchronously unregister a URL from the registry."""
        pass

    @abc.abstractmethod
    async def subscribe(self, url: URL, listener: AsyncNotifyListenerType) -> None:
        """Asynchronously subscribe to a URL in the registry."""
        pass

    @abc.abstractmethod
    async def unsubscribe(self, url: URL, listener: AsyncNotifyListenerType) -> None:
        """Asynchronously unsubscribe from a URL in the registry."""
        pass
