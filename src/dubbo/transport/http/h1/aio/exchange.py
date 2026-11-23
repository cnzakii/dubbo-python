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

import asyncio
import contextlib
import math
from functools import partial
from operator import itemgetter
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

import anyio
import h11r

from dubbo.transport.http.h1.base import H1ClientExchange, H1Headers, H1Request, H1Response, H1ServerExchange
from dubbo.transport.http.h1.exceptions import H1ConnectionError, H1Error, H1ExchangeError
from dubbo.transport.http.streams import OperationGuard

from .connection import AioH1Connection
from .tunnel import AioH1Tunnel

if TYPE_CHECKING:
    from .client import AioH1Client

__all__ = ["AioH1ClientExchange", "AioH1ServerExchange"]

_CONNECTION_CLOSE = (b"connection", b"close")
_NAME = itemgetter(0)


class _AioH1Exchange:
    """What the exchanges of both roles share: sending this side's message body and receiving the peer's."""

    def __init__(self, connection: AioH1Connection) -> None:
        self._connection = connection
        self._send_guard = OperationGuard(partial(H1ExchangeError, "another task is already sending on this exchange"))
        self._receive_guard = OperationGuard(
            partial(H1ExchangeError, "another task is already receiving from this exchange")
        )
        self._send_ended = False
        self._body_ended = False
        self._trailers: H1Headers = ()
        self._chunk = b""
        self._tunnel: AioH1Tunnel | None = None

    @property
    def peer(self) -> str | None:
        return self._connection.peer

    async def send_data(self, data: bytes, *, end_stream: bool = False) -> None:
        with self._send_guard:
            self._check_sendable()
            engine = self._connection.engine
            self._connection.send(engine.send_data_parts, data)
            if end_stream:
                self._connection.send(engine.end_of_message)
                self._sent_end()
            await self._drain()

    async def send_trailers(self, trailers: H1Headers) -> None:
        with self._send_guard:
            self._check_sendable()
            self._connection.send(self._connection.engine.end_of_message, trailers)
            self._sent_end()
            await self._drain()

    async def end(self) -> None:
        with self._send_guard:
            if self._send_ended:
                return
            self._check_sendable()
            self._connection.send(self._connection.engine.end_of_message)
            self._sent_end()
            await self._drain()

    async def receive_data(self, max_bytes: int = 64 * 1024) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        with self._receive_guard:
            await self._before_body()
            if not self._chunk and not await self._receive_chunk():
                return b""
            return self._take(max_bytes)

    async def receive_data_exactly(self, nbytes: int) -> bytes:
        if nbytes < 0:
            raise ValueError("nbytes must not be negative")

        with self._receive_guard:
            await self._before_body()
            # Served from the buffered chunk alone when it is long enough.
            if len(self._chunk) >= nbytes:
                return self._take(nbytes)
            parts: list[bytes] = []
            remaining = nbytes
            try:
                while remaining:
                    if not self._chunk and not await self._receive_chunk():
                        break
                    part = self._take(remaining)
                    parts.append(part)
                    remaining -= len(part)
            except asyncio.CancelledError:
                # The bytes taken so far go back to the buffer, so a cancelled read loses none.
                if parts:
                    self._chunk = b"".join(parts) + self._chunk
                raise
            return b"".join(parts)

    async def receive_trailers(self) -> H1Headers:
        with self._receive_guard:
            await self._before_body()
            self._chunk = b""
            while await self._receive_chunk():
                self._chunk = b""
            return self._trailers

    async def abort(self) -> None:
        self._abort_connection()

    def take_buffered_end(self) -> None:
        """Take the end of a body read to its last byte, if it already arrived, so the connection can be reused."""
        if not self._body_ended and not self._chunk:
            self._prefetch()

    def _abort_connection(self) -> None:
        self._connection.abort()

    def _take(self, max_bytes: int) -> bytes:
        chunk = self._chunk
        if len(chunk) <= max_bytes:
            self._chunk = b""
            return chunk
        self._chunk = chunk[max_bytes:]
        return chunk[:max_bytes]

    async def _receive_chunk(self) -> bool:
        """Receive the next body chunk into the buffer; return ``False`` once the body has ended."""
        while not self._body_ended:
            event = await self._next_event()
            # Empty data events are skipped; the trailers arrive with the end of the message.
            if type(event) is h11r.Data:
                if event.data:
                    self._chunk = event.data
                    return True
            elif type(event) is h11r.EndOfMessage:
                self._body_ended = True
                self._trailers = tuple(event.trailers)
                self._received_end()
            elif type(event) is h11r.ConnectionClosed:
                raise H1ConnectionError("the connection closed before the message ended")
            else:
                raise H1ConnectionError(f"unexpected HTTP/1.1 event {event!r}")
        return False

    async def _next_event(self) -> Any:
        """Return the next event of the message being received."""
        return await self._connection.next_event()

    async def _drain(self) -> None:
        """Wait for the socket to take more output; a cancelled wait closes the connection."""
        try:
            await self._connection.drain()
        except asyncio.CancelledError:
            self._abort_connection()
            raise

    def _take_tunnel(self) -> AioH1Tunnel:
        connection = self._connection
        connection.flush()
        connection.switch_to_tunnel()
        self._tunnel = AioH1Tunnel(connection)
        return self._tunnel

    def _check_sendable(self) -> None:
        if self._send_ended:
            raise H1ExchangeError("the message this side sends has ended")

    async def _before_body(self) -> None:
        """Prepare to receive the peer's body, or raise when it cannot be received yet."""

    def _sent_end(self) -> None:
        self._send_ended = True

    def _received_end(self) -> None:
        """Note that the peer's message ended."""

    def _prefetch(self) -> None:
        """Take the body's end or next chunk if it already arrived, without waiting for more."""
        try:
            event = self._connection.engine.next_event()
        except h11r.RemoteProtocolError as error:
            # The next receive reports it.
            self._connection.violation(error)
            return
        if type(event) is h11r.EndOfMessage:
            self._body_ended = True
            self._trailers = tuple(event.trailers)
            self._received_end()
        elif type(event) is h11r.Data:
            self._chunk = event.data


