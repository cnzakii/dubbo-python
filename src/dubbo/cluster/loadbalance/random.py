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
import random
from collections.abc import Sequence
from typing import Optional, TypeVar, Union

from dubbo.common import URL
from dubbo.protocol import AsyncInvoker, Invocation, Invoker

from .base import BaseAsyncLoadBalance, BaseLoadBalance, get_weight

__all__ = ["RandomLoadBalance", "AsyncRandomLoadBalance"]

_T_Invoker = TypeVar("_T_Invoker", bound=Union[Invoker, AsyncInvoker])


def _select_random(invokers: Sequence[_T_Invoker], invocation: Invocation) -> _T_Invoker:
    """Select using weighted random algorithm.

    Returns:
        Randomly selected invoker based on computed weights.
    """
    # Precompute weights
    weights = [get_weight(invoker, invocation) for invoker in invokers]

    # randomly select an invoker based on weights
    return random.choices(invokers, weights=weights)[0]


class RandomLoadBalance(BaseLoadBalance):
    """Random load balancing with weight support.

    Uses weighted random selection where higher weights increase selection
    probability. Falls back to uniform random when weights are equal.
    """

    def do_select(self, invokers: list[Invoker], url: URL, invocation: Invocation) -> Optional[Invoker]:
        return _select_random(invokers, invocation)


class AsyncRandomLoadBalance(BaseAsyncLoadBalance):
    """Asynchronous random load balancing with weight support.

    Uses weighted random selection where higher weights increase selection
    probability. Falls back to uniform random when weights are equal.
    """

    async def do_select(self, invokers: list[AsyncInvoker], url: URL, invocation: Invocation) -> Optional[AsyncInvoker]:
        return _select_random(invokers, invocation)
