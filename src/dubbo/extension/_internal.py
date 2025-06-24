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

from dubbo.cluster import LoadBalance
from dubbo.codec import CodecFactory
from dubbo.compression import Compressor, Decompressor
from dubbo.registry import AsyncRegistry, Registry
from dubbo.remoting.h2 import AsyncHttp2Transport, Http2Transport
from dubbo.remoting.zookeeper import AsyncZookeeperTransport, ZookeeperTransport

# Global registry for all extension registries
_EXTENSION_REGISTRIES: list["ExtensionRegistry"] = []


def get_all_registries() -> list["ExtensionRegistry"]:
    """Returns all registered extension registries."""
    return _EXTENSION_REGISTRIES


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

    def __post_init__(self) -> None:
        """Post-initialization to register this instance globally."""
        _EXTENSION_REGISTRIES.append(self)


# --------- Define all extension registries for various interfaces ---------

loadBalanceRegistry = ExtensionRegistry(
    interface=LoadBalance,
    impls={
        "random": "dubbo.cluster.loadbalance.random.RandomLoadBalance",
    },
)

registryRegistry = ExtensionRegistry(
    interface=Registry,
    impls={
        "zookeeper": "dubbo.registry.zookeeper.ZookeeperRegistry",
    },
)

asyncRegistryRegistry = ExtensionRegistry(
    interface=AsyncRegistry,
    impls={
        "zookeeper": "dubbo.registry.zookeeper.AsyncZookeeperRegistry",
    },
)

codecFactoryRegistry = ExtensionRegistry(
    interface=CodecFactory,
    impls={
        "json": "dubbo.codec.json_codec.JsonCodecFactory",
        "protobuf": "dubbo.codec.pb_codec.ProtobufCodecFactory",
        "pydantic-json": "dubbo.codec.pydantic_codec.PydanticCodecFactory",
    },
)

compressorRegistry = ExtensionRegistry(
    interface=Compressor,
    impls={
        "identity": "dubbo.compression.identity.Identity",
        "gzip": "dubbo.compression.gzip.Gzip",
        "bzip2": "dubbo.compression.bzip2.Bzip2",
    },
)

decompressorRegistry = ExtensionRegistry(
    interface=Decompressor,
    impls={
        "identity": "dubbo.compression.identity.Identity",
        "gzip": "dubbo.compression.gzip.Gzip",
        "bzip2": "dubbo.compression.bzip2.Bzip2",
    },
)

zkTransportRegistry = ExtensionRegistry(
    interface=ZookeeperTransport,
    impls={
        "kazoo": "dubbo.remoting.zookeeper.kazoo.KazooTransport",
    },
)

asyncZkTransportRegistry = ExtensionRegistry(
    interface=AsyncZookeeperTransport,
    impls={
        "kazoo": "dubbo.remoting.zookeeper.kazoo.AsyncKazooTransport",
    },
)

h2TransportRegistry = ExtensionRegistry(
    interface=Http2Transport,
    impls={
        "sync": "dubbo.remoting.h2.sync.transport.SyncHttp2Transport",
    },
)

asyncH2TransportRegistry = ExtensionRegistry(
    interface=AsyncHttp2Transport,
    impls={
        "auto": "dubbo.remoting.h2.auto.AutoHttp2Transport",
        "asyncio": "dubbo.remoting.h2.aio.transport.AioHttp2Transport",
        "anyio": "dubbo.remoting.h2.anyio.transport.AnyIOHttp2Transport",
    },
)
