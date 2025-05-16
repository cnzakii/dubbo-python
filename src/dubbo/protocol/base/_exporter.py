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
    """
    Exporter interface for exporting services

    This interface provides methods to access the underlying Invoker and
    to unexport the service.
    """

    @abc.abstractmethod
    def get_invoker(self) -> "Invoker":
        """
        Get the invoker of the exported service.

        :return: The invoker of the exported service.
        :rtype: Invoker
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def unexport(self):
        """
        Unexport the service.

        Usually involves destroying or releasing resources related to the exported invoker.
        """
        raise NotImplementedError()


class AsyncExporter(abc.ABC):
    """
    Asynchronous Exporter interface for exporting services.

    Provides async-compatible methods for managing exported services.
    """

    @abc.abstractmethod
    def get_invoker(self) -> "AsyncInvoker":
        """
        Get the async invoker of the exported service.

        :return: The async invoker of the exported service.
        :rtype: AsyncInvoker
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def unexport(self):
        """
        Asynchronously unexport the service.
        """
        raise NotImplementedError()
