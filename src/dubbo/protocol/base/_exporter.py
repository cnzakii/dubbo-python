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
import typing

if typing.TYPE_CHECKING:
    from ._invoker import AsyncInvoker, Invoker

__all__ = ["Exporter", "AsyncExporter"]


class Exporter(abc.ABC):
    """Interface for managing exported services.

    Handles the lifecycle of exposed services and provides access
    to the underlying invoker for service invocation.
    """

    @abc.abstractmethod
    def get_invoker(self) -> "Invoker":
        """Get the invoker of the exported service.

        Returns:
            Invoker for the current exported service
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def unexport(self):
        """Unexport the service and release resources.

        This method should clean up all resources associated with
        the exported service, such as network connections and allocated
        memory.
        """
        raise NotImplementedError()


class AsyncExporter(abc.ABC):
    """Asynchronous interface for managing exported services.

    Provides the same functionality as Exporter but with asynchronous
    support for non-blocking operations in asynchronous contexts.
    """

    @abc.abstractmethod
    def get_invoker(self) -> "AsyncInvoker":
        """Get the async invoker of the exported service.

        Returns:
            AsyncInvoker for the current exported service
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def unexport(self):
        """Asynchronously unexport the service and release resources.

        Non-blocking version of the unexport operation that can be
        awaited in asynchronous contexts.
        """
        raise NotImplementedError()
