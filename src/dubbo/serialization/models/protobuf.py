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

"""Protobuf messages as the proto3 JSON mapping writes them, for the msgspec serializations and plain HTTP."""

from collections.abc import Sequence
from typing import Any, TypeGuard, cast

from google.protobuf import json_format
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import Message

from dubbo.url import URL

from .base import Mode, ModelField

__all__ = [
    "PARSE_ERRORS",
    "ProtobufSupport",
    "field_schema",
    "is_message",
    "is_well_known",
    "message_from_builtins",
    "message_schema",
    "message_to_builtins",
]

# ParseDict raises TypeError or AttributeError, not ParseError, for some values of the wrong shape.
PARSE_ERRORS = (json_format.ParseError, TypeError, AttributeError)

# The well-known types whose JSON is something other than an object.
_NOT_OBJECTS = frozenset(
    "google.protobuf." + name
    for name in (
        "Duration",
        "Timestamp",
        "FieldMask",
        "Value",
        "ListValue",
        "DoubleValue",
        "FloatValue",
        "Int64Value",
        "UInt64Value",
        "Int32Value",
        "UInt32Value",
        "BoolValue",
        "StringValue",
        "BytesValue",
    )
)


# The JSON Schema of each well-known type whose JSON is not that of its fields.
_WELL_KNOWN_SCHEMAS: dict[str, dict[str, Any]] = {
    "google.protobuf.Timestamp": {"type": "string", "format": "date-time"},
    "google.protobuf.Duration": {"type": "string"},
    "google.protobuf.FieldMask": {"type": "string"},
    "google.protobuf.Struct": {"type": "object", "additionalProperties": {}},
    "google.protobuf.Value": {},
    "google.protobuf.ListValue": {"type": "array", "items": {}},
    "google.protobuf.Empty": {"type": "object"},
    "google.protobuf.Any": {
        "type": "object",
        "properties": {"@type": {"type": "string"}},
        "additionalProperties": {},
    },
    "google.protobuf.DoubleValue": {"type": "number", "format": "double"},
    "google.protobuf.FloatValue": {"type": "number", "format": "float"},
    "google.protobuf.Int64Value": {"type": "string", "format": "int64"},
    "google.protobuf.UInt64Value": {"type": "string", "format": "uint64"},
    "google.protobuf.Int32Value": {"type": "integer", "format": "int32"},
    "google.protobuf.UInt32Value": {"type": "integer", "format": "int64"},
    "google.protobuf.BoolValue": {"type": "boolean"},
    "google.protobuf.StringValue": {"type": "string"},
    "google.protobuf.BytesValue": {"type": "string", "format": "byte"},
}
# The JSON Schema of each scalar field type; 64-bit integers are JSON strings, and uint32 does not fit int32.
_T = FieldDescriptor
_SCALAR_SCHEMAS: dict[int, dict[str, Any]] = {
    _T.TYPE_DOUBLE: {"type": "number", "format": "double"},
    _T.TYPE_FLOAT: {"type": "number", "format": "float"},
    _T.TYPE_INT64: {"type": "string", "format": "int64"},
    _T.TYPE_SINT64: {"type": "string", "format": "int64"},
    _T.TYPE_SFIXED64: {"type": "string", "format": "int64"},
    _T.TYPE_UINT64: {"type": "string", "format": "uint64"},
    _T.TYPE_FIXED64: {"type": "string", "format": "uint64"},
    _T.TYPE_INT32: {"type": "integer", "format": "int32"},
    _T.TYPE_SINT32: {"type": "integer", "format": "int32"},
    _T.TYPE_SFIXED32: {"type": "integer", "format": "int32"},
    _T.TYPE_UINT32: {"type": "integer", "format": "int64"},
    _T.TYPE_FIXED32: {"type": "integer", "format": "int64"},
    _T.TYPE_BOOL: {"type": "boolean"},
    _T.TYPE_STRING: {"type": "string"},
    _T.TYPE_BYTES: {"type": "string", "format": "byte"},
}


def is_well_known(descriptor: Any) -> bool:
    """Whether the JSON of a message of ``descriptor`` is that of a well-known type, not an object of its fields."""
    return descriptor.full_name in _WELL_KNOWN_SCHEMAS


