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

"""Where plain HTTP takes each argument of a method from: the path, the query, a header, a cookie, the body or a form."""

import enum
from typing import Any, ClassVar

import attrs

__all__ = ["Body", "Cookie", "Form", "Header", "Param", "ParamLocation", "Path", "Query", "param_of"]


class ParamLocation(enum.Enum):
    """Where in a request a parameter's argument comes from.

    Attributes:
        PATH: A segment of the path.
        QUERY: A query parameter.
        HEADER: A header field.
        COOKIE: A cookie of the ``cookie`` header field.
        BODY: The JSON body, or a member of it.
        FORM: A field of an ``application/x-www-form-urlencoded`` body.
    """

    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    COOKIE = "cookie"
    BODY = "body"
    FORM = "form"


@attrs.define(frozen=True, kw_only=True)
class Param:
    """Where a parameter of a method reached over plain HTTP comes from, and what its value must be, in its ``Annotated`` type.

    A subclass, such as `Query`, names the place; the class itself stands
    for an instance without arguments, as in ``Annotated[int, dubbo.Query]``.
    The constraints, such as ``gt`` and ``max_length``, are checked only on
    calls over plain HTTP, as the value is converted; gRPC calls do not
    check them. Documentation shows them with the descriptions. A protobuf
    message takes none of them. The default value of the parameter
    is its default in the signature.

    Attributes:
        alias: The name it has in the request, when it differs from the
            parameter's.
        title: A title of the value.
        description: What the value is.
        examples: Example values.
        deprecated: Whether the parameter is deprecated.
        include_in_schema: Whether documentation lists the parameter.
        gt: A number the value must be greater than.
        ge: A number the value must be greater than or equal to.
        lt: A number the value must be less than.
        le: A number the value must be less than or equal to.
        multiple_of: A number the value must be a multiple of.
        min_length: The fewest characters or items the value has.
        max_length: The most characters or items the value has.
        pattern: A regular expression a text value matches.
    """

    location: ClassVar[ParamLocation]

    alias: str | None = None
    title: str | None = None
    description: str | None = None
    examples: tuple[Any, ...] | None = attrs.field(default=None, converter=attrs.converters.optional(tuple))
    deprecated: bool = False
    include_in_schema: bool = True
    gt: float | None = None
    ge: float | None = None
    lt: float | None = None
    le: float | None = None
    multiple_of: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None


class Path(Param):
    """A segment of the path, named by the route's template."""

    location = ParamLocation.PATH


class Query(Param):
    """A query parameter; a list type takes every value of a repeated one."""

    location = ParamLocation.QUERY


class Header(Param):
    """A header field, named by the parameter with ``_`` as ``-`` unless an alias names it."""

    location = ParamLocation.HEADER


class Cookie(Param):
    """A cookie, named by the parameter unless an alias names it."""

    location = ParamLocation.COOKIE


class Body(Param):
    """The JSON body, or when several parameters come from it, the member of its name in a JSON object."""

    location = ParamLocation.BODY


class Form(Param):
    """A field of an ``application/x-www-form-urlencoded`` body."""

    location = ParamLocation.FORM


def param_of(annotation: Any) -> Param | None:
    """Return the `Param` in the metadata of the ``Annotated`` type ``annotation``, if any.

    Raises:
        TypeError: The metadata holds more than one, or the base class itself.
    """
    found: list[Param] = []
    for item in getattr(annotation, "__metadata__", ()):
        # The bare class, as in Annotated[int, dubbo.Query], stands for an instance without arguments.
        if isinstance(item, type) and issubclass(item, Param):
            if item is Param:
                raise TypeError("dubbo.Param is the base of the places a parameter comes from; use one, such as Query")
            item = item()
        if isinstance(item, Param):
            if type(item) is Param:
                raise TypeError("dubbo.Param is the base of the places a parameter comes from; use one, such as Query")
            found.append(item)
    if len(found) > 1:
        raise TypeError(f"{annotation!r} names more than one place its value comes from")
    return found[0] if found else None
