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

"""Where each argument of a method called over plain HTTP comes from: the path, the query, a header or the body."""

import collections.abc
import enum
import functools
import inspect
import typing
from collections.abc import Callable, Collection, Mapping, Sequence
from http import HTTPStatus
from typing import Annotated, Any, NotRequired, Required, TypedDict
from urllib.parse import parse_qs

import attrs
import msgspec

from dubbo.rpc.definition import Method, Parameter
from dubbo.rpc.params import Param, ParamLocation, param_of
from dubbo.serialization.json import JsonSerialization
from dubbo.serialization.models import ModelField
from dubbo.serialization.models.protobuf import is_message
from dubbo.transport.http.headers import field_text
from dubbo.utils.annotations import UNIONS, unannotated

from .exceptions import HttpError, problem

__all__ = ["BodyKind", "GatheredField", "RestMessages", "RestParameter", "dumper", "loader"]

_EMPTY = inspect.Parameter.empty
# The places a parameter of a class with fields may come from, field by field.
_MODEL_LOCATIONS = frozenset((ParamLocation.QUERY, ParamLocation.HEADER, ParamLocation.COOKIE, ParamLocation.FORM))
# The arguments of Param that constrain a value, and those that describe it, as msgspec.Meta takes them.
_CONSTRAINTS = ("gt", "ge", "lt", "le", "multiple_of", "pattern", "min_length", "max_length")
_DESCRIPTIONS = ("title", "description")


class BodyKind(enum.Enum):
    """What the body of a request holds.

    Attributes:
        NONE: Nothing a parameter takes.
        WHOLE: The JSON of the only parameter from the body.
        MEMBERS: The parameters from the body, as a JSON object of their
            names or a JSON array in their order.
        FORM: Form fields, ``application/x-www-form-urlencoded``.
    """

    NONE = "none"
    WHOLE = "whole"
    MEMBERS = "members"
    FORM = "form"


@attrs.define(frozen=True)
class GatheredField:
    """One field of a parameter whose class gathers several query parameters, header fields, cookies or form fields.

    Attributes:
        key: Its name in the request; lower-case for a header field.
        field: The field of the class.
        many: Whether it takes every value of a repeated one.
    """

    key: str
    field: ModelField
    many: bool


@attrs.define(frozen=True, kw_only=True)
class RestParameter:
    """Where one parameter of a method comes from in a plain HTTP request.

    Attributes:
        location: The part of the request it comes from.
        key: The name it has there; lower-case for a header field.
        type: Its type with the constraints and descriptions of its `Param`.
        convert: Converts its value from text, or from the values of its
            fields, as msgspec converts, raising `ValueError`.
        default: Its default, or ``inspect.Parameter.empty`` when it has none.
        many: Whether it takes every value of a repeated query parameter,
            header field or form field.
        param: The `Param` in its annotation, if any.
        fields: The fields of a class that gathers several values of its
            place, each under its own name, rather than one value.
    """

    location: ParamLocation
    key: str
    type: Any
    convert: Callable[[Any], Any]
    default: Any
    many: bool
    param: Param | None = None
    fields: tuple[GatheredField, ...] | None = None


@attrs.define(frozen=True)
class _Members:
    """Conversion of a body that holds several parameters, with JSON's own rules."""

    keys: tuple[str, ...]
    # The body as a JSON object of the parameters by name, those with defaults optional.
    object: Any
    load_object: Callable[[bytes], Any]
    # The body as a JSON array of every parameter in order.
    load_array: Callable[[bytes], Any]

    def load(self, body: bytes) -> dict[str, Any]:
        """Return the value of each parameter by name, from a body that is a JSON array or object."""
        if body.lstrip()[:1] == b"[":
            return dict(zip(self.keys, self.load_array(body), strict=True))
        return self.load_object(body)


