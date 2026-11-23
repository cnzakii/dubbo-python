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

"""The contract of the model libraries whose classes the msgspec serializations and REST convert."""

import typing
from collections.abc import Sequence
from typing import Any, Literal

import attrs

from dubbo.extension import extension_point
from dubbo.url import URL

__all__ = ["Mode", "ModelField", "ModelSupport"]

# What a schema describes: the data a value is read from, or the data it is written as.
Mode = Literal["input", "output"]


@attrs.define(frozen=True)
class ModelField:
    """One field of a model class.

    Attributes:
        key: Its name in the data of the model, its alias where it has one.
        type: Its type annotation, which may carry constraints of its library.
        required: Whether the model needs it.
        description: What it is, as the model describes it.
    """

    key: str
    type: Any
    required: bool
    description: str | None = None


@extension_point("apache_dubbo.serialization.model")
class ModelSupport(typing.Protocol):
    """Converts the classes of one model library that msgspec does not know, such as pydantic models.

    Every installed implementation is used, as `dubbo.serialization.models.Models`
    gathers them: the ``json`` and ``msgpack`` serializations and plain HTTP
    ask which one supports each class msgspec does not know, once, when a
    codec or the plain HTTP messages of a route are made, and a class two of
    them support is refused. One instance is made with the URL of the serialization or the server that
    uses it. The conversions run in the event loop for every value, so they
    keep no state between values and must be quick.
    """

    def __init__(self, url: URL) -> None: ...

    def supports(self, cls: type) -> bool:
        """Whether this implementation converts instances of ``cls``."""
        ...

    def to_builtins(self, value: Any) -> Any:
        """Return ``value`` as dicts with string keys, lists, str, int, float, bool and None.

        Raises:
            TypeError: ``value`` cannot be written.
        """
        ...

    def from_builtins(self, cls: type, data: Any) -> Any:
        """Return the instance of ``cls`` that ``data`` stands for, validated as the library validates.

        Raises:
            ValueError: ``data`` stands for no valid instance; the message says why.
        """
        ...

    def schemas(
        self, entries: Sequence[tuple[type, Mode]], ref_template: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return the JSON Schema of each class in ``entries`` in its mode, and the definitions they refer to by name.

        A schema refers to a definition by ``ref_template`` with ``{name}``
        for its name. A class whose input and output differ, as through
        aliases, has a definition of each, under names apart.
        """
        ...

    def fields(self, cls: type) -> tuple[ModelField, ...] | None:
        """Return the fields of ``cls``, for a parameter that gathers several values of a request; None for none."""
        ...
