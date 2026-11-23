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

"""How plain HTTP carries a method whose one parameter is a protobuf message, as ``google.api.http`` maps it."""

import functools
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from typing import Any, Protocol, cast
from urllib.parse import parse_qs

import attrs
from google.protobuf import json_format
from google.protobuf.descriptor import Descriptor, FieldDescriptor
from google.protobuf.message import Message

from dubbo.rpc.definition import Method
from dubbo.rpc.params import ParamLocation, param_of
from dubbo.rpc.rest import HttpRoute
from dubbo.serialization.json import JsonSerialization
from dubbo.serialization.models.protobuf import PARSE_ERRORS, is_message, message_from_builtins
from dubbo.utils.annotations import unannotated
from dubbo.utils.json import dump_json, load_json

from .exceptions import HttpError, problem
from .messages import BodyKind, RestMessages, dumper

__all__ = ["RouteMessages", "TranscodedMessages", "route_messages"]

# A path from a message to one of its fields, through fields of message types.
_FieldPath = tuple[FieldDescriptor, ...]
_TRUE = frozenset(("true", "1"))
_FALSE = frozenset(("false", "0"))
_BOOL_VALUE = "google.protobuf.BoolValue"


@attrs.define(frozen=True, kw_only=True)
class TranscodedMessages:
    """How the request message of one route travels as plain HTTP messages, as ``google.api.http`` maps it, made once for each route.

    The fields the path template names come from those segments; the body
    is the whole message, one field of it or nothing, as the route's
    ``body`` tells; and every other field comes from the query parameter of
    its name, in the proto file or in JSON, with ``.`` between the names of
    nested fields. A repeated field takes every value of a repeated query
    parameter, and a map field one entry from each parameter ``name[key]``.
    A path segment overrides the body and the query.

    Attributes:
        message: The class of the request message.
        variables: The field each variable of the path template names, by
            the variable's name.
        body: What the body holds.
        body_field: The field the body holds; ``None`` for the whole
            message.
        dump_response: Writes the returned value as the JSON of the response.
    """

    message: type[Message]
    variables: tuple[tuple[str, _FieldPath], ...]
    body: BodyKind
    body_field: FieldDescriptor | None
    dump_response: Callable[[Any], bytes]

    @classmethod
    def of(cls, method: Method, route: HttpRoute, json: JsonSerialization) -> "TranscodedMessages":
        """Return how plain HTTP carries the request message of ``method`` on ``route``.

        Raises:
            TypeError: A variable of the path template or the route's
                ``body`` names no field the request can take there, or its
                ``response_body`` none of the response.
        """
        message = unannotated(method.request[0].type)
        descriptor = message.DESCRIPTOR
        body_field = None
        if route.body not in (None, "*"):
            body_field = descriptor.fields_by_name.get(route.body)
            if body_field is None:
                raise TypeError(f"the body names {route.body!r}, which {descriptor.full_name} has no field of")
        return cls(
            message=message,
            variables=tuple((name, _variable_path(descriptor, name)) for name in route.variables),
            body=BodyKind.NONE if route.body is None else BodyKind.WHOLE,
            body_field=body_field,
            dump_response=response_dumper(method.response, route.response_body, json),
        )

    def arguments(
        self,
        variables: Mapping[str, str],
        query: bytes,
        headers: Sequence[tuple[bytes, bytes]],
        body: bytes,
    ) -> tuple[Any, ...]:
        """Return the request message, from the path ``variables``, raw query and body of a request.

        Raises:
            HttpError: 422 for a body that is no JSON or a value that is
                invalid, with each problem as `RestMessages.arguments` reports it.
        """
        errors: list[dict[str, Any]] = []
        message = self.message()
        # Each source is parsed into the message in turn; parsing merges into what is there, so the path, last,
        # overrides the others, and a problem is reported where its value came from.
        if self.body is BodyKind.WHOLE and body:
            message = self._load_body(body, errors)

        # A body of the whole message leaves no field to the query.
        if self.body is BodyKind.NONE or self.body_field is not None:
            bound = {fields for _, fields in self.variables}
            # The entries of each map, by its path, set at once since setting a map replaces it.
            maps: dict[_FieldPath, tuple[str, dict[str, Any]]] = {}
            for key, values in parse_qs(query.decode("utf-8", "replace"), keep_blank_values=True).items():
                # A map entry is named `field[key]`.
                name, bracket, entry = key.partition("[")
                fields = _query_path(self.message.DESCRIPTOR, name)
                # Unknown parameters, and fields of the path or the body, are not taken from the query.
                if fields is None or fields in bound or fields[0] is self.body_field:
                    continue
                leaf = fields[-1]
                if bracket:
                    entry_value = _map_value(leaf)
                    if entry.endswith("]") and entry_value is not None:
                        maps.setdefault(fields, (name, {}))[1][entry[:-1]] = _scalar(entry_value, values[-1])
                    continue
                value = [_scalar(leaf, text) for text in values] if leaf.is_repeated else _scalar(leaf, values[-1])
                _merge(message, fields, value, (ParamLocation.QUERY.value, key), errors)
            for fields, (name, entries) in maps.items():
                _merge(message, fields, entries, (ParamLocation.QUERY.value, name), errors)

        for name, fields in self.variables:
            _merge(message, fields, _scalar(fields[-1], variables[name]), (ParamLocation.PATH.value, name), errors)

        if errors:
            raise HttpError(HTTPStatus.UNPROCESSABLE_ENTITY, errors)
        return (message,)

    def _load_body(self, body: bytes, errors: list[dict[str, Any]]) -> Message:
        """Return the message the body holds, collecting what is wrong with it."""
        message = self.message()
        try:
            value = load_json(body)
        except ValueError as error:
            errors.append(problem(("body",), error, "json_invalid"))
            return message
        try:
            if self.body_field is None:
                return message_from_builtins(self.message, value)
            json_format.ParseDict({self.body_field.name: value}, message, ignore_unknown_fields=True)
        except (ValueError, *PARSE_ERRORS) as error:
            errors.append(problem(("body",), error))
        return message


