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

import logging

import anyio
import pytest

from dubbo.cluster.rule import RuleWatch, rule_key
from dubbo.config_center import ConfigCenter
from dubbo.extension import ApplicationScope, extensions
from dubbo.url import APPLICATION_SCOPE, URL


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("tri://h:1/com.example.Users?version=1&group=a", "com.example.Users:1:a"),
        ("tri://h:1/com.example.Users", "com.example.Users::"),
        ("dubbo://h:1/shop/com.example.Users?interface=com.example.Users", "com.example.Users::"),
    ],
)
def test_the_rules_of_a_service_are_keyed_by_its_interface(url, key):
    assert rule_key(URL.parse(url)) == key


async def test_a_configuration_center_that_cannot_be_reached_is_tried_again_in_the_background(config_store):
    refusals = [OSError("refused")]
    create = config_store.create

    def connecting(url: URL):
        center = create(url)

        class Refusing(type(center)):
            __slots__ = ()

            async def __aenter__(self):
                if refusals:
                    raise refusals.pop()
                return await super().__aenter__()

        center.__class__ = Refusing
        return center

    extensions.register(ConfigCenter, "refusing", connecting)
    config_store.put("k", "value: 1")

    with anyio.fail_after(60):
        async with ApplicationScope() as scope:
            url = URL(protocol="refusing", attributes={APPLICATION_SCOPE: scope})
            async with RuleWatch(url, lambda rule: rule["value"]) as watch:
                await watch.follow("k")
                while not watch.rules:
                    await anyio.sleep(1)

    assert (watch.rules, refusals) == ({"k": 1}, [])


async def test_a_rule_that_applies_and_one_removed_are_logged(config_store, caplog):
    config_store.put("k", "value: 1")

    with caplog.at_level(logging.INFO), anyio.fail_after(5):
        async with ApplicationScope() as scope:
            url = URL(protocol="memory", attributes={APPLICATION_SCOPE: scope})
            async with RuleWatch(url, lambda rule: rule["value"]) as watch:
                await watch.follow("k")
                while not watch.rules:
                    await anyio.sleep(0.01)
                config_store.put("k", None)
                while watch.rules:
                    await anyio.sleep(0.01)

    assert caplog.messages == ["The rule k applies", "The rule k is removed"]
