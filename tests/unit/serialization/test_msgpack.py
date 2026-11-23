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

import dataclasses
from typing import Any

import msgspec
import pytest
from google.protobuf.wrappers_pb2 import Int64Value

from dubbo.serialization.json import JsonSerialization
from dubbo.serialization.msgpack import MsgpackSerialization
from dubbo.url import URL


@dataclasses.dataclass
class Item:
    sku: str
    count: int = 1


def test_values_travel_as_messagepack_and_back() -> None:
    serialization = MsgpackSerialization(URL(protocol="tri"))
    one = serialization.codec([Item])
    pair = serialization.codec([str, list[Item]])

    assert one.encode((Item("a"),)) == msgspec.msgpack.encode({"sku": "a", "count": 1})
    assert one.decode(one.encode((Item("a", 2),))) == (Item("a", 2),)
    assert pair.decode(pair.encode(("x", [Item("b")]))) == ("x", [Item("b")])


def test_a_message_travels_as_its_proto3_json_mapping() -> None:
    codec = MsgpackSerialization(URL(protocol="tri")).codec([Int64Value])

    assert codec.encode((Int64Value(value=5),)) == msgspec.msgpack.encode("5")
    assert codec.decode(codec.encode((Int64Value(value=5),))) == (Int64Value(value=5),)


def test_bytes_of_another_shape_are_refused() -> None:
    with pytest.raises(msgspec.ValidationError):
        MsgpackSerialization(URL(protocol="tri")).codec([Item]).decode(msgspec.msgpack.encode([1]))


def _holding_itself() -> list:
    value: list = []
    value.append(value)
    return value


@pytest.mark.parametrize("value", [2**70, "\ud800", _holding_itself()], ids=["big-int", "lone-surrogate", "cycle"])
def test_a_value_msgspec_cannot_write_raises_type_error(value) -> None:
    codec = MsgpackSerialization(URL(protocol="tri")).codec([Any])

    with pytest.raises(TypeError, match="cannot encode"):
        codec.encode((value,))


@pytest.mark.parametrize("data", [b"\x91" * 100_000 + b"\xc0", b"[" * 100_000], ids=["msgpack", "json"])
def test_data_nested_too_deeply_raises_value_error(data) -> None:
    serialization = MsgpackSerialization if data[0] == 0x91 else JsonSerialization
    codec = serialization(URL(protocol="tri")).codec([Any])

    with pytest.raises(ValueError, match="nested too deeply"):
        codec.decode(data)