class RouteMessages(Protocol):
    """How the values of a method travel as plain HTTP messages on one route, as `RestMessages` and `TranscodedMessages` do."""

    @property
    def body(self) -> BodyKind:
        """What the body of a request holds."""
        ...

    @property
    def dump_response(self) -> Callable[[Any], bytes]:
        """Writes the returned value as the JSON of the response."""
        ...

    def arguments(
        self, variables: Mapping[str, str], query: bytes, headers: Sequence[tuple[bytes, bytes]], body: bytes
    ) -> tuple[Any, ...]:
        """Return the arguments of a request, from its path ``variables``, raw query, header fields and body.

        Raises:
            HttpError: The request does not carry valid arguments.
        """
        ...


def route_messages(method: Method, route: HttpRoute, json: JsonSerialization) -> RouteMessages:
    """Return how the values of the unary ``method`` travel as plain HTTP messages on ``route``, bodies in ``json``.

    A method whose one parameter is a protobuf message, from the body or
    without a `dubbo.Param`, travels as `TranscodedMessages` tells; any
    other as `RestMessages` does.

    Raises:
        TypeError: Plain HTTP cannot carry the method on ``route``.
    """
    if len(method.request) == 1 and is_message(unannotated(method.request[0].type)):
        param = param_of(method.request[0].type)
        if param is None or param.location is ParamLocation.BODY:
            return TranscodedMessages.of(method, route, json)
    if route.body != "*":
        raise TypeError("body applies to a method whose one parameter is a protobuf message")
    messages = RestMessages.of(method, json, route.variables)
    if route.response_body is None:
        return messages
    return attrs.evolve(messages, dump_response=response_dumper(method.response, route.response_body, json))


def response_dumper(response: Any, response_body: str | None, json: JsonSerialization) -> Callable[[Any], bytes]:
    """Return what writes a returned ``response`` as JSON, or only its field ``response_body`` when given.

    Raises:
        TypeError: The ``json`` serialization cannot carry ``response``, or
            ``response_body`` is given and ``response`` is no protobuf
            message with such a field.
    """
    if response_body is None:
        return dumper(json, response)

    message = unannotated(response)
    if not is_message(message) or (field := message.DESCRIPTOR.fields_by_name.get(response_body)) is None:
        raise TypeError(f"the response body names {response_body!r}, which the returned {response!r} has no field of")
    return functools.partial(_dump_field, message, cast(FieldDescriptor, field))


