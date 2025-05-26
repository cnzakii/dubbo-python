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
from typing import Optional

from dubbo.common import URL
from dubbo.protocol import Invocation, Invoker

from .base import LoadBalance, get_weight

__all__ = ["RandomLoadBalance"]


class RandomLoadBalance(LoadBalance):
    """Random load balancing with weight support.

    Uses weighted random selection where higher weights increase selection
    probability. Falls back to uniform random when weights are equal.
    """

    def select(self, invokers: list[Invoker], url: URL, invocation: Invocation) -> Optional[Invoker]:
        """Select using weighted random algorithm.

        Returns:
            Randomly selected invoker based on computed weights.
        """
        if not invokers:
            return None
        if len(invokers) == 1:
            return invokers[0]

        # Precompute weights
        weights = [get_weight(invoker, invocation) for invoker in invokers]

        # randomly select an invoker based on weights
        return random.choices(invokers, weights=weights)[0]
