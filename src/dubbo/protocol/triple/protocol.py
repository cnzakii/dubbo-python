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

from collections.abc import Mapping

from dubbo.constants import (
    COMPRESSOR_KEY,
    DEFAULT_SERIALIZATION,
    DEFAULT_TRIPLE_PORT,
    GROUP_KEY,
    MAX_RECEIVE_MESSAGE_SIZE_KEY,
    SERIALIZATION_KEY,
    SHUTWAIT_KEY,
    VERSION_KEY,
)
from dubbo.protocol.base import Protocol
from dubbo.protocol.served import ClientPool, MessagesCache, ServedExporter, ServedService, Servers
from dubbo.rpc.definition import Service
from dubbo.rpc.invocation import Invoker
from dubbo.serialization import Serialization, get_serialization
from dubbo.url import SSL_CONTEXT, URL

from .connection import TripleClient
from .invoker import TripleInvoker
from .messages import GrpcMessages, grpc_messages
from .server import TripleServer
from .settings import transport_url

__all__ = ["TripleProtocol"]

# The settings of the calls and of the invokers making them rather than of their connection.
_CALL_KEYS = frozenset({COMPRESSOR_KEY, MAX_RECEIVE_MESSAGE_SIZE_KEY, SHUTWAIT_KEY})


class TripleProtocol(Protocol):
    """Serve and call services with Triple, in its gRPC format over HTTP/2 and HTTP/3, and serve plain HTTP too.

    A method is reached at ``/{interface}/{method}``; a group and a version,
    when set, travel in the ``tri-service-group`` and ``tri-service-version``
    headers and must match exactly. The ``serialization`` parameter names the
    message format, ``protobuf`` when absent; ``json`` carries protobuf
    messages in their proto3 JSON mapping and other Python types as JSON, and the values of a format of Java objects, such as ``hessian2``,
    travel wrapped in a protobuf message. Without a port, the address uses
    port 50051.

    With ``triple.rest.enabled=true``, a server also answers plain HTTP
    requests, over HTTP/1.1, HTTP/2 and HTTP/3, to its unary methods,
    whatever their serialization: with JSON
    on ``/{interface}/{method}`` by ``GET`` or ``POST``, or on the route
    `dubbo.route` declares, as `dubbo.protocol.triple.rest.handler.RestHandler`
    describes.

    The Triple settings are the parameters after ``triple.``, as in
    ``triple.compressor``.

    The services exported on one address share a server, which listens until
    the protocol closes; each must ask for the settings the server was
    created with. Once the exporter of a service closes, its calls fail with
    ``UNAVAILABLE``. The invokers of equal connection settings and the same
    TLS context share their connections, made by the clients the ``client``
    setting names with the URL of the first, less the settings of the
    calls, such as ``compressor``.

    ``http3.enabled=true`` has a server serve HTTP/3 too, over TLS, and lets
    the invokers use it: they call over HTTP/2 until a response announces
    HTTP/3 with ``alt-svc``, then over HTTP/3 while it is connected, or over
    HTTP/3 alone with ``http3.negotiation=false``. The other ``http3.<key>``
    settings take the place of ``<key>`` for HTTP/3.
    """

    def __init__(self) -> None:
        self._servers: Servers[TripleServer] = Servers()
        self._clients: ClientPool[TripleClient] = ClientPool()
        # The shared serialization of each name, and how the methods of each definition travel in it, which the
        # invokers of a service share: a codec keeps no state between messages.
        self._codecs: MessagesCache[tuple[Serialization, Mapping[str, GrpcMessages]]] = MessagesCache()

    async def export(self, url: URL, definition: Service, invoker: Invoker) -> ServedExporter:
        url = _with_port(url)
        serialization = get_serialization(url)
        service = ServedService(
            interface=definition.interface,
            group=url.parameters.get(GROUP_KEY, ""),
            version=url.parameters.get(VERSION_KEY, ""),
            invoker=invoker,
        )

        server = await self._servers.serve(
            url, lambda: TripleServer(url), lambda server: server.add(service, definition, serialization)
        )
        return ServedExporter(server=server, service=service, url=url.replace(port=server.address[1]))

    async def refer(self, url: URL, definition: Service) -> Invoker:
        url = _with_port(url)
        serialization, messages = self._get_codecs(url, definition)
        connection = _connection_url(url)
        client = await self._clients.acquire(
            (connection, connection.attributes.get(SSL_CONTEXT)),
            lambda: TripleClient(connection),
        )

        try:
            return TripleInvoker(url, definition, client, serialization, messages, self._clients)
        except BaseException:
            await self._clients.release(client)
            raise

    def announce_shutdown(self) -> None:
        self._servers.announce_shutdown()

    async def aclose(self) -> None:
        try:
            await self._servers.aclose()
        finally:
            await self._clients.aclose()

    def _get_codecs(self, url: URL, definition: Service) -> tuple[Serialization, Mapping[str, GrpcMessages]]:
        """Return the serialization ``url`` names and how each method of ``definition`` travels in it.

        Those of a shared serialization are made once; the others for each call.

        Raises:
            ExtensionError: No serialization has that name.
            TypeError: The serialization cannot carry a method of ``definition``.
        """
        key = (url.parameters.get(SERIALIZATION_KEY, DEFAULT_SERIALIZATION), definition)

        def make() -> tuple[Serialization, Mapping[str, GrpcMessages]]:
            serialization = get_serialization(url)
            return serialization, {method.name: grpc_messages(serialization, method) for method in definition.methods}

        return self._codecs.get(key, make, lambda made: made[0].shared)


def _with_port(url: URL) -> URL:
    return url if url.port is not None else url.replace(port=DEFAULT_TRIPLE_PORT)


def _connection_url(url: URL) -> URL:
    """Return the URL of the HTTP/2 client that ``url`` is called over: `transport_url` of it, less the settings of the calls."""
    transport = transport_url(url)
    return transport.replace(
        parameters={key: value for key, value in transport.parameters.items() if key not in _CALL_KEYS}
    )
