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

"""The ``json`` serialization."""

from collections.abc import Sequence
from typing import Any

import msgspec
from google.protobuf.message import Message

from dubbo.utils.annotations import unannotated
from dubbo.utils.json import dump_json, load_json

from .base import Codec
from .models.protobuf import is_message, message_from_builtins, message_to_builtins
from .msgspec_serialization import MsgspecSerialization

__all__ = ["JsonSerialization"]


class JsonSerialization(MsgspecSerialization):
    """Carry values as JSON text in UTF-8, written and validated by msgspec.

    A message of one value is the JSON of that value, and of any other
    number of values a JSON array of them in order, as plain HTTP calls of
    Triple carry the arguments. A class msgspec does not know is
    converted by the installed `dubbo.serialization.models.ModelSupport`
    that supports it, such as pydantic models and protobuf messages, which
    follow the proto3 JSON mapping. A protobuf message alone is converted by
    protobuf itself.
    """

    content_subtype = "json"
    encoder = msgspec.json.Encoder
    decoder = msgspec.json.Decoder

    def codec(self, types: Sequence[Any]) -> Codec[tuple[Any, ...]]:
        if len(types) == 1 and is_message(message := unannotated(types[0])):
            return _message_codec(message)
        return super().codec(types)


def _message_codec(message: type[Message]) -> Codec[tuple[Any, ...]]:
    """Return the codec of one protobuf message of ``message`` as JSON, by the proto3 JSON mapping."""

    def encode(values: tuple[Any, ...]) -> bytes:
        (value,) = values
        if not isinstance(value, message):
            raise TypeError(f"expected {message.__qualname__}, not {type(value).__qualname__}")
        return dump_json(message_to_builtins(value)).encode()

    def decode(data: bytes) -> tuple[Any, ...]:
        return (message_from_builtins(message, load_json(data)),)

    return Codec(encode=encode, decode=decode)
