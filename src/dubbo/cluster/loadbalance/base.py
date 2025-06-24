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
import time
from typing import Optional, TypeVar, Union

from dubbo.common import URL, constants
from dubbo.protocol import AsyncInvoker, Invocation, Invoker

from ..base import AsyncLoadBalance, LoadBalance

__all__ = ["BaseLoadBalance", "BaseAsyncLoadBalance", "AsyncLoadBalance", "get_weight"]

_T_Invoker = TypeVar("_T_Invoker", bound=Union[Invoker, AsyncInvoker])


def get_weight(invoker: _T_Invoker, invocation: Invocation) -> int:
    """Get the weight of the invoker with warmup capability.

    Calculates weight based on URL parameters and warmup time. New services
    need warmup time to be fully functional, during which their weight is
    gradually increased from 1 to the configured value.

    Args:
        invoker: The service invoker containing URL configuration.
        invocation: The service invocation context with method information.

    Returns:
        Calculated weight value, minimum 0.
    """
    url = invoker.get_url()
    # TODO: Multiple registry scenario, load balance among multiple registries.
    is_multiple = False

    # Determine weight based on URL parameters
    if is_multiple:
        weight = url.get_param_int(constants.WEIGHT_KEY, constants.DEFAULT_WEIGHT_VALUE)
    else:
        # Get the weight from the url
        weight = url.get_method_param_int(invocation.method_name, constants.WEIGHT_KEY, constants.DEFAULT_WEIGHT_VALUE)

        if weight > 0:
            # Get service provider startup timestamp
            timestamp = url.get_param_int(constants.TIMESTAMP_KEY, 0)
            if timestamp > 0:
                # Calculate service uptime in milliseconds
                uptime = max(int(time.time() * 1000) - timestamp, 1)
                # Get service warm-up time
                warmup = url.get_param_int(constants.WARMUP_KEY, constants.DEFAULT_WARMUP_VALUE)
                # If within warmup period, adjust weight -> downward adjustment
                if 0 < uptime < warmup:
                    # Calculate warmup weight
                    warmup_weight = int(uptime / (warmup / weight))
                    # Ensure warmup weight is between 1 and original weight
                    weight = max(1, min(warmup_weight, weight))
    return max(weight, 0)


class BaseLoadBalance(LoadBalance, abc.ABC):
    """Base class for load balancing strategies."""

    def select(self, invokers: list[Invoker], url: URL, invocation: Invocation) -> Optional[Invoker]:
        if not invokers:
            return None
        if len(invokers) == 1:
            return invokers[0]

        return self.do_select(invokers, url, invocation)

    @abc.abstractmethod
    def do_select(self, invokers: list[Invoker], url: URL, invocation: Invocation) -> Optional[Invoker]:
        """Abstract method to implement the specific load balancing algorithm."""
        raise NotImplementedError()


class BaseAsyncLoadBalance(AsyncLoadBalance, abc.ABC):
    """Base class for asynchronous load balancing strategies.

    This class extends BaseLoadBalance to provide an asynchronous interface
    for selecting invokers.
    """

    async def select(self, invokers: list[AsyncInvoker], url: URL, invocation: Invocation) -> Optional[AsyncInvoker]:
        if not invokers:
            return None
        if len(invokers) == 1:
            return invokers[0]

        return await self.do_select(invokers, url, invocation)

    @abc.abstractmethod
    async def do_select(self, invokers: list[AsyncInvoker], url: URL, invocation: Invocation) -> Optional[AsyncInvoker]:
        """Asynchronous method to implement the specific load balancing algorithm."""
        raise NotImplementedError()
