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

"""The services a Triple server serves, and the route of each of their methods."""

import logging
from typing import Any

import attrs
from multidict import MultiDict

from dubbo.exceptions import RpcError, Status
from dubbo.protocol.generic import builtin_methods
from dubbo.protocol.served import ServedService
from dubbo.rpc.context import serving
from dubbo.rpc.definition import Method, RpcType, Service
from dubbo.rpc.invocation import Invocation, Invoke, Result
from dubbo.rpc.rest import HttpRoute
from dubbo.serialization import JavaSerialization, Serialization
from dubbo.serialization.json import JsonSerialization

from .messages import GrpcMessages, grpc_messages
from .rest.transcoding import RouteMessages, route_messages

__all__ = ["MethodKey", "ServedMethod", "ServedRoute", "invoke", "served_methods"]

_logger = logging.getLogger(__name__)

_BUILT_IN = "it is built into the protocol"

# A method is found by the path, group and version of a gRPC request.
MethodKey = tuple[bytes, bytes, bytes]


@attrs.define(frozen=True, kw_only=True)
class ServedRoute:
    """A route plain HTTP reaches a served method on.

    Attributes:
        route: The route; for a method without one of its own,
            ``/{interface}/{method}`` with ``GET`` and ``POST``.
        default: Whether the route is that of a method without one of its own.
        messages: How the values of the method travel on the route; ``None`` when they cannot.
        unbound: Why the route cannot carry the method, when it cannot.
    """

    route: HttpRoute
    default: bool
    messages: RouteMessages | None
    unbound: str = ""


@attrs.define(frozen=True, kw_only=True)
class ServedMethod:
    """How to serve one method of an exported service, made once when the service is exported.

    Attributes:
        service: The exported service.
        method: The method.
        messages: How the values of the method travel. Making them checks
            that the format can carry the method, so a method it cannot
            carry fails the export instead of every call.
        subtype: Content-subtype of the messages, compared with each request's.
        invoke: Serves the calls of the method.
        rest_routes: The routes plain HTTP reaches the method on.
    """

    service: ServedService
    method: Method
    messages: GrpcMessages
    subtype: bytes
    invoke: Invoke
    rest_routes: tuple[ServedRoute, ...]


def served_methods(
    service: ServedService, definition: Service, serialization: Serialization, json: JsonSerialization | None
) -> dict[MethodKey, ServedMethod]:
    """Return how to serve the methods of ``definition``, by ``service`` in ``serialization``, and over plain HTTP in ``json``.

    ``json`` is ``None`` where plain HTTP is off, which leaves every method without routes.

    In a format of Java objects, the service also serves the
    `dubbo.protocol.generic.builtin_methods`.

    Raises:
        TypeError: ``serialization`` cannot carry a method, or plain HTTP
            cannot carry a method on the route it declares.
    """
    group, version = service.group.encode(), service.version.encode()

    invoke = service.invoker.invoke
    methods: list[tuple[Method, Invoke]] = [(method, invoke) for method in definition.methods]
    if isinstance(serialization, JavaSerialization):
        methods += builtin_methods(definition, invoke, serialization)

    served: dict[MethodKey, ServedMethod] = {}
    for method, serve in methods:
        path = f"/{definition.interface}/{method.name}".encode()
        messages = grpc_messages(serialization, method)
        served[path, group, version] = ServedMethod(
            service=service,
            method=method,
            messages=messages,
            subtype=messages.content_subtype.encode("ascii"),
            invoke=serve,
            rest_routes=()
            if json is None
            else _rest_routes(definition, method, json, built_in=method not in definition.methods),
        )

    return served


async def invoke(
    served: ServedMethod, arguments: tuple[Any, ...], attachments: MultiDict[str | bytes], peer: str | None
) -> Result:
    """Serve one call of ``served`` with ``arguments``; a handler that raises fails the call with INTERNAL."""
    method = served.method
    invocation = Invocation(
        interface=served.service.interface,
        method=method.name,
        arguments=arguments,
        attachments=attachments,
        definition=method,
    )
    # The handler reads the attachments and the peer from the serving context.
    with serving(attachments, peer=peer):
        try:
            return await served.invoke(invocation)
        except Exception as error:
            _logger.exception(
                "Serving %s.%s raised instead of returning a result; its caller receives INTERNAL",
                invocation.interface,
                invocation.method,
            )
            return Result(error=RpcError(Status.INTERNAL, f"{type(error).__name__}: {error}"))


def _rest_routes(
    definition: Service, method: Method, json: JsonSerialization, *, built_in: bool
) -> tuple[ServedRoute, ...]:
    """Return the routes plain HTTP reaches ``method`` on, each with how it carries the method or why it cannot.

    Raises:
        TypeError: The method declares a route that plain HTTP cannot carry it on.
    """
    if method.http_routes:
        routes = []
        for route in method.http_routes:
            try:
                routes.append(ServedRoute(route=route, default=False, messages=route_messages(method, route, json)))
            except TypeError as error:
                raise TypeError(
                    f"{definition.interface}.{method.name} cannot be served on {route.path}: {error}"
                ) from None
        return tuple(routes)

    # A method without a route of its own is on the default mapping, which answers 501 when it cannot carry it;
    # the method is still served over gRPC.
    route = HttpRoute(path=f"/{definition.interface}/{method.name}", methods=("GET", "POST"))
    if built_in:
        # A built-in method, such as $invoke, is not carried over plain HTTP.
        return (ServedRoute(route=route, default=True, messages=None, unbound=_BUILT_IN),)
    if method.rpc_type is not RpcType.UNARY:
        return (
            ServedRoute(
                route=route, default=True, messages=None, unbound="a streaming method is called over gRPC alone"
            ),
        )
    try:
        return (ServedRoute(route=route, default=True, messages=route_messages(method, route, json)),)
    except TypeError as error:
        return (ServedRoute(route=route, default=True, messages=None, unbound=str(error)),)
