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

"""How plain HTTP reaches the methods of a service: the routes they are served on."""

import enum
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, TypedDict, TypeVar, Unpack

import attrs

__all__ = [
    "HttpRoute",
    "SegmentKind",
    "delete",
    "get",
    "patch",
    "path_segments",
    "post",
    "put",
    "route",
]

T = TypeVar("T")
# The attribute of a decorated method that holds its routes, a tuple of HttpRoute, or of a class that holds its path
# prefix.
HTTP_ATTRIBUTE = "__dubbo_http__"

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
# One segment of a path template: {name}, {name.path} for a field of a protobuf message, {name:path}, or text
# without braces.
_SEGMENT = re.compile(rf"\{{({_NAME}(?:\.{_NAME})*)(?::(path))?\}}|[^{{}}]+")
_FIELD = re.compile(_NAME)
# A method name is an HTTP token, RFC 9110 section 9.
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class SegmentKind(enum.Enum):
    """What one segment of a path template matches.

    Attributes:
        LITERAL: The segment's own text.
        VARIABLE: Any one segment, which the parameter of its name takes.
        CATCH_ALL: The rest of the path, one segment or more, which the
            parameter of its name takes.
    """

    LITERAL = "literal"
    VARIABLE = "variable"
    CATCH_ALL = "catch-all"


def path_segments(path: str) -> tuple[tuple[SegmentKind, str], ...]:
    """Return the segments of a path template, each its kind and its text or the name of its variable.

    Raises:
        ValueError: ``path`` does not start with ``/``, ends with ``/`` or
            has an empty segment, a segment mixes text and a variable, a
            variable repeats, or ``{name:path}`` is not last.
    """
    if not path.startswith("/"):
        raise ValueError(f"route path {path!r} must start with '/'")
    if path == "/":
        return ()
    if path.endswith("/"):
        raise ValueError(f"route path {path!r} must not end with '/'")

    segments: list[tuple[SegmentKind, str]] = []
    parts = path[1:].split("/")
    for index, part in enumerate(parts):
        # An empty part, from "//", matches nothing and is refused here.
        match = _SEGMENT.fullmatch(part)
        if match is None:
            raise ValueError(f"route path {path!r} has a segment that is not text or one {{variable}}: {part!r}")
        name, catch_all = match.groups()
        if name is None:
            segments.append((SegmentKind.LITERAL, part))
            continue
        if catch_all and index != len(parts) - 1:
            raise ValueError(f"route path {path!r} may take {{{name}:path}} only as its last segment")
        if any(kind is not SegmentKind.LITERAL and taken == name for kind, taken in segments):
            raise ValueError(f"route path {path!r} takes {{{name}}} more than once")
        segments.append((SegmentKind.CATCH_ALL if catch_all else SegmentKind.VARIABLE, name))
    return tuple(segments)


def _methods(value: Iterable[str]) -> frozenset[str]:
    """Return the HTTP method names of ``value``, upper-case, checking each is an HTTP token."""
    if isinstance(value, str):
        raise TypeError(f"HTTP methods are a collection of names, such as ['GET'], not the string {value!r}")

    methods = frozenset(method.upper() for method in value)
    if not methods:
        raise ValueError("a route needs at least one HTTP method")
    if invalid := sorted(method for method in methods if not _TOKEN.fullmatch(method)):
        raise ValueError(f"{invalid} are not HTTP method names")
    return methods


def _status_code(_: object, __: object, value: int) -> None:
    if not 200 <= value <= 599:
        raise ValueError(f"the status of a response that returns the value must be from 200 to 599, not {value}")


def _path(_: object, __: object, value: str) -> None:
    path_segments(value)


def _body(_: object, __: object, value: str | None) -> None:
    if value is not None and value != "*" and not _FIELD.fullmatch(value):
        raise ValueError(f"body must be '*', a field name or None, not {value!r}")


def _response_body(_: object, __: object, value: str | None) -> None:
    if value is not None and not _FIELD.fullmatch(value):
        raise ValueError(f"response_body must be a field name or None, not {value!r}")


# The status of an additional response: a code, a range such as 4XX, or the default of every other status.
_RESPONSE_STATUS = re.compile(r"[1-5][0-9][0-9]|[1-5]XX|default")


