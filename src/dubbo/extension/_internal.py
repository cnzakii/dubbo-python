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
from dataclasses import dataclass
from typing import Union

from dubbo.cluster.loadbalance import LoadBalance
from dubbo.compression import Compressor
from dubbo.logger import DubboLogger


@dataclass(frozen=True)
class ExtensionRegistry:
    """
    Represents a registry for a specific interface with named implementations.

    :param interface: The interface type
    :param impls: A mapping of implementation names to class path strings or classes
    """

    interface: type
    impls: dict[str, Union[str, type]]


loggerRegistry = ExtensionRegistry(
    interface=DubboLogger,
    impls={
        "logging": "dubbo.logger._logging:LoggingLogger",
        "loguru": "dubbo.logger._loguru:LoguruLogger",
    },
)


loadBalanceRegistry = ExtensionRegistry(
    interface=LoadBalance,
    impls={
        "random": "dubbo.cluster.loadbalance.random:RandomLoadBalance",
    },
)


compressorRegistry = ExtensionRegistry(
    interface=Compressor,
    impls={
        "identity": "dubbo.compression.identity:Identity",
        "gzip": "dubbo.compression.gzip:Gzip",
        "bzip2": "dubbo.compression.bzip2:Bzip2",
    },
)


def get_all_registries() -> list[ExtensionRegistry]:
    """
    Return all ExtensionRegistry instances defined in this module.
    :return: A list of ExtensionRegistry instances
    """
    registries = []
    for name, obj in globals().items():
        if isinstance(obj, ExtensionRegistry):
            registries.append(obj)
    return registries