class AioH1ServerExchange(_AioH1Exchange, H1ServerExchange):
    """A request an asyncio-backed HTTP/1.1 server received, handed to its handler."""

    def __init__(
        self,
        connection: AioH1Connection,
        request: h11r.Request,
        *,
        closing: bool,
        alt_svc: bytes | None = None,
        body_deadline: float = math.inf,
    ) -> None:
        super().__init__(connection)
        # The event loop time by which the request body must have arrived.
        self._body_deadline = body_deadline
        self._request = H1Request(
            method=request.method,
            target=request.target,
            headers=tuple(request.headers),
            http_version=request.http_version,
        )
        self._closing = closing
        # The Alt-Svc field value each final response carries unless it gives its own.
        self._alt_svc = alt_svc
        self._response_sent = False
        # A request without body ends at once; the engine switches protocols on a 2xx response to CONNECT only once
        # it saw the request end.
        self._prefetch()

    @property
    def request(self) -> H1Request:
        return self._request

    @property
    def tls(self) -> bool:
        return self._connection.tls

    @property
    def response_ended(self) -> bool:
        return self._send_ended

    @property
    def complete(self) -> bool:
        """Return whether the response and the request both ended."""
        return self._send_ended and self._body_ended

    @property
    def tunnel_taken(self) -> AioH1Tunnel | None:
        return self._tunnel

    def close_after_response(self) -> None:
        """Ask the client to close the connection, if the response head is still unsent."""
        self._closing = True

    async def send_informational(self, status: int, headers: H1Headers = (), *, reason: bytes | None = None) -> None:
        with self._send_guard:
            self._check_response_unsent()
            if status == 101:
                raise H1ExchangeError("101 Switching Protocols is sent by switch_protocols")
            engine = self._connection.engine
            self._connection.send(engine.send_informational_response, status, headers, reason=reason)
            await self._drain()

    async def send_response(
        self, status: int, headers: H1Headers = (), *, reason: bytes | None = None, end_stream: bool = False
    ) -> None:
        with self._send_guard:
            self._check_response_unsent()

            # A task still receiving takes the request's end itself.
            if not self._receive_guard.busy:
                self.take_buffered_end()
            # A response before the whole request closes the connection rather than reading on.
            if (self._closing or not self._body_ended) and not _asks_to_close(headers):
                headers = (*headers, _CONNECTION_CLOSE)
            if self._alt_svc is not None and b"alt-svc" not in map(bytes.lower, map(_NAME, headers)):
                headers = (*headers, (b"alt-svc", self._alt_svc))

            engine = self._connection.engine
            self._connection.send(engine.send_response, status, headers, reason=reason)
            self._response_sent = True
            if end_stream:
                # A 2xx to CONNECT made the connection a tunnel, which has no message to end: it closes instead.
                if engine.local_state is not h11r.State.SWITCHED_PROTOCOL:
                    self._connection.send(engine.end_of_message)
                self._sent_end()
            await self._drain()

    async def switch_protocols(self, headers: H1Headers = (), *, reason: bytes | None = None) -> AioH1Tunnel:
        with self._send_guard:
            self._check_response_unsent()
            # The engine decides on an upgrade once the request has ended.
            with self._receive_guard:
                while await self._receive_chunk():
                    self._chunk = b""
            engine = self._connection.engine
            if engine.peer_state is not h11r.State.MIGHT_SWITCH_PROTOCOL:
                raise H1ExchangeError("the request asks for no upgrade")

            self._connection.send(engine.send_informational_response, 101, headers, reason=reason)
            self._response_sent = True
            self._sent_end()
            return self._take_tunnel()

    async def tunnel(self) -> AioH1Tunnel:
        if self._tunnel is not None:
            raise H1ExchangeError("the tunnel was already taken")
        if self._connection.engine.local_state is not h11r.State.SWITCHED_PROTOCOL:
            raise H1ExchangeError("no 2xx response to CONNECT was sent")
        return self._take_tunnel()

    def _check_response_unsent(self) -> None:
        if self._response_sent:
            raise H1ExchangeError("the final response was already sent")

    def _check_sendable(self) -> None:
        if not self._response_sent:
            raise H1ExchangeError("the final response was not sent")
        super()._check_sendable()

    async def _before_body(self) -> None:
        engine = self._connection.engine
        if not self._response_sent and not self._body_ended and engine.client_is_waiting_for_100_continue:
            self._connection.send(engine.send_informational_response, 100)

    async def _next_event(self) -> Any:
        try:
            with anyio.fail_after(max(self._body_deadline - asyncio.get_running_loop().time(), 0)):
                return await super()._next_event()
        except TimeoutError:
            self._refuse(408)
            raise H1ConnectionError(
                "the request body took longer to arrive than request-body-timeout", status=408
            ) from None
        except H1ConnectionError as error:
            # A body that breaks the protocol, as a malformed chunk does.
            if error.status is not None:
                self._refuse(error.status)
            raise

    def _refuse(self, status: int) -> None:
        """Answer ``status`` unless a response is begun or being written, then close the connection either way."""
        if not self._response_sent and not self._send_guard.busy:
            engine = self._connection.engine
            with contextlib.suppress(H1Error):
                self._connection.send(engine.send_response, status, ((b"content-length", b"0"), _CONNECTION_CLOSE))
                self._connection.send(engine.end_of_message)
            self._response_sent = True
            self._sent_end()
        self._connection.close()


