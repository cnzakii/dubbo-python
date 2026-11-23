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
import re
from typing import Annotated, Any

import msgspec
import pydantic
import pytest
from google.protobuf.descriptor_pb2 import UninterpretedOption
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.wrappers_pb2 import Int64Value

from dubbo._proto.google.rpc.status_pb2 import Status as StatusMessage
from dubbo.serialization.json import JsonSerialization
from dubbo.url import URL


@dataclasses.dataclass
class User:
    name: str
    age: Annotated[int, msgspec.Meta(ge=0)] = 0


class Owner(pydantic.BaseModel):
    owner_id: int = pydantic.Field(alias="ownerId", ge=1)


@dataclasses.dataclass
class Order:
    owner: Owner
    total: Int64Value
    tags: list[str] = dataclasses.field(default_factory=list)


def _codec(*types: Any):
    return JsonSerialization(URL(protocol="tri")).codec(types)


def _one(annotation: Any, value: Any) -> bytes:
    return _codec(annotation).encode((value,))


def test_a_message_alone_follows_the_proto3_json_mapping() -> None:
    status = _codec(StatusMessage)

    assert status.encode((StatusMessage(code=5, message="gone"),)) == b'{"code":5,"message":"gone"}'
    assert status.encode((StatusMessage(),)) == b"{}"
    assert _one(Int64Value, Int64Value(value=5)) == b'"5"'
    assert _one(Timestamp, Timestamp(seconds=1)) == b'"1970-01-01T00:00:01Z"'
    assert status.decode(b'{"code":5,"unknown":1}') == (StatusMessage(code=5),)
    assert _codec(Struct).decode(b'{"a": [1]}')[0]["a"][0] == 1


def test_a_message_writes_lower_camel_case_and_reads_either_name() -> None:
    option = _codec(UninterpretedOption)

    assert option.encode((UninterpretedOption(identifier_value="x", negative_int_value=-5),)) == (
        b'{"identifierValue":"x","negativeIntValue":"-5"}'
    )
    assert option.decode(b'{"identifier_value":"x","negativeIntValue":"-5","unknown":1}') == (
        UninterpretedOption(identifier_value="x", negative_int_value=-5),
    )


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"[]", "Expected `object`, got `list`"),
        (b'""', "Expected `object`, got `str`"),
        (b"1", "Expected `object`, got `int`"),
        (b"{", "Expecting property name"),
        (b'{"code":"x"}', "Failed to parse code field"),
    ],
)
def test_a_message_alone_rejects_json_of_another_shape(data: bytes, problem: str) -> None:
    with pytest.raises(ValueError, match=re.escape(problem)):
        _codec(StatusMessage).decode(data)


def test_a_message_alone_is_written_only_from_its_class() -> None:
    with pytest.raises(TypeError):
        _codec(StatusMessage).encode((User("a"),))


def test_other_types_are_written_and_validated_by_msgspec() -> None:
    user = _codec(User)

    assert user.encode((User("c", 1),)) == b'{"name":"c","age":1}'
    assert user.decode(b'{"name":"a"}') == (User("a"),)
    assert _one(None, None) == b"null"
    with pytest.raises(msgspec.ValidationError, match=r"Expected `int` >= 0 - at `\$.age`"):
        user.decode(b'{"name":"a","age":-1}')


def test_json_is_read_strictly() -> None:
    with pytest.raises(msgspec.ValidationError, match="Expected `int`, got `str`"):
        _codec(User).decode(b'{"name":"a","age":"3"}')


def test_models_and_messages_within_other_types_are_converted_by_their_libraries() -> None:
    order = _codec(Order)
    value = Order(Owner(ownerId=7), Int64Value(value=5), ["a"])

    assert order.encode((value,)) == b'{"owner":{"ownerId":7},"total":"5","tags":["a"]}'
    assert order.decode(order.encode((value,))) == (value,)
    with pytest.raises(msgspec.ValidationError, match=r"ownerId: Input should be greater .* - at `\$.owner`"):
        order.decode(b'{"owner":{"ownerId":0},"total":"5"}')


def test_other_numbers_of_values_travel_as_a_json_array() -> None:
    pair = _codec(str, User)

    assert pair.encode(("x", User("a"))) == b'["x",{"name":"a","age":0}]'
    assert pair.decode(b'["x", {"name": "a"}]') == ("x", User("a"))
    assert _codec().encode(()) == b"[]"
    assert _codec().decode(b"[]") == ()
    for broken in (b"[", b'["x"]', b'{"a": 1}', b'[1, {"name": "a"}]'):
        with pytest.raises(msgspec.DecodeError):
            pair.decode(broken)
    with pytest.raises(TypeError):
        pair.encode(("x", object()))


def test_a_class_no_library_converts_is_refused_when_the_codec_is_made() -> None:
    class Opaque:
        pass

    with pytest.raises(TypeError, match=r"cannot carry .*Opaque"):
        _codec(list[Opaque])