@attrs.define(frozen=True, kw_only=True)
class RestMessages:
    """How the values of one unary method travel as plain HTTP messages on one route, made once for each route.

    A parameter comes from where `dubbo.Path`, `dubbo.Query`,
    `dubbo.Header`, `dubbo.Cookie`, `dubbo.Body` or `dubbo.Form` in its
    ``Annotated`` type says; a class with fields there, such as a
    dataclass or a pydantic model, gathers a value of that place for each
    of its fields, by the field's name, ``_`` being ``-`` in a header field
    name. Without one, a parameter the
    route's path template names comes from that segment; a parameter of a
    scalar type, such as ``int``, ``str``, an enum, a date or a union of
    them, from the query parameter of its name; and every other parameter,
    such as a dataclass, a model, a ``list`` or a ``dict``, from the body.

    Text becomes a value as msgspec converts it loosely: ``20`` an int,
    ``true`` or ``1`` a bool, ISO 8601 a date. ``null`` is no value; a
    parameter left out takes its default. A query parameter, header field
    or form field that repeats gives its last value, or every value to a
    parameter of a list type.

    The body is JSON, read strictly by the ``json`` serialization: the only
    body parameter whole, or else a JSON object with a member for each, or a
    JSON array of them all in order. A protobuf message comes from the body
    alone, as the whole of it.

    Attributes:
        parameters: Where each parameter comes from, in order.
        body: What the body holds.
        dump_response: Writes the returned value as the JSON of the response.
        takes_query: Whether a parameter comes from the query.
        takes_headers: Whether a parameter comes from a header field.
        takes_cookies: Whether a parameter comes from a cookie.
        load_body: Reads a body that is the only body parameter whole.
        members: Conversion of a body of several parameters.
    """

    parameters: tuple[RestParameter, ...]
    body: BodyKind
    dump_response: Callable[[Any], bytes]
    takes_query: bool
    takes_headers: bool
    takes_cookies: bool = False
    load_body: Callable[[bytes], Any] | None = None
    members: _Members | None = None

    @classmethod
    def of(cls, method: Method, json: JsonSerialization, variables: Collection[str] = ()) -> "RestMessages":
        """Return how plain HTTP carries the unary ``method``, whose route's path template names ``variables``.

        Raises:
            TypeError: A type cannot be carried as JSON, a protobuf message
                comes from elsewhere than the whole body, a parameter comes
                from a path segment the template does not name, a segment
                names no parameter, or the body is both JSON and a form.
        """
        models = json.models
        # Where each parameter comes from, and under which name.
        located = [(_location(parameter, variables, json), parameter) for parameter in method.request]
        locations = {location for (location, _), _ in located}
        if {ParamLocation.BODY, ParamLocation.FORM} <= locations:
            raise TypeError(f"{method.name} takes both a JSON body and a form, but a request has one body")
        # The path parameters and the template's variables must match one to one.
        taken = {key for (location, key), _ in located if location is ParamLocation.PATH}
        if missing := sorted(taken - set(variables)):
            raise TypeError(f"{method.name} takes {missing} from path segments that its route does not name")
        if unused := sorted(set(variables) - taken):
            raise TypeError(f"the route of {method.name} names {unused}, which no parameter takes")

        # The parameters from the JSON body; a protobuf message must be the only one.
        members = [(key, parameter) for (location, key), parameter in located if location is ParamLocation.BODY]
        for (location, _), parameter in located:
            if is_message(unannotated(parameter.type)) and (location is not ParamLocation.BODY or len(members) > 1):
                raise TypeError(
                    f"{method.name} takes the protobuf message {parameter.name!r} from elsewhere than the whole "
                    "body; a message comes from the body alone, as its only parameter"
                )

        body = BodyKind.NONE
        if ParamLocation.FORM in locations:
            body = BodyKind.FORM
        elif len(members) == 1:
            body = BodyKind.WHOLE
        elif members:
            body = BodyKind.MEMBERS

        parameters = []
        for (location, key), parameter in located:
            target = _type(parameter)
            models.prepare([target])
            parameters.append(
                RestParameter(
                    location=location,
                    key=key,
                    type=target,
                    convert=functools.partial(msgspec.convert, type=target, strict=False, dec_hook=models.decode),
                    default=parameter.default,
                    many=location in (ParamLocation.QUERY, ParamLocation.HEADER, ParamLocation.FORM)
                    and _is_sequence(parameter.type),
                    param=param_of(parameter.type),
                    fields=_gathered(parameter.type, location, json) if location in _MODEL_LOCATIONS else None,
                )
            )
        return cls(
            parameters=tuple(parameters),
            body=body,
            dump_response=dumper(json, method.response),
            takes_query=ParamLocation.QUERY in locations,
            takes_headers=ParamLocation.HEADER in locations,
            takes_cookies=ParamLocation.COOKIE in locations,
            load_body=loader(json, _type(members[0][1])) if body is BodyKind.WHOLE else None,
            members=_members(members, json) if body is BodyKind.MEMBERS else None,
        )

    def arguments(
        self,
        variables: Mapping[str, str],
        query: bytes,
        headers: Sequence[tuple[bytes, bytes]],
        body: bytes,
    ) -> tuple[Any, ...]:
        """Return the arguments of a request, from its path ``variables``, raw query, header fields and body.

        Header names are lower-case.

        Raises:
            HttpError: 422 for an argument that is missing or invalid, or a
                body that is no JSON, with each problem as a ``dict`` of
                ``loc``, which starts with ``path``, ``query``, ``header``,
                ``cookie`` or ``body``, ``msg`` and ``type``.
        """
        # Each problem is collected, so that one 422 reports them all.
        errors: list[dict[str, Any]] = []
        # The query and header fields are parsed only for a method that takes from them.
        queried = parse_qs(query.decode("utf-8", "replace"), keep_blank_values=True) if self.takes_query else {}
        fields = _header_values(headers) if self.takes_headers else {}
        cookies = _cookie_values(headers) if self.takes_cookies else {}
        # The values found in the body by name: form fields, or the members of a body of several parameters.
        members: Mapping[str, Any] = {}
        # Whether the body was invalid as a whole, which its own errors report.
        invalid = False
        if self.body is BodyKind.FORM:
            members = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        elif self.members is not None and body:
            reported = len(errors)
            members = _load(self.members.load, body, errors) or {}
            invalid = len(errors) > reported

        values = []
        for parameter in self.parameters:
            # Where a problem with this parameter is reported.
            loc: tuple[Any, ...] = (parameter.location.value, parameter.key)
            if parameter.fields is not None:
                found = {ParamLocation.QUERY: queried, ParamLocation.HEADER: fields, ParamLocation.COOKIE: cookies}
                values.append(_gather(parameter, found.get(parameter.location, members), errors))
                continue
            match parameter.location:
                case ParamLocation.PATH:
                    value = variables.get(parameter.key, _EMPTY)
                case ParamLocation.QUERY:
                    value = _pick(parameter, queried)
                case ParamLocation.HEADER:
                    value = _pick(parameter, fields)
                case ParamLocation.COOKIE:
                    value = _pick(parameter, cookies)
                case ParamLocation.FORM:
                    value = _pick(parameter, members)
                case _ if self.load_body is not None:
                    # The whole body is this parameter, read at once; an empty body is a missing one.
                    loc = ("body",)
                    if body:
                        values.append(_load(self.load_body, body, errors))
                        continue
                    value = _EMPTY
                case _:
                    # A member of a body of several, already read; one missing takes its default
                    # below, unless the body was invalid and its errors cover it.
                    value = members.get(parameter.key, _EMPTY)
                    if value is not _EMPTY or invalid:
                        values.append(None if value is _EMPTY else value)
                        continue
            values.append(_take(parameter, value, loc, errors))
        if errors:
            raise HttpError(HTTPStatus.UNPROCESSABLE_ENTITY, errors)
        return tuple(values)


