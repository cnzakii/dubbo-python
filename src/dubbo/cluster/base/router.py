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

from dubbo.common import URL
from dubbo.protocol import AsyncInvoker, Invocation, Invoker


class Router(abc.ABC):
    """Router interface for service invocation"""

    @property
    @abc.abstractmethod
    def priority(self) -> int:
        """Get the priority of the router."""
        raise NotImplementedError()

    @abc.abstractmethod
    def route(self, invokers: list[Invoker], url: URL, invocation: Invocation) -> list[Invoker]:
        """Route the invokers based on the URL and invocation."""
        raise NotImplementedError()

    @abc.abstractmethod
    def get_url(self) -> URL:
        """Get the URL associated with this router."""
        raise NotImplementedError()

    @abc.abstractmethod
    def notify(self, invokers: list[Invoker]) -> None:
        """Notify the router of changes in the invoker list."""
        raise NotImplementedError()


class RouterFactory(abc.ABC):
    """RouterFactory"""

    @abc.abstractmethod
    def get_router(self, url: URL) -> Router:
        """Get the router."""
        raise NotImplementedError()


class AsyncRouter(abc.ABC):
    """Asynchronous Router interface for service invocation"""

    @property
    @abc.abstractmethod
    def priority(self) -> int:
        """Get the priority of the router."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def route(self, invokers: list[AsyncInvoker], url: URL, invocation: Invocation) -> list[AsyncInvoker]:
        """Asynchronously route the invokers based on the URL and invocation."""
        raise NotImplementedError()

    @abc.abstractmethod
    def get_url(self) -> URL:
        """Get the URL associated with this router."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def notify(self, invokers: list[AsyncInvoker]) -> None:
        """Notify the router of changes in the invoker list."""
        raise NotImplementedError()


class AsyncRouterFactory(abc.ABC):
    """AsyncRouterFactory"""

    @abc.abstractmethod
    def get_router(self, url: URL) -> AsyncRouter:
        """Get the router."""
        raise NotImplementedError()
