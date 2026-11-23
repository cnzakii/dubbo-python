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

import hashlib

import anyio
import attrs
import pytest
from v2.nacos import ConfigParam, NacosException

from dubbo.transport.nacos import NacosError
from dubbo.transport.nacos.sdk import SdkNacosConfigClient
from dubbo.transport.nacos.sdk import config as sdk_config
from dubbo.url import URL

pytestmark = pytest.mark.anyio


@attrs.define(kw_only=False)
class _Configs:
    """A configuration service of the SDK that another writer changes once while it is read."""

    content: str | tuple[str, str]
    interference: str | None = None
    published: list[tuple[str, str, str, str]] = attrs.field(factory=list)

    @property
    def grpc_client_proxy(self):
        return self

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def get_config(self, param: ConfigParam) -> str | tuple[str, str]:
        return self.content

    async def publish_config(self, param: ConfigParam) -> bool:
        self.published.append((param.data_id, param.group, param.content, param.cas_md5))
        if self.interference is not None:
            self.content, self.interference = self.interference, None
            raise NacosException(400, "md5 does not match")
        self.content = param.content
        return True


@pytest.mark.parametrize(("current", "md5"), [("", "0"), ("a", hashlib.md5(b"a").hexdigest())])
async def test_sdk_writes_a_configuration_if_it_is_still_what_was_read(monkeypatch, current, md5):
    configs = _Configs(content=current)
    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: configs)

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client:
        await client.update("d", "g", lambda content: (content or "") + "b")

    assert configs.published == [("d", "g", current + "b", md5)]


async def test_sdk_reads_the_local_cache_it_falls_back_to_as_the_configuration(monkeypatch):
    configs = _Configs(content=("a", ""))
    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: configs)

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client:
        assert await client.get("d", "g") == "a"


async def test_sdk_fails_a_read_whose_fallback_finds_no_local_cache(monkeypatch):
    configs = _Configs(content=("", ""))
    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: configs)

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client:
        with pytest.raises(NacosError, match="cannot read d"):
            await client.get("d", "g")


async def test_sdk_reads_a_configuration_again_that_changed_before_it_was_written(monkeypatch):
    configs = _Configs(content="a", interference="a,c")
    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: configs)
    monkeypatch.setattr(sdk_config, "_CAS_WAIT", 0)

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client:
        await client.update("d", "g", lambda content: f"{content},b")

    assert configs.content == "a,c,b"
    assert configs.published == [
        ("d", "g", "a,b", hashlib.md5(b"a").hexdigest()),
        ("d", "g", "a,c,b", hashlib.md5(b"a,c").hexdigest()),
    ]


async def test_sdk_watch_cancelled_while_adding_its_listener_leaves_none(monkeypatch):
    class Configs:
        def __init__(self):
            self.grpc_client_proxy = self
            self.listeners = []

        async def start(self):
            pass

        async def shutdown(self):
            pass

        async def add_listener(self, data_id, group, listener):
            self.listeners.append(listener)
            await anyio.sleep_forever()

        async def remove_listener(self, data_id, group, listener):
            self.listeners.remove(listener)

    configs = Configs()
    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: configs)

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client:
        with anyio.move_on_after(0.1):
            async with client.watch("d", "g"):
                pytest.fail("the watch was not cancelled")

    assert configs.listeners == []


async def test_sdk_watch_keeps_a_change_told_while_it_read_the_first_value(monkeypatch):
    class Configs:
        def __init__(self):
            self.grpc_client_proxy = self
            self.listener = None

        async def start(self):
            pass

        async def shutdown(self):
            pass

        async def add_listener(self, data_id, group, listener):
            self.listener = listener

        async def remove_listener(self, data_id, group, listener):
            pass

        async def get_config(self, param):
            # A newer value is told while the older one is on its way back.
            await self.listener("", param.group, param.data_id, "new")
            return "old"

    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: Configs())

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client, client.watch("d", "g") as values:
        with anyio.fail_after(5):
            assert await anext(values) == "new"


class _Recording:
    """A configuration service of the SDK that records what is read and watched."""

    def __init__(self) -> None:
        self.grpc_client_proxy = self
        self.listened: list[str] = []
        self.read: list[str] = []

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def add_listener(self, data_id, group, listener):
        self.listened.append(data_id)

    async def remove_listener(self, data_id, group, listener):
        pass

    async def get_config(self, param: ConfigParam) -> str:
        self.read.append(param.data_id)
        return "rule"


async def test_sdk_neither_watches_nor_reads_a_name_nacos_refuses_and_warns_of_it_once(monkeypatch, caplog):
    configs = _Recording()
    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: configs)

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client:
        async with client.watch("Echo:*:blue.condition-router", "dubbo") as contents:
            first = await anext(contents)
        async with client.watch("Echo::blue.condition-router", "dubbo") as contents:
            valid = await anext(contents)
        async with client.watch("Echo:*:blue.condition-router", "dubbo") as contents:
            again = await anext(contents)
        read = await client.get("Echo:*:blue.condition-router", "dubbo")

    assert (first, valid, again, read) == (None, "rule", None, None)
    assert (configs.listened, configs.read) == (["Echo::blue.condition-router"], ["Echo::blue.condition-router"])
    warnings = [record for record in caplog.records if "Echo:*:blue.condition-router" in record.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "name",
    [
        # Numeric to Python's isalnum, but neither a letter nor a digit to Java.
        "Echo½.condition-router",
        # A letter, but two UTF-16 units to Java, neither of them a letter.
        "Echo\U0001d400.condition-router",
        ".",
        "..",
    ],
)
async def test_sdk_refuses_a_name_nacos_refuses_as_java_checks_it(monkeypatch, name):
    configs = _Recording()
    monkeypatch.setattr(sdk_config, "NacosConfigService", lambda settings: configs)

    async with SdkNacosConfigClient(URL.parse("nacos://n")) as client:
        read = await client.get(name, "dubbo")
        named = await client.get("Écho٣.condition-router", "dubbo")

    assert (read, named, configs.read) == (None, "rule", ["Écho٣.condition-router"])