def _location(parameter: Parameter, variables: Collection[str], json: JsonSerialization) -> tuple[ParamLocation, str]:
    """Return where ``parameter`` comes from and its name there.

    A `Param` decides first; then a path variable of its name; then its type, a structured one
    coming from the body and a scalar from the query. A header name defaults to the parameter's name
    with ``-`` for ``_``, and is lower-case.
    """
    param = param_of(parameter.type)
    if param is not None:
        location = param.location
        key = param.alias or _request_name(parameter.name, location)
    elif parameter.name in variables:
        location, key = ParamLocation.PATH, parameter.name
    else:
        location = ParamLocation.BODY if _is_complex(parameter.type, json) else ParamLocation.QUERY
        key = parameter.name
    return location, key.lower() if location is ParamLocation.HEADER else key


def _type(parameter: Parameter) -> Any:
    """Return the type of ``parameter`` with the constraints and descriptions of its `Param`, as msgspec.Meta."""
    param = param_of(parameter.type)
    if param is None:
        return parameter.type
    constraints = {name: value for name in _CONSTRAINTS if (value := getattr(param, name)) is not None}
    descriptions: dict[str, Any] = {
        name: value for name in _DESCRIPTIONS if (value := getattr(param, name)) is not None
    }
    if param.examples is not None:
        descriptions["examples"] = list(param.examples)
    if param.deprecated:
        descriptions["extra_json_schema"] = {"deprecated": True}
    if not constraints and not descriptions:
        return parameter.type
    if is_message(unannotated(parameter.type)):
        raise TypeError(f"{parameter.name!r} is a protobuf message, which takes no constraints or descriptions")
    annotation = _constrained(parameter.type, msgspec.Meta(**constraints)) if constraints else parameter.type
    return Annotated[annotation, msgspec.Meta(**descriptions)] if descriptions else annotation  # ty: ignore[invalid-type-form]


