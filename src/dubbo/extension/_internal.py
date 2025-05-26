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
from dubbo.remoting.zookeeper import ZookeeperClient
from dubbo.remoting.zookeeper.kazoo import AsyncKazooZookeeperClient


@dataclass(frozen=True)
class ExtensionRegistry:
    """Registry for a specific interface with named implementations.

    Maps implementation names to either fully qualified import paths
    or actual implementation classes for lazy or eager loading.

    Args:
        interface: The base interface or abstract class to be implemented
        impls: A mapping of implementation names to either class paths (str)
            or direct class references
    """

    interface: type
    impls: dict[str, Union[str, type]]


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

zkClientRegistry = ExtensionRegistry(
    interface=ZookeeperClient,
    impls={
        "kazoo": "dubbo.remoting.zookeeper.kazoo:KazooZookeeperClient",
    },
)

asyncZkClientRegistry = ExtensionRegistry(
    interface=AsyncKazooZookeeperClient,
    impls={
        "kazoo": "dubbo.remoting.zookeeper.kazoo:AsyncKazooZookeeperClient",
    },
)


def get_all_registries() -> list[ExtensionRegistry]:
    """Collect all ExtensionRegistry instances defined in this module.

    Searches through the global namespace to find all ExtensionRegistry
    instances, enabling automatic discovery of available extension points.

    Returns:
        List of all ExtensionRegistry instances in this module.

    Example:
        registries = get_all_registries()
        for registry in registries:
            print(f"Found registry for {registry.interface.__name__}")
    """
    registries = []
    for name, obj in globals().items():
        if isinstance(obj, ExtensionRegistry):
            registries.append(obj)
    return registries