def _responses(value: Mapping[int | str, Mapping[str, Any]] | None) -> Mapping[str, Mapping[str, Any]] | None:
    """Return ``value`` by the keys an OpenAPI responses object takes, such as ``404``, ``4XX`` and ``default``."""
    if value is None:
        return None

    responses = {}
    for status, response in value.items():
        key = str(status).upper() if str(status).lower() != "default" else "default"
        if not _RESPONSE_STATUS.fullmatch(key):
            raise ValueError(
                f"a response is for a status from 100 to 599, a range such as 4XX, or default, not {status!r}"
            )
        if not isinstance(response, Mapping):
            raise TypeError(f"the response of {status!r} must be a mapping, as an OpenAPI response object is")
        responses[key] = response
    return responses


@attrs.define(frozen=True, kw_only=True)
class HttpRoute:
    """The HTTP route of a method, and how documentation describes it.

    Attributes:
        path: The path template: segments of text, ``{name}`` for one
            segment that the parameter ``name`` takes, and as the last
            segment, ``{name:path}`` for the rest of the path. For a method
            whose one parameter is a protobuf message, ``{name}`` is a field
            of the message and ``{name.inner}`` a field of a field.
        methods: The HTTP methods, upper-case, such as ``GET``.
        status_code: The status of a response that returns the value, from
            200 to 599.
        body: For a method whose one parameter is a protobuf message, what
            of it the body holds, as in ``google.api.http``: ``*``, the
            default, for the whole message; a field name for that field,
            the fields outside it and the path coming from the query; or
            ``None`` for no body, every field outside the path coming from
            the query. Any other method takes ``*`` alone.
        response_body: For a method that returns a protobuf message, the
            field of it the response holds; ``None``, the default, for the
            whole message.
        summary: A short summary of the operation; ``None`` takes the first
            line of the method's docstring.
        description: What the operation does; ``None`` takes the rest of the
            method's docstring.
        tags: Groups the operation is listed under; empty for the interface
            of its service.
        deprecated: Whether the operation is deprecated, as a method marked
            with ``warnings.deprecated`` also is.
        operation_id: The unique name of the operation; ``None`` for
            ``{interface}.{method}``.
        include_in_schema: Whether documentation lists the operation. A
            route left out is still served.
        responses: Further responses the operation may answer with, by
            status, such as ``404``, ``4XX`` or ``default``, each an OpenAPI
            response object whose ``model``, a type, gives the schema of its
            JSON; one of 422, 4XX or default replaces the automatic 422 of
            invalid input.
        openapi_extra: Members merged into the OpenAPI operation object of
            the route, over those made for it.
    """

    path: str = attrs.field(validator=_path)
    methods: frozenset[str] = attrs.field(converter=_methods)
    status_code: int = attrs.field(default=200, validator=_status_code)
    body: str | None = attrs.field(default="*", validator=_body)
    response_body: str | None = attrs.field(default=None, validator=_response_body)
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = attrs.field(default=(), converter=tuple)
    deprecated: bool = False
    operation_id: str | None = None
    include_in_schema: bool = True
    responses: Mapping[str, Mapping[str, Any]] | None = attrs.field(default=None, converter=_responses)
    openapi_extra: Mapping[str, Any] | None = None

    @property
    def variables(self) -> tuple[str, ...]:
        """The names of the parameters the path template takes, in order."""
        return tuple(name for kind, name in path_segments(self.path) if kind is not SegmentKind.LITERAL)