def message_schema(descriptor: Any, definitions: dict[str, Any], ref_template: str) -> dict[str, Any]:
    """Return the JSON Schema of a message of ``descriptor`` in the proto3 JSON mapping.

    A message other than a well-known type goes into ``definitions`` under
    its full name, so that recursive messages refer to themselves, and the
    schema refers to it by ``ref_template`` with ``{name}`` for the name.
    """
    name = descriptor.full_name
    if name in _WELL_KNOWN_SCHEMAS:
        return dict(_WELL_KNOWN_SCHEMAS[name])
    if name not in definitions:
        # Set before its fields, which may lead back to it.
        definitions[name] = {}
        definitions[name] = {
            "type": "object",
            "properties": {
                field.json_name: field_schema(field, definitions, ref_template) for field in descriptor.fields
            },
        }
    return {"$ref": ref_template.format(name=name)}


def field_schema(field: FieldDescriptor, definitions: dict[str, Any], ref_template: str) -> dict[str, Any]:
    """Return the JSON Schema of the value of ``field`` in the proto3 JSON mapping, as `message_schema` does."""
    message = field.message_type
    if message is not None and message.GetOptions().map_entry:
        value = message.fields_by_name["value"]
        return {"type": "object", "additionalProperties": field_schema(value, definitions, ref_template)}
    if message is not None:
        schema = message_schema(message, definitions, ref_template)
    elif field.enum_type is not None:
        enum = field.enum_type
        if enum.full_name == "google.protobuf.NullValue":
            schema = {"type": "null"}
        else:
            definitions.setdefault(enum.full_name, {"type": "string", "enum": [value.name for value in enum.values]})
            schema = {"$ref": ref_template.format(name=enum.full_name)}
    else:
        schema = dict(_SCALAR_SCHEMAS[field.type])
    return {"type": "array", "items": schema} if field.is_repeated else schema


def is_message(cls: Any) -> TypeGuard[type[Message]]:
    """Whether ``cls`` is a protobuf message class."""
    return isinstance(cls, type) and issubclass(cls, Message)


def message_to_builtins(value: Message) -> Any:
    """Return ``value`` as the proto3 JSON mapping writes it, as parsed JSON.

    Field names are in lowerCamelCase, fields with default values are left
    out and 64-bit integers are strings.

    Raises:
        TypeError: A field is out of the mapping's range, such as a Timestamp past year 9999.
    """
    try:
        return json_format.MessageToDict(value)
    except ValueError as error:
        raise TypeError(f"cannot write the {type(value).__qualname__} as JSON: {error}") from None


def message_from_builtins(cls: type[Message], data: Any) -> Message:
    """Return the message of ``cls`` that the parsed JSON ``data`` holds, by the proto3 JSON mapping.

    Field names are read in lowerCamelCase or as in the proto file, and
    unknown fields are ignored. A message other than a well-known type whose
    JSON is no object must be a JSON object.

    Raises:
        ValueError: ``data`` holds no such message.
    """
    if cls.DESCRIPTOR.full_name not in _NOT_OBJECTS and not isinstance(data, dict):
        raise ValueError("Expected `object`, got `" + type(data).__name__ + "`")

    try:
        return json_format.ParseDict(data, cls(), ignore_unknown_fields=True)
    except PARSE_ERRORS as error:
        raise ValueError(str(error)) from None


class ProtobufSupport:
    """Converts protobuf messages by the proto3 JSON mapping, as `message_to_builtins` and `message_from_builtins` do.

    A message has no fields of a request to gather: it comes from the body alone.
    """

    def __init__(self, url: URL) -> None:
        pass

    def supports(self, cls: type) -> bool:
        return issubclass(cls, Message)

    def to_builtins(self, value: Any) -> Any:
        return message_to_builtins(value)

    def from_builtins(self, cls: type, data: Any) -> Any:
        return message_from_builtins(cast("type[Message]", cls), data)

    def schemas(
        self, entries: Sequence[tuple[type, Mode]], ref_template: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # A message reads and writes the same JSON.
        definitions: dict[str, Any] = {}
        schemas = [
            message_schema(cast("type[Message]", cls).DESCRIPTOR, definitions, ref_template) for cls, _ in entries
        ]
        return schemas, definitions

    def fields(self, cls: type) -> tuple[ModelField, ...] | None:
        return None
