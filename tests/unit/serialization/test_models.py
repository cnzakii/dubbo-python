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
import sys
from typing import Annotated, Any

import msgspec
import pydantic
import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorProto
from google.protobuf.struct_pb2 import Value

from dubbo._proto.google.rpc.status_pb2 import Status as StatusMessage
from dubbo.extension import extensions
from dubbo.serialization.json import JsonSerialization
from dubbo.serialization.models import ModelField, Models, ModelSupport
from dubbo.serialization.models.pydantic import PydanticSupport
from dubbo.serialization.msgpack import MsgpackSerialization
from dubbo.url import URL

_URL = URL(protocol="tri")


def _route(point: type) -> type:
    return dataclasses.make_dataclass("Route", [("points", list[point])])


@pytest.mark.parametrize("serialization", [JsonSerialization, MsgpackSerialization])
def test_an_installed_model_converts_its_classes_in_every_msgspec_serialization(serialization: Any, points) -> None:
    route_type = _route(points)
    codec = serialization(_URL).codec([route_type])
    route = route_type([points(1, 2)])

    assert codec.decode(codec.encode((route,))) == (route,)


def test_what_a_model_refuses_is_reported_at_its_place(points) -> None:
    with pytest.raises(msgspec.ValidationError, match=r"expected two integers .* - at `\$.points\[0\]`"):
        JsonSerialization(_URL).codec([_route(points)]).decode(b'{"points": ["1"]}')


def test_a_class_two_models_support_is_refused(points) -> None:
    extensions.register(ModelSupport, "more-points", extensions.get(ModelSupport, "points"))

    with pytest.raises(TypeError, match="more than one model: more-points, points"):
        Models(_URL).prepare([_route(points)])


def test_pydantic_supports_nothing_until_the_application_imports_it(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pydantic", None)

    assert not PydanticSupport(_URL).supports(dict)


class _Item(pydantic.BaseModel):
    sku: str


@dataclasses.dataclass
class _Shelf:
    item: _Item


def test_schemas_name_the_definitions_of_every_library_apart() -> None:
    # A dataclass named as a pydantic model, and a model named as a message of a file without a package.
    item = dataclasses.make_dataclass("_Item", [("count", int)])
    taken = pydantic.create_model("google.rpc.Status", __module__="taken", note=(str, ...))

    entries = [(_Shelf, "input"), (item, "input"), (taken, "input"), (StatusMessage, "output")]
    schemas, definitions = Models(_URL).schemas(entries, "#/{name}")

    def fields(schema: dict[str, Any]) -> set[str]:
        return set(definitions[schema["$ref"].removeprefix("#/")]["properties"])

    shelf = definitions[schemas[0]["$ref"].removeprefix("#/")]
    assert fields(shelf["properties"]["item"]) == {"sku"}
    assert (fields(schemas[1]), fields(schemas[2]), fields(schemas[3])) == (
        {"count"},
        {"note"},
        {"code", "message", "details"},
    )
    assert len({schema["$ref"] for schema in schemas} | {shelf["properties"]["item"]["$ref"]}) == 5


class _Tag(pydantic.BaseModel):
    label: str = pydantic.Field(validation_alias="in", serialization_alias="out")


@dataclasses.dataclass
class _Tagged:
    tag: _Tag


@dataclasses.dataclass
class _Rack:
    shelf: _Shelf
    tagged: _Tagged


def test_an_output_that_differs_from_its_input_has_a_definition_of_its_own() -> None:
    entries = [(_Rack, "input"), (_Rack, "output")]

    (given, returned), definitions = Models(_URL).schemas(entries, "#/{name}")

    def resolve(schema: dict[str, Any]) -> dict[str, Any]:
        return definitions[schema["$ref"].removeprefix("#/")]

    def label(rack: dict[str, Any]) -> set[str]:
        return set(resolve(resolve(resolve(rack)["properties"]["tagged"])["properties"]["tag"])["properties"])

    assert (label(given), label(returned)) == ({"in"}, {"out"})
    # What reads and writes the same JSON is defined once.
    assert resolve(given)["properties"]["shelf"] == resolve(returned)["properties"]["shelf"]
    assert {"_Rack", "_Rack-Output", "_Tagged", "_Tagged-Output", "_Shelf"} <= definitions.keys()


def test_a_recursive_message_refers_to_itself_and_well_known_types_are_inline() -> None:
    schemas, definitions = Models(_URL).schemas([(Value, "input"), (FileDescriptorProto, "output")], "#/{name}")

    assert (schemas[0], definitions.get("google.protobuf.Value")) == ({"title": "Value"}, None)
    assert schemas[1] == {"$ref": "#/google.protobuf.FileDescriptorProto"}
    properties = definitions["google.protobuf.FileDescriptorProto"]["properties"]
    assert properties["messageType"] == {"type": "array", "items": {"$ref": "#/google.protobuf.DescriptorProto"}}
    assert definitions["google.protobuf.FieldDescriptorProto.Label"]["enum"][0] == "LABEL_OPTIONAL"


def test_the_fields_of_a_class_are_those_msgspec_or_its_model_reads() -> None:
    @dataclasses.dataclass
    class Page:
        size: Annotated[int, msgspec.Meta(ge=1)]
        tags: list[str] = dataclasses.field(default_factory=list)

    class Filter(pydantic.BaseModel):
        min_price: int = pydantic.Field(alias="minPrice", description="At least")

    models = Models(_URL)

    assert models.fields(Page) == (
        ModelField(key="size", type=Annotated[int, msgspec.Meta(ge=1)], required=True),
        ModelField(key="tags", type=list[str], required=False),
    )
    (price,) = models.fields(Filter)
    assert (price.key, price.required, price.description) == ("minPrice", True, "At least")
    assert models.fields(int) is None
    assert models.fields(StatusMessage) is None
