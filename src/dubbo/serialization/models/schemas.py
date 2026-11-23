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

"""The JSON Schemas of types, written by msgspec, with the classes of the installed models as they describe them."""

from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

import msgspec

from .base import Mode

if TYPE_CHECKING:
    from .installed import Models

__all__ = ["merge_definitions", "type_schemas"]

# Where schemas refer to the definitions of one source until their names are final.
_PLACEHOLDER = "dubbo-schema:{source}:"


def type_schemas(
    models: "Models", entries: Sequence[tuple[Any, Mode]], ref_template: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the JSON Schema of each type in ``entries`` in its mode, and the definitions they refer to by name.

    msgspec writes the schemas, and each implementation those of its
    classes. A definition whose output differs from its input, or that
    refers to one that does, has an output definition of its own named with
    ``-Output``; a definition of an implementation whose name another took is
    named with ``-2`` and on.
    """
    modes: dict[Mode, list[int]] = {"input": [], "output": []}
    for index, (_, mode) in enumerate(entries):
        modes[mode].append(index)

    # Each implementation describes its classes once, in every mode they are in.
    wanted: dict[tuple[type, Mode], None] = {}
    for mode, indexes in modes.items():
        for cls in models.prepare([entries[index][0] for index in indexes]):
            wanted[cls, mode] = None
    made: dict[tuple[type, Mode], dict[str, Any]] = {}
    groups: list[tuple[str, dict[str, Any]]] = []
    for name, support in models.supports.items():
        owned = [key for key in wanted if models.owner(key[0]) is support]
        if owned:
            prefix = _PLACEHOLDER.format(source=name)
            schemas, definitions = support.schemas(owned, prefix + "{name}")
            made.update(zip(owned, schemas, strict=True))
            groups.append((prefix, definitions))

    # msgspec describes the types of each mode apart, with the schemas of the models in that mode.
    results: list[dict[str, Any]] = [{} for _ in entries]
    described: dict[Mode, dict[str, Any]] = {}
    for mode, indexes in modes.items():
        if not indexes:
            continue
        schemas, described[mode] = msgspec.json.schema_components(
            [entries[index][0] for index in indexes],
            # msgspec takes an empty schema, which allows any value, for none; a title stands in for it.
            schema_hook=lambda cls, mode=mode: made[cls, mode] or {"title": cls.__name__},
            ref_template=_PLACEHOLDER.format(source=mode) + "{name}",
        )
        for index, schema in zip(indexes, schemas, strict=True):
            results[index] = schema

    definitions, targets = _join_modes(described.get("input", {}), described.get("output", {}))
    results = [_refer(schema, targets) for schema in results]
    definitions = {name: _refer(definition, targets) for name, definition in definitions.items()}
    return merge_definitions(results, {}, [(_PLACEHOLDER.format(source="input"), definitions), *groups], ref_template)


def merge_definitions(
    schemas: list[dict[str, Any]],
    definitions: dict[str, Any],
    groups: Iterable[tuple[str, dict[str, Any]]],
    ref_template: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add the definitions of each group to ``definitions``, and refer to each by its final name.

    A group's schemas refer to its definitions by its prefix and their
    names; a name another took gets ``-2`` and on.
    """
    targets: dict[str, str] = {}
    for prefix, found in groups:
        for name, definition in found.items():
            final, number = name, 1
            while final in definitions:
                number += 1
                final = f"{name}-{number}"
            definitions[final] = definition
            targets[prefix + name] = ref_template.format(name=final)
    return (
        [_refer(schema, targets) for schema in schemas],
        {name: _refer(definition, targets) for name, definition in definitions.items()},
    )


def _join_modes(inputs: dict[str, Any], outputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Return the definitions of both modes, referred to as inputs are, and where each output reference goes."""
    output = _PLACEHOLDER.format(source="output")
    shared = _PLACEHOLDER.format(source="input")
    same_names = {output + name: shared + name for name in outputs}
    # An output that differs from the input of its name, or refers to one that does, keeps a definition apart.
    apart = {
        name for name, definition in outputs.items() if inputs.get(name, definition) != _refer(definition, same_names)
    }
    grown = True
    while grown:
        grown = False
        for name, definition in outputs.items():
            if name in inputs and name not in apart and any(ref[len(output) :] in apart for ref in _refs(definition)):
                apart.add(name)
                grown = True
    definitions = dict(inputs)
    targets: dict[str, str] = {}
    for name, definition in outputs.items():
        final = name
        if name not in inputs:
            definitions[name] = definition
        elif name in apart:
            final = f"{name}-Output"
            definitions[final] = definition
        targets[output + name] = shared + final
    return definitions, targets


def _refs(value: Any) -> Iterator[str]:
    """Yield every reference within ``value``."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                yield item
            else:
                yield from _refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item)


def _refer(value: Any, targets: dict[str, str]) -> Any:
    """Return a copy of ``value`` whose references point to the final names of their definitions."""
    if isinstance(value, dict):
        return {key: targets.get(item, item) if key == "$ref" else _refer(item, targets) for key, item in value.items()}
    if isinstance(value, list):
        return [_refer(item, targets) for item in value]
    return value