def _constrained(annotation: Any, constraints: msgspec.Meta) -> Any:
    """Return ``annotation`` with ``constraints``, on each member other than None of a union, as msgspec takes them."""
    base = unannotated(annotation)
    if typing.get_origin(base) not in UNIONS:
        return Annotated[annotation, constraints]
    members = tuple(arg if arg is type(None) else Annotated[arg, constraints] for arg in typing.get_args(base))
    union = typing.Union[members]  # noqa: UP007
    metadata = getattr(annotation, "__metadata__", ())
    return Annotated[union, *metadata] if metadata else union  # ty: ignore[invalid-type-form]


def _members(members: list[tuple[str, Parameter]], json: JsonSerialization) -> _Members:
    """Return the conversion of a body of the parameters ``members``, a JSON object by name or a JSON array."""
    types = {key: _type(parameter) for key, parameter in members}
    # A TypedDict reads the object form, a parameter with a default being optional;
    # a tuple reads the array form, every parameter in order.
    fields = {key: (Required if parameter.default is _EMPTY else NotRequired)[types[key]] for key, parameter in members}
    shape = TypedDict("Members", fields)  # ty: ignore[invalid-argument-type, mismatched-type-name]
    json.models.prepare([shape])
    return _Members(
        keys=tuple(types),
        object=shape,
        load_object=msgspec.json.Decoder(shape, dec_hook=json.models.decode).decode,
        load_array=msgspec.json.Decoder(
            tuple.__class_getitem__(tuple(types.values())), dec_hook=json.models.decode
        ).decode,
    )


def _request_name(name: str, location: ParamLocation) -> str:
    """Return the name a value of ``name`` has in ``location``, that of a header field with ``-`` for ``_``."""
    return name.replace("_", "-") if location is ParamLocation.HEADER else name


def _gathered(annotation: Any, location: ParamLocation, json: JsonSerialization) -> tuple[GatheredField, ...] | None:
    """Return the fields of a class ``annotation`` that gathers values of ``location``, each as it names them; else ``None``."""
    found = json.models.fields(unannotated(annotation))
    if found is None:
        return None
    return tuple(
        GatheredField(
            key=_request_name(field.key, location).lower() if location is ParamLocation.HEADER else field.key,
            field=field,
            many=location is not ParamLocation.COOKIE and _is_sequence(field.type),
        )
        for field in found
    )


def _gather(parameter: RestParameter, values: Mapping[str, Sequence[str]], errors: list[dict[str, Any]]) -> Any:
    """Return the value of ``parameter`` made of the values of its fields, or its default when none is given."""
    assert parameter.fields is not None
    data = {
        gathered.field.key: found if gathered.many else found[-1]
        for gathered in parameter.fields
        if (found := values.get(gathered.key))
    }
    if not data and parameter.default is not _EMPTY:
        return parameter.default
    # Each problem is located at its field, under the place it comes from.
    return _take(parameter, data, (parameter.location.value,), errors)


