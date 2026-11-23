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

"""The installed model libraries, as the msgspec serializations and plain HTTP use them together."""

import typing
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from msgspec import inspect

from dubbo.extension import extensions
from dubbo.url import URL

from .base import Mode, ModelField, ModelSupport
from .schemas import type_schemas

__all__ = ["Models"]


class Models:
    """Every installed `ModelSupport`, made with one URL, and the hooks through which msgspec reaches them.

    `prepare` finds the classes msgspec does not know within a type, at
    any depth, and which implementation converts each; the hooks then look
    the class up. Methods ``encode`` and ``decode`` are the ``enc_hook``
    and ``dec_hook`` of msgspec.

    Attributes:
        supports: Every installed implementation, by name.
    """

    def __init__(self, url: URL) -> None:
        self.supports = {name: extensions.get(ModelSupport, name)(url) for name in extensions.names(ModelSupport)}
        # The implementation of each class, once asked.
        self._owners: dict[type, ModelSupport | None] = {}

    def prepare(self, types: Iterable[Any]) -> list[type]:
        """Check that an implementation converts each class msgspec does not know within ``types``; return them.

        Raises:
            TypeError: msgspec cannot carry a type, or a class within one is
                converted by no implementation, or by more than one.
        """
        found: dict[type, None] = {}
        for info in inspect.multi_type_info(list(types)):
            for cls in _custom_classes(info, set()):
                if self.owner(cls) is None:
                    raise TypeError(f"cannot carry {cls.__qualname__}: msgspec does not know it, nor does any model")
                found[cls] = None
        return list(found)

    def owner(self, cls: type) -> ModelSupport | None:
        """Return the implementation that converts ``cls``, if any.

        Raises:
            TypeError: More than one converts it.
        """
        try:
            return self._owners[cls]
        except KeyError:
            pass
        names = [name for name, support in self.supports.items() if support.supports(cls)]
        if len(names) > 1:
            raise TypeError(f"{cls.__qualname__} is supported by more than one model: {', '.join(sorted(names))}")
        owner = self._owners[cls] = self.supports[names[0]] if names else None
        return owner

    def encode(self, value: Any) -> Any:
        owner = self.owner(type(value))
        if owner is None:
            raise TypeError(f"cannot write {type(value).__qualname__}: msgspec does not know it, nor does any model")
        return owner.to_builtins(value)

    def decode(self, cls: type, data: Any) -> Any:
        owner = self._owners.get(cls)
        if owner is None:
            raise TypeError(f"cannot read {cls.__qualname__}: msgspec does not know it, nor does any model")
        return owner.from_builtins(cls, data)

    def fields(self, annotation: Any) -> tuple[ModelField, ...] | None:
        """Return the fields of a class msgspec reads as an object, or one a model converts; None for another type."""
        cls = typing.get_origin(annotation) or annotation
        if not isinstance(cls, type):
            return None
        owner = self.owner(cls)
        if owner is not None:
            return owner.fields(cls)
        info = inspect.type_info(annotation)
        if not isinstance(
            info, (inspect.DataclassType, inspect.StructType, inspect.TypedDictType, inspect.NamedTupleType)
        ):
            return None
        hints = typing.get_type_hints(cls, include_extras=True)
        return tuple(
            ModelField(key=field.encode_name, type=hints[field.name], required=field.required) for field in info.fields
        )

    def schemas(
        self, entries: Sequence[tuple[Any, Mode]], ref_template: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return the JSON Schema of each type in ``entries``, in its mode, and the definitions they refer to by name.

        msgspec writes the schemas, and each implementation those of its
        classes, as `type_schemas` names their definitions.

        Raises:
            TypeError: As `prepare` raises.
        """
        return type_schemas(self, entries, ref_template)


def _custom_classes(node: inspect.Type, seen: set[int]) -> Iterator[type]:
    """Yield the classes msgspec does not know within ``node``, a type msgspec described, at any depth."""
    from msgspec import inspect

    if id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, inspect.CustomType):
        yield node.cls
        return
    for name in node.__struct_fields__:
        value = getattr(node, name)
        for item in value if isinstance(value, tuple) else (value,):
            if isinstance(item, inspect.Field):
                item = item.type
            if isinstance(item, inspect.Type):
                yield from _custom_classes(item, seen)
