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

from http import HTTPStatus
from types import TracebackType
from typing import TYPE_CHECKING, Self

from dubbo.constants import (
    DEFAULT_SHUTWAIT,
    HEALTH_ENABLED_KEY,
    REFLECTION_ENABLED_KEY,
    REST_DEFAULT_MAPPING_KEY,
    REST_ENABLED_KEY,
    REST_KEY,
    SERVER_KEY,
    SHUTWAIT_KEY,
)
from dubbo.extension import Resource, extensions
from dubbo.protocol.served import ServedService
from dubbo.rpc.definition import Service, get_definition
from dubbo.rpc.server import ServiceInvoker
from dubbo.serialization import Serialization
from dubbo.serialization.json import JsonSerialization
from dubbo.transport.http import HttpServer
from dubbo.transport.http.h1 import H1ServerExchange
from dubbo.transport.http.h3 import H3Priority, H3ServerStream
from dubbo.transport.http.headers import HttpHeader
from dubbo.url import SSL_CONTEXT, URL
from dubbo.utils.converters import boolean, milliseconds

from .attachments import split_headers
from .connection import HTTP3_ENABLED, http3_server_url
from .grpc.headers import is_grpc
from .grpc.server import serve as serve_grpc
from .rest.cors import Cors
from .rest.handler import RestHandler
from .rest.openapi import OpenAPI
from .routes import MethodKey, ServedMethod, served_methods
from .settings import TripleSettings, transport_url
from .streams import HttpServerStream

if TYPE_CHECKING:
    from .builtin import HealthService

__all__ = ["TripleServer"]

_REST_ENABLED = f"{REST_KEY}.{REST_ENABLED_KEY}"
_REST_DEFAULT_MAPPING = f"{REST_KEY}.{REST_DEFAULT_MAPPING_KEY}"