def _cookie_values(headers: Sequence[tuple[bytes, bytes]]) -> dict[str, list[str]]:
    """Return the value of each cookie by name, from every ``cookie`` header field, RFC 6265 section 5.4."""
    values: dict[str, list[str]] = {}
    for name, value in headers:
        if name == b"cookie":
            for pair in field_text(value).split(";"):
                key, separator, found = pair.partition("=")
                if separator and (key := key.strip()):
                    values.setdefault(key, []).append(found.strip().removeprefix('"').removesuffix('"'))
    return values


def _header_values(headers: Sequence[tuple[bytes, bytes]]) -> dict[str, list[str]]:
    """Return every value of each header field by name, leaving out the pseudo-header fields of HTTP/2 and HTTP/3."""
    values: dict[str, list[str]] = {}
    for name, value in headers:
        if not name.startswith(b":"):
            values.setdefault(name.decode("latin-1"), []).append(field_text(value))
    return values


def _pick(parameter: RestParameter, values: Mapping[str, Sequence[str]]) -> Any:
    """Return every value under the name of ``parameter`` when it takes many, else the last one; `_EMPTY` for none."""
    found = values.get(parameter.key)
    if not found:
        return _EMPTY
    return list(found) if parameter.many else found[-1]


def _load(load: Callable[[bytes], Any], body: bytes, errors: list[dict[str, Any]]) -> Any:
    """Return ``load(body)``, or collect what is wrong with it under ``body``."""
    try:
        return load(body)
    except ValueError as error:
        # A body that is no JSON at all, as distinct from JSON of the wrong shape.
        malformed = isinstance(error, msgspec.DecodeError) and not isinstance(error, msgspec.ValidationError)
        errors.append(problem(("body",), error, "json_invalid" if malformed else "value_invalid"))
        return None


def _take(parameter: RestParameter, value: Any, loc: tuple[Any, ...], errors: list[dict[str, Any]]) -> Any:
    """Return ``parameter`` converted from ``value``, or its default when ``value`` is missing; collect what is wrong."""
    if value is _EMPTY:
        if parameter.default is _EMPTY:
            errors.append({"loc": list(loc), "msg": "Field required", "type": "missing"})
        return parameter.default

    try:
        converted = parameter.convert(value)
    except ValueError as error:
        errors.append(problem(loc, error))
        return None
    # None comes only from a parameter left out, not from the text null.
    if converted is None and value == "null":
        errors.append({"loc": list(loc), "msg": "Expected a value, got `null`", "type": "value_invalid"})
    return converted


def _is_sequence(annotation: Any) -> bool:
    """Whether ``annotation`` stands for several values, such as ``list[int]``, or a union with such a type."""
    annotation = unannotated(annotation)
    if typing.get_origin(annotation) in UNIONS:
        return any(_is_sequence(arg) for arg in typing.get_args(annotation))
    cls = typing.get_origin(annotation) or annotation
    return (
        isinstance(cls, type)
        and issubclass(cls, (collections.abc.Sequence, collections.abc.Set))
        and not issubclass(cls, (str, bytes, bytearray))
    )


def _is_complex(annotation: Any, json: JsonSerialization) -> bool:
    """Whether ``annotation`` stands for a structured value rather than a scalar."""
    annotation = unannotated(annotation)
    if typing.get_origin(annotation) in UNIONS:
        return any(_is_complex(arg, json) for arg in typing.get_args(annotation))

    cls = typing.get_origin(annotation) or annotation
    if not isinstance(cls, type):
        return False
    if issubclass(cls, (str, bytes, bytearray)):
        return False
    if issubclass(cls, (Mapping, collections.abc.Sequence, collections.abc.Set)):
        return True
    # A class of a model library, or one msgspec reads as an object, such as a dataclass.
    return json.models.owner(cls) is not None or json.models.fields(annotation) is not None


def dumper(json: JsonSerialization, annotation: Any) -> Callable[[Any], bytes]:
    """Return what writes a value of ``annotation`` as a JSON body.

    Raises:
        TypeError: ``json`` cannot carry ``annotation``.
    """
    encode = json.codec([annotation]).encode
    return lambda value: encode((value,))


def loader(json: JsonSerialization, annotation: Any) -> Callable[[bytes], Any]:
    """Return what reads a value of ``annotation`` from a JSON body, raising `ValueError`; raise as `dumper`."""
    decode = json.codec([annotation]).decode
    return lambda data: decode(data)[0]
