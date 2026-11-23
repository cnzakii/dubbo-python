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

"""The HTTP/1.1 transport contract, RFC 9110 and RFC 9112."""

import typing
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Self, TypeAlias

import attrs

from dubbo.extension import Resource, extension_point
from dubbo.url import URL

__all__ = [
    "H1Client",
    "H1ClientExchange",
    "H1ExchangeHandler",
    "H1Headers",
    "H1Request",
    "H1Response",
    "H1ServerExchange",
    "H1Tunnel",
]

# An ordered field section of raw names and values; names keep the case they were sent in.
H1Headers: TypeAlias = tuple[tuple[bytes, bytes], ...]


@attrs.frozen
class H1Request:
    """A request line and its header section, RFC 9112 section 3.

    Attributes:
        method: The request method, such as ``b"GET"``.
        target: The request target as sent: origin form such as
            ``b"/path?query"``, absolute form, authority form for CONNECT, or
            ``b"*"``.
        headers: The header section.
        http_version: ``b"1.1"`` or ``b"1.0"``.
    """

    method: bytes
    target: bytes
    headers: H1Headers
    http_version: bytes


@attrs.frozen
class H1Response:
    """A status line and its header section, RFC 9112 section 4.

    Attributes:
        status: The status code; 1xx for an interim response.
        reason: The reason phrase, which may be empty.
        headers: The header section.
        http_version: ``b"1.1"`` or ``b"1.0"``.
    """

    status: int
    reason: bytes
    headers: H1Headers
    http_version: bytes