def _dump_field(message_type: type[Message], field: FieldDescriptor, value: Any) -> bytes:
    """Write the ``field`` of the returned ``value`` as the proto3 JSON mapping writes it within the message."""
    if not isinstance(value, message_type):
        raise TypeError(f"expected {message_type.__qualname__}, not {type(value).__qualname__}")

    try:
        if field.is_repeated:
            # A list, or a map, which is a repeated message; an empty one is left out of the message's JSON.
            empty = {} if _map_value(field) is not None else []
            picked = json_format.MessageToDict(value).get(field.json_name, empty)
        elif field.message_type is not None:
            # An unset message is written as an empty one; a well-known type as its own JSON.
            picked = json_format.MessageToDict(getattr(value, field.name))
        else:
            # The scalar alone in a message, written even at its default.
            single = message_type()
            setattr(single, field.name, getattr(value, field.name))
            picked = json_format.MessageToDict(single, always_print_fields_with_no_presence=True)[field.json_name]
    # A field out of the JSON mapping's range, such as a Timestamp past year 9999, raises ValueError.
    except ValueError as error:
        raise TypeError(f"cannot write the {message_type.__qualname__} as JSON: {error}") from None
    return dump_json(picked).encode()


def _merge(
    message: Message, fields: _FieldPath, value: Any, loc: tuple[str, ...], errors: list[dict[str, Any]]
) -> None:
    """Set the field ``fields`` leads to within ``message`` from its JSON ``value``, collecting what is wrong."""
    for field in reversed(fields):
        value = {field.name: value}
    try:
        json_format.ParseDict(value, message)
    except PARSE_ERRORS as error:
        errors.append(problem(loc, error))


def _map_value(field: FieldDescriptor) -> FieldDescriptor | None:
    """Return the value field of the entries of the map ``field``; ``None`` when it is no map."""
    entry = field.message_type
    return entry.fields_by_name["value"] if entry is not None and entry.GetOptions().map_entry else None


def _variable_path(descriptor: Descriptor | None, name: str) -> _FieldPath:
    """Return the fields the path variable ``name``, such as ``book.id``, names by their proto names.

    Raises:
        TypeError: A name is no field, a field before the last is no
            singular message, or the last is repeated or a message.
    """
    fields: list[FieldDescriptor] = []
    for part in name.split("."):
        field = descriptor.fields_by_name.get(part) if descriptor is not None else None
        if field is None:
            raise TypeError(f"the path names {name!r}, but {part!r} is no field of the request there")
        fields.append(field)
        descriptor = field.message_type
    if any(field.is_repeated for field in fields) or fields[-1].message_type is not None:
        raise TypeError(f"the path names {name!r}, which is not a single value of a scalar type")
    return tuple(fields)


@functools.cache
def _fields_by_name(descriptor: Descriptor) -> dict[str, FieldDescriptor]:
    """Return the fields of ``descriptor`` by both their proto names and their JSON names."""
    fields = {field.json_name: field for field in descriptor.fields}
    fields.update(descriptor.fields_by_name)
    return fields


def _query_path(descriptor: Any, key: str) -> _FieldPath | None:
    """Return the fields the query parameter ``key``, such as ``book.id``, names; ``None`` when it names none."""
    fields: list[FieldDescriptor] = []
    for part in key.split("."):
        # Only a singular message has fields of its own to name.
        if descriptor is None or (fields and fields[-1].is_repeated):
            return None
        field = _fields_by_name(descriptor).get(part)
        if field is None:
            return None
        fields.append(field)
        descriptor = field.message_type
    return tuple(fields)


def _scalar(field: FieldDescriptor, text: str) -> Any:
    """Return the JSON value of the text ``text`` of a path segment or query parameter for ``field``.

    A boolean is ``true`` or ``false``, or ``1`` or ``0``; any other value
    stays text, which the proto3 JSON mapping reads for numbers, enums,
    bytes and the well-known types written as strings.
    """
    if field.type == FieldDescriptor.TYPE_BOOL or (
        field.message_type is not None and field.message_type.full_name == _BOOL_VALUE
    ):
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    return text
