# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

# The consumer: it calls both versions of the Greeter, each through a
# reference of its own.

import asyncio

from dubbo import Dubbo
from dubbo.config import ApplicationConfig, ReferenceConfig, RegistryConfig

from greeter import Greeter


async def main() -> None:
    # One reference to a service is named by its interface. Two references
    # to it, one for each version, each take a name of their own, which
    # consumer.references in a file keys their settings by too.
    # ConsumerConfig(version=...) would choose one version for every
    # reference of the application instead.
    app = Dubbo(
        application=ApplicationConfig(name="group-version-client"),
        registries={"default": RegistryConfig(address="zookeeper://127.0.0.1:2181")},
    )
    v1 = app.refer(Greeter, name="greeter_v1", config=ReferenceConfig(version="1.0.0"))
    v2 = app.refer(Greeter, name="greeter_v2", config=ReferenceConfig(version="2.0.0"))

    async with app:
        print("Version 1.0.0:", await v1.greet("Alice"))
        print("Version 2.0.0:", await v2.greet("Alice"))


if __name__ == "__main__":
    asyncio.run(main())
