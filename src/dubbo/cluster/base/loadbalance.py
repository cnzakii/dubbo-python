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
from dubbo.protocol import AsyncInvoker, Invocation, Invoker


class LoadBalance(abc.ABC):
    """Base class for load balancing strategies.

    Defines the interface for selecting an invoker from a list of available
    invokers based on the load balancing algorithm implementation.
    """

    @abc.abstractmethod
    def select(self, invokers: list[Invoker], url: URL, invocation: Invocation) -> Optional[Invoker]:
        """Select an invoker from the available invokers list.

        Args:
            invokers: List of available service invokers.
            url: The request URL with configuration parameters.
            invocation: The service invocation context.

        Returns:
            The selected invoker, or None if no suitable invoker is found.
        """
        raise NotImplementedError()


class AsyncLoadBalance(abc.ABC):
    """Base class for asynchronous load balancing strategies.

    Defines the interface for selecting an invoker from a list of available
    invokers based on the load balancing algorithm implementation.
    """

    @abc.abstractmethod
    async def select(self, invokers: list[AsyncInvoker], url: URL, invocation: Invocation) -> Optional[AsyncInvoker]:
        """Asynchronously select an invoker from the available invokers list.

        Args:
            invokers: List of available service invokers.
            url: The request URL with configuration parameters.
            invocation: The service invocation context.

        Returns:
            The selected asynchronous invoker, or None if no suitable invoker is found.
        """
        raise NotImplementedError()
