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

"""Finds the method a plain HTTP request reaches: static paths in a dict, path templates in a tree of segments."""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import attrs

from dubbo.rpc.rest import SegmentKind, path_segments

__all__ = ["Found", "RestRouter", "RouteEntry", "WrongMethod"]


@attrs.define(frozen=True, kw_only=True)
class RouteEntry:
    """A method as plain HTTP reaches it.

    Attributes:
        path: The path template.
        methods: The HTTP methods it takes, upper-case.
        group: The group of its service.
        version: The version of its service.
        target: What serves it.
    """

    path: str
    methods: frozenset[str]
    group: str
    version: str
    target: Any
    # The names the path template gives its variable segments, in order.
    variables: tuple[str, ...] = attrs.field(init=False)

    def __attrs_post_init__(self) -> None:
        names = tuple(name for kind, name in path_segments(self.path) if kind is not SegmentKind.LITERAL)
        object.__setattr__(self, "variables", names)


@attrs.define(frozen=True)
class Found:
    """The endpoint that serves a request.

    Attributes:
        endpoint: The endpoint.
        variables: The decoded path segments its template names, by name.
    """

    endpoint: RouteEntry
    variables: dict[str, str]


@attrs.define(frozen=True)
class WrongMethod:
    """The path is routed, but not for the request's method.

    Attributes:
        allowed: The methods the path takes.
    """

    allowed: frozenset[str]


@attrs.define
class _Node:
    """A segment of the path templates, with the endpoints whose templates end there."""

    endpoints: list[RouteEntry] = attrs.Factory(list)
    # The children by the literal segment that leads to each.
    literals: dict[str, "_Node"] = attrs.Factory(dict)
    # The child that takes any one segment.
    variable: "_Node | None" = None
    # The endpoints that take the rest of the path, at least one segment.
    catch_all: list[RouteEntry] = attrs.Factory(list)


class RestRouter:
    """The endpoints of a server, found by method, path, and service group and version.

    A path matches literal segments before ``{name}`` segments before
    ``{name:path}``, whatever order the endpoints are given in, and a
    trailing ``/`` is ignored. An endpoint serves a request only when its
    group and version are the ones the request names, the empty ones when
    it names none.

    ``unique`` false keeps the first of endpoints that would conflict.

    Raises:
        ValueError: Two endpoints take the same method on paths of the same
            shape, for the same group and version, and ``unique`` is true.
    """

    def __init__(self, endpoints: Iterable[RouteEntry], *, unique: bool = True) -> None:
        # Templates of literal segments only, by their segments; the others are in the tree.
        self._static: dict[tuple[str, ...], list[RouteEntry]] = {}
        self._root = _Node()
        # The path of the endpoint that took each shape, method, group and version.
        taken: dict[tuple[tuple[str, ...], str, str, str], str] = {}
        for endpoint in endpoints:
            segments = path_segments(endpoint.path)
            # The template with its variables unnamed: /a/{x} and /a/{y} have the same shape.
            shape = tuple(text if kind is SegmentKind.LITERAL else f"{{{kind.value}}}" for kind, text in segments)
            keys = [(shape, method, endpoint.group, endpoint.version) for method in endpoint.methods]
            conflict = next((key for key in keys if key in taken), None)
            if conflict is not None:
                if not unique:
                    continue
                raise ValueError(
                    f"{conflict[1]} {endpoint.path} is routed twice, also as {taken[conflict]}, "
                    f"with group {endpoint.group!r} and version {endpoint.version!r}"
                )
            taken.update(dict.fromkeys(keys, endpoint.path))
            if all(kind is SegmentKind.LITERAL for kind, _ in segments):
                self._static.setdefault(tuple(text for _, text in segments), []).append(endpoint)
            else:
                self._insert(segments, endpoint)

    def find(
        self, method: str, segments: Sequence[str], group: str | None, version: str | None, *, any_service: bool = False
    ) -> Found | WrongMethod | None:
        """Return what serves a request, whose path is ``segments``, each percent-decoded.

        ``group`` and ``version`` are ``None`` when the request does not name
        them; ``any_service`` matches endpoints of every group and version, as
        a CORS preflight request, which carries none, needs. ``None`` means no
        endpoint of the group and version serves the path.
        """
        # A trailing "/" leaves an empty last segment.
        if segments and not segments[-1]:
            segments = segments[:-1]
        # The methods of every endpoint on the path, for a 405 when none takes the request's.
        allowed: set[str] = set()
        candidates = self._static.get(tuple(segments))
        if candidates is not None:
            found = _choose(candidates, method, group, version, allowed, any_service)
            if found is not None:
                return Found(found, {})

        # Templates from the most specific; the first with an endpoint of the method wins.
        for endpoints, values in _match(self._root, segments, 0, []):
            found = _choose(endpoints, method, group, version, allowed, any_service)
            if found is not None:
                return Found(found, dict(zip(found.variables, values, strict=True)))

        return WrongMethod(frozenset(allowed)) if allowed else None

    def _insert(self, segments: tuple[tuple[SegmentKind, str], ...], endpoint: RouteEntry) -> None:
        """Add ``endpoint`` to the tree along its template's ``segments``."""
        node = self._root
        for kind, text in segments:
            if kind is SegmentKind.LITERAL:
                node = node.literals.setdefault(text, _Node())
            elif kind is SegmentKind.VARIABLE:
                if node.variable is None:
                    node.variable = _Node()
                node = node.variable
            else:
                # {name:path} is the last segment, and takes the rest of the path.
                node.catch_all.append(endpoint)
                return
        node.endpoints.append(endpoint)


def _match(
    node: _Node, segments: Sequence[str], index: int, values: list[str]
) -> Iterator[tuple[list[RouteEntry], list[str]]]:
    """Yield the endpoints whose templates match ``segments`` from ``index``, the most specific first, with their values."""
    # Literal before variable before catch-all; a variable takes no empty segment.
    if index == len(segments):
        if node.endpoints:
            yield node.endpoints, values
        return

    child = node.literals.get(segments[index])
    if child is not None:
        yield from _match(child, segments, index + 1, values)
    if node.variable is not None and segments[index]:
        yield from _match(node.variable, segments, index + 1, [*values, segments[index]])
    if node.catch_all:
        yield node.catch_all, [*values, "/".join(segments[index:])]


def _choose(
    endpoints: list[RouteEntry],
    method: str,
    group: str | None,
    version: str | None,
    allowed: set[str],
    any_service: bool,
) -> RouteEntry | None:
    """Return the endpoint of ``method``, ``group`` and ``version``, noting the methods the group and version take."""
    group = group or ""
    version = version or ""
    found: RouteEntry | None = None
    for endpoint in endpoints:
        if not any_service and (endpoint.group != group or endpoint.version != version):
            continue
        allowed |= endpoint.methods
        if found is None and method in endpoint.methods:
            found = endpoint
    return found
