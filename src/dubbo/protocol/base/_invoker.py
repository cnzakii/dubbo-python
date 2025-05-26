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
from typing import Generic, TypeVar

from dubbo.common import AsyncNode, Node

if typing.TYPE_CHECKING:
    from ._invocation import Invocation
    from ._result import AsyncResult, Result


__all__ = ["Invoker", "AsyncInvoker"]

_T = TypeVar("_T")


class Invoker(Node, Generic[_T]):
    """Invoker interface for service invocation (API/SPI, Prototype, ThreadSafe).

    This interface extends Node and defines the core method `invoke` to perform
    a service call invocation with the given invocation details.
    """

    @abc.abstractmethod
    def invoke(self, invocation: "Invocation") -> "Result[_T]":
        """Performs the invocation with the provided invocation details.

        Args:
            invocation: The invocation information including method name,
                parameters, and other metadata.

        Returns:
            Result[_T]: The result of the invocation, encapsulating
                response data or errors.

        Raises:
            RpcError: In case of invocation failure or communication errors.
        """
        raise NotImplementedError()


class AsyncInvoker(AsyncNode, Generic[_T]):
    """Asynchronous Invoker interface for service invocation.

    This interface extends AsyncNode to provide an asynchronous version of
    the Invoker interface for non-blocking service invocation.
    """

    @abc.abstractmethod
    async def invoke(self, invocation: "Invocation") -> "AsyncResult[_T]":
        """Performs the asynchronous invocation with the given invocation details.

        Args:
            invocation: The invocation information including method name,
                parameters, and other metadata.

        Returns:
            AsyncResult[_T]: An awaitable result that resolves to the invocation response.

        Raises:
            RpcError: In case of invocation failure or communication errors.
        """
        raise NotImplementedError()