def route(
    path: str,
    *,
    methods: Iterable[str] | None = None,
    status_code: int = 200,
    body: str | None = "*",
    response_body: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
    deprecated: bool = False,
    operation_id: str | None = None,
    include_in_schema: bool = True,
    responses: Mapping[int | str, Mapping[str, Any]] | None = None,
    openapi_extra: Mapping[str, Any] | None = None,
) -> Callable[[T], T]:
    """Route plain HTTP requests on ``path`` to a contract method, or on a contract class, prefix its routes.

    On a method, ``methods`` names the HTTP methods it takes, and
    ``status_code`` the status of its responses; the method is then reached
    on this route alone, not on ``/{interface}/{method}``. A parameter named
    in the path template takes that segment; `dubbo.Path`, `dubbo.Query`,
    `dubbo.Header`, `dubbo.Cookie`, `dubbo.Body` and `dubbo.Form` in an
    ``Annotated`` type tell where another comes from, else a scalar, such as ``int``, ``str`` or an enum, comes from the
    query and any other type from the JSON body::

        @dubbo.service("com.example.UserService")
        @dubbo.route("/users")
        class UserService(typing.Protocol):
            @dubbo.get("/{id}")
            async def get_user(self, id: int, verbose: bool = False) -> User: ...

            @dubbo.route("/{id}", methods=["PUT", "PATCH"])
            async def update(self, id: int, user: User) -> User: ...

    A method whose one parameter is a protobuf message, as generated
    from a proto file, takes the fields of the message from the path, the
    query and the body as ``body`` tells, in the manner of
    ``google.api.http``; ``response_body`` picks one field of a returned
    message. The other arguments describe the operation in documentation,
    as `HttpRoute` tells.

    A method may take several routes, one decorator each, listed from the
    top. On a class, ``path`` prefixes the route of each method, and a
    method without one is reached on ``{path}/{method}``. A route takes
    unary methods alone, and a class takes one.

    Raises:
        ValueError: The path template is invalid, ``methods`` is empty or
            names no HTTP method, ``status_code`` is not from 200 to 599,
            ``body`` or ``response_body`` names no field, or a response is
            for no status.
        TypeError: ``methods`` is a string, is missing on a method, or is
            given on a class with another argument than ``path``; or the
            class already has a route.
    """
    # The template is checked at decoration, so a wrong one fails where it is written.
    path_segments(path)
    # Whether any argument shapes or describes an operation, which a class route cannot take.
    documented = (
        body != "*"
        or response_body is not None
        or summary is not None
        or description is not None
        or bool(tags)
        or deprecated
        or operation_id is not None
        or not include_in_schema
        or responses is not None
        or openapi_extra is not None
    )
    # The route of a method; a class route, without methods, is a bare path prefix.
    taken = None
    if methods is not None:
        taken = HttpRoute(
            path=path,
            methods=methods,
            status_code=status_code,
            body=body,
            response_body=response_body,
            summary=summary,
            description=description,
            tags=tuple(tags),
            deprecated=deprecated,
            operation_id=operation_id,
            include_in_schema=include_in_schema,
            responses=responses,
            openapi_extra=openapi_extra,
        )

    def decorate(target: T) -> T:
        if isinstance(target, type):
            # vars() leaves out a prefix inherited from a base class, which the class may replace.
            if HTTP_ATTRIBUTE in vars(target):
                raise TypeError(f"{target.__qualname__!r} already has a route")
            if taken is not None or status_code != 200 or documented:
                raise TypeError("a route on a class only prefixes the paths; give the rest on each method")
            setattr(target, HTTP_ATTRIBUTE, path)
        else:
            if taken is None:
                raise TypeError("a route on a method needs its HTTP methods, as in methods=['GET']")
            # Decorators apply from the bottom, so each goes before those applied already.
            setattr(target, HTTP_ATTRIBUTE, (taken, *getattr(target, HTTP_ATTRIBUTE, ())))
        return target

    return decorate


class _Options(TypedDict, total=False):
    """The arguments of `route` other than the path and methods, as `get`, `post`, `put`, `patch` and `delete` take them."""

    status_code: int
    body: str | None
    response_body: str | None
    summary: str | None
    description: str | None
    tags: Sequence[str]
    deprecated: bool
    operation_id: str | None
    include_in_schema: bool
    responses: Mapping[int | str, Mapping[str, Any]] | None
    openapi_extra: Mapping[str, Any] | None


def get(path: str, **options: Unpack[_Options]) -> Callable[[T], T]:
    """Route ``GET`` requests on ``path`` to a contract method, as `route` does."""
    return route(path, methods=("GET",), **options)


def post(path: str, **options: Unpack[_Options]) -> Callable[[T], T]:
    """Route ``POST`` requests on ``path`` to a contract method, as `route` does."""
    return route(path, methods=("POST",), **options)


def put(path: str, **options: Unpack[_Options]) -> Callable[[T], T]:
    """Route ``PUT`` requests on ``path`` to a contract method, as `route` does."""
    return route(path, methods=("PUT",), **options)


def patch(path: str, **options: Unpack[_Options]) -> Callable[[T], T]:
    """Route ``PATCH`` requests on ``path`` to a contract method, as `route` does."""
    return route(path, methods=("PATCH",), **options)


def delete(path: str, **options: Unpack[_Options]) -> Callable[[T], T]:
    """Route ``DELETE`` requests on ``path`` to a contract method, as `route` does."""
    return route(path, methods=("DELETE",), **options)