class H1Tunnel(typing.Protocol):
    """The connection of an exchange that switched protocols, as raw bytes in both directions.

    A 101 response to an Upgrade request, or a 2xx response to CONNECT,
    hands the connection over, RFC 9110 sections 7.8 and 9.3.6. The
    connection carries no more HTTP/1.1 and belongs to the tunnel, which
    closes it. Bytes that arrived after the response, before the switch, are
    received first.

    One send and one receive may be active concurrently. Task cancellation
    propagates unchanged.
    """

    @property
    def peer(self) -> str | None:
        """The address of the other endpoint, as ``host:port`` with an IPv6 host in brackets."""
        ...

    async def receive(self, max_bytes: int = 64 * 1024) -> bytes:
        """Return one nonempty chunk of at most ``max_bytes`` bytes, or ``b""`` once the peer has finished sending.

        Raises:
            ValueError: ``max_bytes`` is not positive.
            H1ConnectionError: The connection failed or was closed locally.
        """
        ...

    async def send(self, data: bytes) -> None:
        """Send ``data``, waiting while the connection takes no more.

        Raises:
            H1ConnectionError: The connection failed, was closed, or this
                side finished sending.
        """
        ...

    async def send_eof(self) -> None:
        """Finish sending and keep receiving, as a TCP half-close.

        Raises:
            H1ConnectionError: The connection failed or was closed, or it
                cannot be half-closed, as TLS cannot.
        """
        ...

    async def aclose(self) -> None:
        """Close the connection. Closing again does nothing."""
        ...

    async def __aenter__(self) -> Self:
        """Enter the tunnel's managed lifetime."""
        ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the connection."""
        ...


class H1ServerExchange(typing.Protocol):
    """One request and its response, as the server that received the request sees it.

    A connection carries one exchange at a time; the next request, even one
    the client already sent, waits until this exchange completes. The
    request's line and header section have arrived when the handler
    receives the exchange.

    The server answers with zero or more interim responses and one final
    response, RFC 9110 section 15, then its body and, when the body is sent
    chunked, trailers. Without a ``content-length`` field, the body of an
    HTTP/1.1 response is sent chunked and that of an HTTP/1.0 response ends
    with the connection.

    One send and one receive operation may be active concurrently. Starting a
    second operation in the same direction before the first completes raises
    `H1ExchangeError`. Sends return once the connection accepts their input
    subject to transport backpressure. Task cancellation propagates
    unchanged; cancelling a send after it began closes the connection, as
    HTTP/1.1 cannot abandon one message alone.
    """

    @property
    def request(self) -> H1Request:
        """The request line and header section."""
        ...

    @property
    def peer(self) -> str | None:
        """The address of the client, as ``host:port`` with an IPv6 host in brackets."""
        ...

    @property
    def tls(self) -> bool:
        """Whether the connection runs over TLS."""
        ...

    async def receive_data(self, max_bytes: int = 64 * 1024) -> bytes:
        """Return one nonempty chunk of at most ``max_bytes`` request body bytes, or ``b""`` once the body has ended.

        The first receive answers a request that expects ``100-continue``
        with 100 Continue, unless the final response was sent.

        Raises:
            ValueError: ``max_bytes`` is not positive.
            H1ExchangeError: Another receive is active.
            H1ConnectionError: The client broke the protocol, or the
                connection failed or closed.
        """
        ...

    async def receive_data_exactly(self, nbytes: int) -> bytes:
        """Return ``nbytes`` request body bytes, or fewer, ``b""`` for none, once the body has ended.

        Cancellation puts the bytes collected so far back, so the next
        receive starts with them.

        Raises:
            ValueError: ``nbytes`` is negative.
            H1ExchangeError: Another receive is active.
            H1ConnectionError: The client broke the protocol, or the
                connection failed or closed.
        """
        ...

    async def receive_trailers(self) -> H1Headers:
        """Return the request's trailer section once its body has ended; empty unless a chunked body carried one.

        Body not yet received is discarded.

        Raises:
            H1ExchangeError: Another receive is active.
            H1ConnectionError: The client broke the protocol, or the
                connection failed or closed.
        """
        ...

    async def send_informational(self, status: int, headers: H1Headers = (), *, reason: bytes | None = None) -> None:
        """Send an interim response, whose status is 1xx other than 101, before the final response.

        Raises:
            H1ExchangeError: Another send is active, the final response was
                sent, the status is not interim, or the client speaks
                HTTP/1.0, which takes no interim response.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def send_response(
        self, status: int, headers: H1Headers = (), *, reason: bytes | None = None, end_stream: bool = False
    ) -> None:
        """Send the final response's status line and header section; ``end_stream`` also ends its body.

        ``reason`` is the reason phrase, empty when absent. A response sent
        before the request has ended carries ``connection: close``, and the
        connection closes after it.

        Raises:
            H1ExchangeError: Another send is active, the final response was
                already sent, or the status line or a field is invalid.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def send_data(self, data: bytes, *, end_stream: bool = False) -> None:
        """Send ``data`` as response body; ``end_stream`` also ends the body.

        Raises:
            H1ExchangeError: Another send is active, the final response was
                not sent or has ended, ``data`` exceeds its
                ``content-length``, or the response takes no body, as one to
                HEAD does not.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def send_trailers(self, trailers: H1Headers) -> None:
        """End a chunked response body with ``trailers``.

        Raises:
            H1ExchangeError: Another send is active, the response is not
                chunked, or it has ended.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def end(self) -> None:
        """End the response body. Ending again does nothing.

        Raises:
            H1ExchangeError: Another send is active, the final response was
                not sent, or less body was sent than its ``content-length``.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def switch_protocols(self, headers: H1Headers = (), *, reason: bytes | None = None) -> H1Tunnel:
        """Answer an Upgrade request with 101 Switching Protocols and return the connection as a tunnel.

        ``headers`` carry the ``upgrade`` and ``connection`` fields that name
        the protocol chosen. The exchange ends; the tunnel owns the connection.

        Raises:
            H1ExchangeError: Another send is active, a response was already
                sent, or the request asks for no upgrade.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def tunnel(self) -> H1Tunnel:
        """Return the connection as a tunnel after a 2xx response to CONNECT.

        The exchange ends; the tunnel owns the connection.

        Raises:
            H1ExchangeError: The request is not CONNECT, its response is not
                2xx, or the tunnel was already taken.
        """
        ...

    async def abort(self) -> None:
        """Close the connection at once, the only way HTTP/1.1 abandons an exchange."""
        ...


class H1ClientExchange(typing.Protocol):
    """One request and its response, as the client that sent the request sees it.

    `H1Client.request` sends the request line and header section. The
    response arrives as zero or more interim responses and the final one,
    RFC 9110 section 15, before its body. Once both messages end, the
    connection returns to the client's pool, unless either side closes it.

    One send and one receive operation may be active concurrently. Task
    cancellation propagates unchanged; cancelling a send after it began
    closes the connection. Closing the context closes the connection unless
    the exchange completed.
    """

    @property
    def peer(self) -> str | None:
        """The address of the server, as ``host:port`` with an IPv6 host in brackets."""
        ...

    async def send_data(self, data: bytes, *, end_stream: bool = False) -> None:
        """Send ``data`` as request body; ``end_stream`` also ends the body.

        Raises:
            H1ExchangeError: Another send is active, the body has ended, or
                ``data`` exceeds the request's ``content-length``.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def send_trailers(self, trailers: H1Headers) -> None:
        """End a chunked request body with ``trailers``.

        Raises:
            H1ExchangeError: Another send is active, the request is not
                chunked, or its body has ended.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def end(self) -> None:
        """End the request body. Ending again does nothing.

        Raises:
            H1ExchangeError: Another send is active, or less body was sent
                than the request's ``content-length``.
            H1ConnectionError: The connection failed or closed.
        """
        ...

    async def receive_response(self) -> H1Response:
        """Return the next response: an interim one, whose status is 1xx, or the final one.

        A 101 response ends the exchange; `tunnel` then takes the connection.

        Raises:
            H1ExchangeError: Another receive is active, or the final response
                was already returned.
            H1ConnectionError: The server broke the protocol, or the
                connection failed or closed first.
        """
        ...

    async def receive_data(self, max_bytes: int = 64 * 1024) -> bytes:
        """Return one nonempty chunk of at most ``max_bytes`` response body bytes, or ``b""`` once the body has ended.

        Raises:
            ValueError: ``max_bytes`` is not positive.
            H1ExchangeError: Another receive is active, or the final response
                was not received.
            H1ConnectionError: The server broke the protocol, or the
                connection failed or closed.
        """
        ...

    async def receive_data_exactly(self, nbytes: int) -> bytes:
        """Return ``nbytes`` response body bytes, or fewer, ``b""`` for none, once the body has ended.

        Cancellation puts the bytes collected so far back, so the next
        receive starts with them.

        Raises:
            ValueError: ``nbytes`` is negative.
            H1ExchangeError: Another receive is active, or the final response
                was not received.
            H1ConnectionError: The server broke the protocol, or the
                connection failed or closed.
        """
        ...

    async def receive_trailers(self) -> H1Headers:
        """Return the response's trailer section once its body has ended; empty unless a chunked body carried one.

        Body not yet received is discarded.

        Raises:
            H1ExchangeError: Another receive is active, or the final response
                was not received.
            H1ConnectionError: The server broke the protocol, or the
                connection failed or closed.
        """
        ...

    async def tunnel(self) -> H1Tunnel:
        """Return the connection as a tunnel after a 101 response, or a 2xx response to CONNECT.

        The connection leaves the client's pool for good; the tunnel owns it.

        Raises:
            H1ExchangeError: No such response was received, or the tunnel was
                already taken.
        """
        ...

    async def abort(self) -> None:
        """Close the connection at once, the only way HTTP/1.1 abandons an exchange."""
        ...

    async def __aenter__(self) -> Self:
        """Enter the exchange's managed lifetime."""
        ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the connection unless the exchange completed or its tunnel was taken."""
        ...


# An application coroutine invoked once for each request a server receives.
H1ExchangeHandler: TypeAlias = Callable[[H1ServerExchange], Awaitable[None]]


@extension_point("apache_dubbo.transport.http.h1.client", default="aio")
class H1Client(Resource, typing.Protocol):
    """A pool of HTTP/1.1 connections to one server.

    The implementation is named by the ``client`` parameter, ``aio`` when
    absent, and created with a URL whose host and port name the server. The
    TLS parameters configure it as `dubbo.transport.tls.get_ssl_context`
    reads them, offering ``http/1.1`` through ALPN, and these parameters
    configure the pool:

    - ``max-connections``: connections open at once, idle ones included;
      100 when absent. A request waits while all are in use.
    - ``idle-timeout``: whole milliseconds an idle connection is kept for
      reuse; 5000 when absent, 0 to keep none.
    - ``max-header-list-size``: bytes of the largest response head
      accepted, status line included; 64 KiB when absent.
    - ``connect-timeout``: whole milliseconds to establish a connection,
      the TLS handshake included; 3000 when absent.

    Each exchange has a connection to itself, never pipelined. The client
    may be shared by concurrent callers. Task cancellation propagates
    unchanged.

    Attributes:
        url: The URL the instance was created with.
    """

    url: URL

    def __init__(self, url: URL) -> None:
        self.url = url

    async def request(
        self, method: bytes, target: bytes, headers: H1Headers, *, end_stream: bool = False
    ) -> H1ClientExchange:
        """Send a request line and header section on an idle or new connection, and return the exchange.

        ``headers`` carry ``host``, which HTTP/1.1 requires, and
        ``content-length``, or ``transfer-encoding: chunked``, when a body
        follows; a request with neither has no body, RFC 9112 section 6.3.
        ``end_stream`` also ends the body.

        Raises:
            RuntimeError: The client is closed.
            H1ExchangeError: The request line or a field is invalid.
            H1ConnectionError: A connection cannot be established, or fails
                while the request is sent.
        """
        ...

    async def aclose(self) -> None:
        """Close the idle connections, and each in use once its exchange completes, or abort it after a grace period.

        A cancelled close aborts every connection at once. Tunnels taken
        from the client close too. Closing again waits for the close under
        way.
        """
        ...
