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

"""The server of one HTTP origin, over every version it is given a handler for."""

import typing
from typing import Self

from dubbo.extension import Resource, extension_point
from dubbo.url import URL

from .h1.base import H1ExchangeHandler
from .h2.base import H2StreamHandler
from .h3.base import H3StreamHandler

__all__ = ["HttpServer"]


@extension_point("apache_dubbo.transport.http.server", default="aio")
class HttpServer(Resource, typing.Protocol):
    """Serves HTTP/1.1, HTTP/2 and HTTP/3 on one port, each version when it is given a handler.

    The implementation is named by the ``server`` parameter, ``aio`` when
    absent, and created with a URL whose host is the interface to bind, such
    as ``0.0.0.0`` for every IPv4 interface, and whose port is 0 for an
    ephemeral port. When the host is a name that resolves to several
    addresses, the port must be fixed.

    HTTP/1.1 (RFC 9112) and HTTP/2 (RFC 9113) share one TCP listener. With
    TLS, ALPN offers the versions served, ``h2`` first; a connection that
    selects none, or that is cleartext, is HTTP/2 when it opens with the
    HTTP/2 connection preface and HTTP/1.1 otherwise. HTTP/3 (RFC 9114)
    requires TLS and listens on UDP with the same port, at each address the
    TCP listener binds, or at the first address the host resolves to when it
    is served alone. While HTTP/3 is served, each final HTTP/1.1 and HTTP/2
    response that carries no ``alt-svc`` field announces it with
    ``alt-svc: h3=":<port>"``, RFC 7838.

    The TLS parameters configure TCP connections as
    `dubbo.transport.tls.get_ssl_context` reads them, and QUIC connections
    as `H3Client` describes them, with ``tls-cert-file`` required. Each
    version reads the parameters of its client, `H2Client` and `H3Client`,
    and these; HTTP/3 reads a parameter ``http3.<key>`` in place of ``<key>``,
    such as ``http3.idle-timeout``, so that its settings may differ:

    - ``max-header-list-size``: bytes of the largest request header section,
      for HTTP/1.1 the whole request head; 64 KiB when absent. A larger
      HTTP/1.1 head is answered with 431.
    - ``idle-timeout``: whole milliseconds an HTTP/1.1 connection may wait
      for its next request to begin, and a TCP connection for its first
      bytes when both TCP versions are served; for HTTP/3, as `H3Client`
      describes it. 30000 when absent, 0 for no limit.
    - ``request-header-timeout``: whole milliseconds an HTTP/1.1 request
      head may take to arrive once it begins, which also bounds the HTTP/2
      connection preface when both TCP versions are served; 10000 when
      absent, 0 for no limit.
    - ``request-body-timeout``: whole milliseconds an HTTP/1.1 request body
      may take to arrive once its head has; 30000 when absent, 0 for no
      limit. A body late is answered with 408, if no response was sent,
      and its connection closed.
    - ``ssl-handshake-timeout``: whole milliseconds a TLS or QUIC handshake
      may take; 10000 when absent.
    - ``shutwait``: whole milliseconds closing waits for running handlers;
      10000 when absent.

    Entering the server starts listening; closing it stops. The server is
    single-use and owns its listeners, connections and handler tasks.
    Each HTTP/1.1 request runs ``h1`` once, one at a time on its
    connection; a request that breaks the protocol is answered with the
    status RFC 9112 suggests, such as 400, and its connection closed. When a
    handler fails or returns before the response ends, the connection
    closes. After a complete response the connection waits for the next
    request, unless either side asked to close it or the request body was
    not read to its end. Each HTTP/2 or HTTP/3 request stream runs ``h2`` or
    ``h3`` in its own task; an ordinary handler failure resets only that
    stream, with ``INTERNAL_ERROR`` or ``H3_INTERNAL_ERROR`` unless both of
    its directions have ended. When the peer goes, or aborts the stream, the
    handler is cancelled as AnyIO cancels, so that what it shields from
    cancellation finishes first.

    Attributes:
        url: The URL the instance was created with.
        h1: The handler of each HTTP/1.1 request, or ``None`` to not serve HTTP/1.1.
        h2: The handler of each HTTP/2 request stream, or ``None`` to not serve HTTP/2.
        h3: The handler of each HTTP/3 request stream, or ``None`` to not serve HTTP/3.

    Raises:
        ValueError: No handler is given, the URL has no host or port, a
            parameter is invalid, or HTTP/3 is served without TLS or with an
            ``ssl.SSLContext``.
        OSError: A TLS file cannot be loaded.
        ModuleNotFoundError: HTTP/3 is served without its optional dependency.
    """

    url: URL
    h1: H1ExchangeHandler | None
    h2: H2StreamHandler | None
    h3: H3StreamHandler | None

    def __init__(
        self,
        url: URL,
        *,
        h1: H1ExchangeHandler | None = None,
        h2: H2StreamHandler | None = None,
        h3: H3StreamHandler | None = None,
    ) -> None:
        if h1 is None and h2 is None and h3 is None:
            raise ValueError("an HTTP server needs a handler for at least one HTTP version")

        self.url = url
        self.h1 = h1
        self.h2 = h2
        self.h3 = h3

    @property
    def address(self) -> tuple[str, int]:
        """The host and port actually bound, with port 0 resolved, shared by TCP and UDP.

        Raises:
            RuntimeError: The server is not listening.
        """
        ...

    def announce_shutdown(self) -> None:
        """Tell the connections established now to take no more requests, and keep serving.

        HTTP/2 and HTTP/3 connections receive a GOAWAY that refuses no
        request. HTTP/1.1 has no shutdown notice: an idle connection closes,
        and a busy one once its response ends, which carries
        ``connection: close`` when its head is still unsent. Clients send
        their next requests on new connections, which the server still
        serves; `aclose` stops serving.
        """
        ...

    async def aclose(self) -> None:
        """Stop accepting work, let running handlers finish, then release everything.

        New connections are refused at once, connections still choosing
        their version close, and the others are told to take no more
        requests as `announce_shutdown` does. Handlers still running when
        the grace period ends are cancelled. Connections then close once the
        responses they still queue are written, and for HTTP/3 acknowledged,
        or are aborted after a second. A cancelled close cancels the
        handlers and aborts the connections at once. Closing again waits for
        the close under way.
        """
        ...

    async def __aenter__(self) -> Self:
        """Start listening.

        Raises:
            RuntimeError: The server has already been started.
            OSError: An address cannot be bound.
            ValueError: A host name with port 0 would listen on several
                ports, one per address.
        """
        ...