class TripleServer(Resource):
    """Serves the services exported on one address, routing each stream by path, group and version.

    Entering it starts listening; closing it stops.
    ``max-receive-message-size`` is the bytes of the largest request, 4 MiB
    when absent. ``compressor`` names the compression of the responses, used
    for the callers whose grpc-accept-encoding lists it; responses are sent as
    they are otherwise.

    ``rest.enabled=true`` serves each unary method over plain HTTP too, any
    version, with JSON, as `dubbo.protocol.triple.rest.handler.RestHandler`
    describes; a request whose ``content-type`` is not ``application/grpc``
    is one. ``rest.enable-default-mapping=false`` then serves none on
    ``/{interface}/{method}``, only those with a route. ``rest.cors.*`` lets web pages of other origins
    call them, as `dubbo.protocol.triple.rest.cors.Cors` describes, and
    ``rest.openapi.enabled=true`` serves their OpenAPI document, as
    `dubbo.protocol.triple.rest.openapi.OpenAPI` describes.

    ``http3.enabled=true`` also serves HTTP/3, which needs TLS, on the UDP
    port of the same number, and HTTP/2 responses announce it with
    ``alt-svc``. Each other ``http3.<key>`` takes the place of ``<key>`` for
    HTTP/3, such as ``http3.max-concurrent-streams``.

    ``health-enabled=true`` adds the gRPC health service, which reports each
    exported service as serving under its interface and its service key.
    ``reflection-enabled=true`` adds the gRPC reflection service, v1 and
    v1alpha, which describes the services whose protobuf files are loaded.
    Neither is registered with a registry or served over plain HTTP.

    Raises:
        ValueError: A parameter is invalid.
    """

    def __init__(self, url: URL) -> None:
        url = transport_url(url)
        self.routes: dict[MethodKey, ServedMethod] = {}
        # Methods of the services removed since, whose callers are told to try another server.
        self._unexported: dict[MethodKey, ServedMethod] = {}
        # Seconds an unexported service waits for its running calls before they are cancelled.
        self.shutwait = url.get_parameter(SHUTWAIT_KEY, milliseconds, DEFAULT_SHUTWAIT)
        self._parameters = url.parameters
        # A TLS context given as an object, which its parameters do not name.
        self._ssl_context = url.attributes.get(SSL_CONTEXT)
        self._settings = TripleSettings.from_url(url)
        self._rest: RestHandler | None = None
        # Plain HTTP bodies are JSON, whatever a service's own serialization.
        self._rest_json: JsonSerialization | None = None
        if url.get_parameter(_REST_ENABLED, boolean, False):
            self._rest = RestHandler(
                self._settings,
                default_mapping=url.get_parameter(_REST_DEFAULT_MAPPING, boolean, True),
                cors=Cors.from_url(url),
                openapi=OpenAPI.from_url(url),
            )
            self._rest_json = JsonSerialization(url)
        http3 = url.get_parameter(HTTP3_ENABLED, boolean, False)
        self._server = extensions.get(HttpServer, url.parameters.get(SERVER_KEY))(
            http3_server_url(url), h1=self._handle_h1, h2=self._handle, h3=self._handle_h3 if http3 else None
        )

        self._builtins: set[ServedService] = set()
        self._health: HealthService | None = None
        if url.get_parameter(HEALTH_ENABLED_KEY, boolean, False):
            from . import builtin

            self._health = builtin.HealthService()
            self._add_builtin(url, self._health)
        if url.get_parameter(REFLECTION_ENABLED_KEY, boolean, False):
            from . import builtin

            self._add_builtin(url, builtin.ReflectionService(self._interfaces))
            self._add_builtin(url, builtin.ReflectionV1AlphaService(self._interfaces))

    @property
    def address(self) -> tuple[str, int]:
        return self._server.address

    def add(
        self, service: ServedService, definition: Service, serialization: Serialization, *, rest: bool = True
    ) -> None:
        """Start routing the methods of ``definition`` to ``service``, also over plain HTTP unless ``rest`` is false.

        Raises:
            TypeError: ``serialization`` cannot carry a method.
            ValueError: Another service already serves one of the paths with
                the same group and version, or one of its HTTP routes with
                the same method.
        """
        routes = served_methods(service, definition, serialization, self._rest_json if rest else None)
        taken = routes.keys() & self.routes.keys()
        if taken:
            path, group, version = next(iter(taken))
            raise ValueError(f"{path.decode()} is already exported with group {group!r} and version {version!r}")

        # Paths served again leave the unexported ones, which answer UNAVAILABLE and 503.
        unexported = {key: method for key, method in self._unexported.items() if key not in routes}
        # The REST router is rebuilt first, so that a conflicting HTTP route leaves the server unchanged.
        if self._rest is not None:
            self._rest.update({**self.routes, **routes}, unexported.values())
        self.routes |= routes
        self._unexported = unexported
        self._report_health()

    def remove(self, service: ServedService) -> None:
        """Stop routing to ``service``; its calls then fail with UNAVAILABLE, so that callers retry elsewhere."""
        self._unexported |= {key: method for key, method in self.routes.items() if method.service is service}
        self.routes = {key: method for key, method in self.routes.items() if method.service is not service}
        if self._rest is not None:
            self._rest.update(self.routes, self._unexported.values())
        self._report_health()

    def check(self, url: URL) -> None:
        """Check that ``url`` asks for the settings this server was created with.

        Raises:
            ValueError: A setting of the server differs, such as a message
                limit, the TLS files or an HTTP/2 parameter.
        """
        parameters = transport_url(url).parameters
        differing = sorted(
            key
            for key in parameters.keys() | self._parameters.keys()
            if parameters.get(key) != self._parameters.get(key)
        )
        if url.attributes.get(SSL_CONTEXT) is not self._ssl_context:
            differing.append("TLS context")
        if differing:
            host, port = self.address
            raise ValueError(f"the server at {host}:{port} runs with other {', '.join(differing)}")

    def announce_shutdown(self) -> None:
        """Report every service as not serving and send the connections a GOAWAY, while serving on."""
        if self._health is not None:
            self._health.announce_shutdown()
        self._server.announce_shutdown()

    async def aclose(self) -> None:
        # Watches of the health service would otherwise hold the server open.
        if self._health is not None:
            self._health.close()
        await self._server.aclose()

    async def __aenter__(self) -> Self:
        await self._server.__aenter__()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        # The server decides from the exception whether to close gracefully or at once.
        if self._health is not None:
            self._health.close()
        await self._server.__aexit__(exc_type, exc, traceback)

    def _add_builtin(self, url: URL, implementation: object) -> None:
        from dubbo.serialization.protobuf import ProtobufSerialization

        definition = get_definition(implementation)
        service = ServedService(
            interface=definition.interface,
            group="",
            version="",
            invoker=ServiceInvoker.from_implementation(implementation, url),
        )
        self._builtins.add(service)
        # Built-in services are gRPC's own: off the plain HTTP routes, and so out of the OpenAPI document.
        self.add(service, definition, ProtobufSerialization(url), rest=False)

    def _interfaces(self) -> set[str]:
        return {route.service.interface for route in self.routes.values()}

    def _report_health(self) -> None:
        if self._health is None:
            return

        services = {route.service for route in self.routes.values()} - self._builtins
        self._health.update({key for service in services for key in (service.interface, service.key)})

    async def _handle(self, stream: HttpServerStream) -> None:
        fields, attachments = split_headers(stream.request_headers)
        if self._rest is None or is_grpc(fields.get(HttpHeader.CONTENT_TYPE)):
            await serve_grpc(stream, fields, attachments, self.routes, self._settings, self._unexported.keys())
        else:
            await self._rest.serve_stream(stream, fields, attachments)

    async def _handle_h3(self, stream: H3ServerStream) -> None:
        # Messages are of use as they arrive, so the responses on a connection take turns, at the client's urgency.
        stream.set_priority(H3Priority(stream.priority.urgency, incremental=True))
        await self._handle(stream)

    async def _handle_h1(self, exchange: H1ServerExchange) -> None:
        if self._rest is None:
            await exchange.send_response(HTTPStatus.NOT_FOUND, ((HttpHeader.CONTENT_LENGTH, b"0"),), end_stream=True)
            return
        await self._rest.serve_exchange(exchange)
