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

import pytest

from dubbo.registry import reference
from dubbo.registry.consumer import Consumer
from dubbo.rpc.definition import Service
from dubbo.url import APPLICATION_SCOPE, URL

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("mode", "preferred"),
    [("instance", [None]), ("interface", [None]), ("all", ["true", None])],
)
async def test_only_the_instances_of_mode_all_are_preferred_over_its_interfaces(monkeypatch, mode, preferred):
    seen: list[str | None] = []

    class Cluster:
        async def aclose(self) -> None:
            pass

    async def create_cluster(url, directory):
        if url.parameters.get("cluster") != "zone-aware":
            seen.append(url.parameters.get("registry.preferred"))
        return Cluster()

    monkeypatch.setattr(reference, "create_cluster", create_cluster)
    monkeypatch.setattr(reference, "DynamicDirectory", lambda *args: None)
    monkeypatch.setattr(reference, "create_routers", lambda names, url: [])
    registry = URL.parse(f"memory://registry?register-mode={mode}").replace(attributes={APPLICATION_SCOPE: object()})
    consumer = Consumer(interface="test.Greeter", group=None, version=None, parameters={})

    await reference._refer_registry(
        registry, consumer, Service(interface="test.Greeter", methods=()), URL.parse("consumer://h/test.Greeter")
    )

    assert seen == preferred