class AioH1ClientExchange(_AioH1Exchange, H1ClientExchange):
    """A request an asyncio-backed HTTP/1.1 client sent, returned by `AioH1Client.request`."""

    def __init__(self, connection: AioH1Connection, client: "AioH1Client", *, send_ended: bool) -> None:
        super().__init__(connection)
        self._client = client
        self._send_ended = send_ended
        self._final_received = False
        self._switched = False
        self._released = False

    async def receive_response(self) -> H1Response:
        with self._receive_guard:
            if self._final_received:
                raise H1ExchangeError("the final response was already received")

            event = await self._connection.next_event()
            # A 1xx response other than 101 is returned as it is, and a later call receives the final one.
            if type(event) is h11r.InformationalResponse:
                if event.status_code == 101:
                    self._final_received = self._switched = True
            elif type(event) is h11r.Response:
                self._final_received = True
                self._switched = self._connection.engine.peer_state is h11r.State.SWITCHED_PROTOCOL
            elif type(event) is h11r.ConnectionClosed:
                raise H1ConnectionError("the server closed the connection before responding")
            else:
                raise H1ConnectionError(f"unexpected HTTP/1.1 event {event!r}")

            # After 101, or 2xx to CONNECT, the connection is a tunnel and carries no body.
            if self._switched:
                self._body_ended = True
            return H1Response(
                status=event.status_code,
                reason=event.reason,
                headers=tuple(event.headers),
                http_version=event.http_version,
            )

    async def tunnel(self) -> AioH1Tunnel:
        if self._tunnel is not None:
            raise H1ExchangeError("the tunnel was already taken")
        if not self._switched or self._connection.engine.peer_state is not h11r.State.SWITCHED_PROTOCOL:
            raise H1ExchangeError("no 101 response, or 2xx response to CONNECT, was received")
        # The connection, now a tunnel, still counts against the client's until it closes.
        self._released = True
        return self._take_tunnel()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if not self._released and self._final_received:
            self.take_buffered_end()
        self._abort_connection()

    async def _before_body(self) -> None:
        if not self._final_received:
            raise H1ExchangeError("the final response was not received")

    def _abort_connection(self) -> None:
        # A connection back in the pool belongs to the exchange after this one.
        if not self._released:
            self._released = True
            self._connection.abort()

    def _sent_end(self) -> None:
        super()._sent_end()
        self._release_when_complete()

    def _received_end(self) -> None:
        self._release_when_complete()

    def _release_when_complete(self) -> None:
        """Return the connection to the pool once both messages ended, or close it if it cannot carry another."""
        if self._released or not (self._send_ended and self._body_ended) or self._switched:
            return

        self._released = True
        connection = self._connection
        engine = connection.engine
        # Kept alive only when both sides finished cleanly and neither asked to close.
        if engine.local_state is h11r.State.DONE and engine.peer_state is h11r.State.DONE and connection.usable:
            engine.start_next_cycle()
            self._client._release(connection)
        else:
            connection.close()


def _asks_to_close(headers: Any) -> bool:
    """Return whether a ``connection`` field of ``headers`` holds the option ``close``, among others separated by commas."""
    return any(
        name.lower() == b"connection" and b"close" in (option.strip() for option in value.lower().split(b","))
        for name, value in headers
    )
