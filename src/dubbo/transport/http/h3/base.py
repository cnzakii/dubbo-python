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

import typing
from collections.abc import Awaitable, Callable
from enum import IntEnum
from types import TracebackType
from typing import NamedTuple, Self, TypeAlias

import attrs

from dubbo.extension import Resource, extension_point
from dubbo.url import URL

__all__ = [
    "H3Client",
    "H3ClientStream",
    "H3ErrorCode",
    "H3Header",
    "H3Headers",
    "H3Priority",
    "H3SensitiveHeader",
    "H3ServerStream",
    "H3Settings",
    "H3Stream",
    "H3StreamHandler",
]


class H3SensitiveHeader(NamedTuple):
    """A field that QPACK must never add to a dynamic table, RFC 9204 section 7.1.3, such as a credential.

    Attributes:
        name: The lowercase field name.
        value: The raw field value.
    """

    name: bytes
    value: bytes


# A lowercase HTTP/3 field name and its raw value; received fields are always plain pairs.
H3Header: TypeAlias = tuple[bytes, bytes] | H3SensitiveHeader

# An ordered field section; repeated field names remain separate entries.
H3Headers: TypeAlias = tuple[H3Header, ...]


class H3Priority(NamedTuple):
    """The extensible priority of a response, RFC 9218 section 4.

    A client signals it in the ``priority`` header field of its request or
    in PRIORITY_UPDATE frames; a server schedules the responses that share a
    connection by it. Responses are sent most urgent first; among those of
    one urgency, incremental ones share the connection in turn, and the
    others are sent one at a time, in the order of their streams.

    Attributes:
        urgency: From 0, the most urgent, to 7.
        incremental: Whether the response is of use as it arrives.
    """

    urgency: int = 3
    incremental: bool = False


@attrs.frozen(kw_only=True)
class H3Settings:
    """The SETTINGS a peer announced, RFC 9114 section 7.2.4 and the extensions this transport supports.

    Attributes:
        max_field_section_size: Bytes of the largest field section the peer
            accepts.
        qpack_max_table_capacity: Bytes of QPACK dynamic table the peer's
            decoder allows, RFC 9204 section 5.
        qpack_blocked_streams: Streams the peer lets this side's QPACK
            encoder block, RFC 9204 section 5.
        enable_connect_protocol: Whether the peer accepts extended CONNECT,
            RFC 9220.
        h3_datagram: Whether the peer accepts HTTP datagrams, RFC 9297.
    """

    max_field_section_size: int
    qpack_max_table_capacity: int
    qpack_blocked_streams: int
    enable_connect_protocol: bool
    h3_datagram: bool


class H3ErrorCode(IntEnum):
    """The HTTP/3 error codes of RFC 9114 section 8.1, RFC 9204 section 6 and RFC 9297 section 5.2.

    They close a connection, abort a stream or stop reading one. A code
    received from a peer may be a plain ``int`` outside this enumeration.

    Attributes:
        H3_NO_ERROR: Not the result of an error, such as asking a client to
            stop sending a request whose complete response was sent.
        H3_GENERAL_PROTOCOL_ERROR: An unspecific protocol error.
        H3_INTERNAL_ERROR: An unexpected internal error.
        H3_STREAM_CREATION_ERROR: The peer created a stream it may not create.
        H3_CLOSED_CRITICAL_STREAM: A stream the connection requires was closed.
        H3_FRAME_UNEXPECTED: A frame arrived where it is not permitted.
        H3_FRAME_ERROR: A frame had an invalid layout or size.
        H3_EXCESSIVE_LOAD: The peer behaves in a way that might generate excessive load.
        H3_ID_ERROR: A stream or push identifier was used incorrectly.
        H3_SETTINGS_ERROR: A SETTINGS frame had invalid content.
        H3_MISSING_SETTINGS: No SETTINGS frame began the control stream.
        H3_REQUEST_REJECTED: The request was refused before any application processing.
        H3_REQUEST_CANCELLED: The request or its response is no longer needed.
        H3_REQUEST_INCOMPLETE: The stream ended before the request was complete.
        H3_MESSAGE_ERROR: A message was malformed.
        H3_CONNECT_ERROR: The connection of a CONNECT request was reset or closed abnormally.
        H3_VERSION_FALLBACK: The request must be retried over HTTP/1.1.
        QPACK_DECOMPRESSION_FAILED: A field section could not be decoded.
        QPACK_ENCODER_STREAM_ERROR: An instruction on the QPACK encoder stream could not be read.
        QPACK_DECODER_STREAM_ERROR: An instruction on the QPACK decoder stream could not be read.
        H3_DATAGRAM_ERROR: A datagram or capsule was malformed, or arrived for a
            request that gives datagrams no meaning.
    """

    H3_NO_ERROR = 0x0100
    H3_GENERAL_PROTOCOL_ERROR = 0x0101
    H3_INTERNAL_ERROR = 0x0102
    H3_STREAM_CREATION_ERROR = 0x0103
    H3_CLOSED_CRITICAL_STREAM = 0x0104
    H3_FRAME_UNEXPECTED = 0x0105
    H3_FRAME_ERROR = 0x0106
    H3_EXCESSIVE_LOAD = 0x0107
    H3_ID_ERROR = 0x0108
    H3_SETTINGS_ERROR = 0x0109
    H3_MISSING_SETTINGS = 0x010A
    H3_REQUEST_REJECTED = 0x010B
    H3_REQUEST_CANCELLED = 0x010C
    H3_REQUEST_INCOMPLETE = 0x010D
    H3_MESSAGE_ERROR = 0x010E
    H3_CONNECT_ERROR = 0x010F
    H3_VERSION_FALLBACK = 0x0110
    QPACK_DECOMPRESSION_FAILED = 0x0200
    QPACK_ENCODER_STREAM_ERROR = 0x0201
    QPACK_DECODER_STREAM_ERROR = 0x0202
    H3_DATAGRAM_ERROR = 0x33


class H3Stream(typing.Protocol):
    """One HTTP/3 request stream: a bidirectional QUIC stream carrying one request and its response, RFC 9114 section 4.1.

    `H3ServerStream` and `H3ClientStream` add what only one role does: a
    client sends the request and receives interim and final responses; a
    server receives the request and sends them. Both then send and receive
    the message body, the optional trailing field section, and the HTTP
    datagrams of RFC 9297 when both sides enable them.

    Callers use each direction in order: the header section, zero or more
    data operations, then trailers or an end operation. One send, one
    receive and one datagram receive may be active concurrently. Starting a
    second operation of the same kind before the first completes raises
    `H3UsageError`; operations are never silently queued. Outbound
    operations return after the connection accepts their complete input
    subject to flow control and transport backpressure; they do not wait for
    peer processing.

    Implementations translate network-backend and protocol-engine failures
    into Dubbo's transport exception hierarchy. The two directions fail
    separately: a RESET_STREAM from the peer fails receives, and a
    STOP_SENDING from the peer fails sends, each with `H3StreamResetError`.
    Loss of the owning connection raises `H3ConnectionError`. Once the peer
    ends its direction normally, receives return what is left, then ``b""``.
    Task cancellation propagates unchanged.

    Cancelling an outbound operation after protocol submission aborts the
    stream before propagating cancellation. Cancelling a receive leaves the
    stream usable; an exact receive puts the bytes it collected back at the
    front, without returning their flow-control credit again. Closing the
    context aborts the request in both directions, as `reset` does with
    `H3ErrorCode.H3_REQUEST_CANCELLED`, while the local direction has not
    ended. Once it has, closing stops reading a peer direction that has not
    ended with STOP_SENDING, whose code is `H3ErrorCode.H3_NO_ERROR` for a server that sent its whole
    response, RFC 9114 section 4.1, and `H3ErrorCode.H3_REQUEST_CANCELLED`
    otherwise. A client keeps a whole response it received before such a
    request readable; only its sends raise `H3StreamResetError`. Data that
    arrives after the context closes is discarded, and its flow-control
    credit released.
    """

    @property
    def stream_id(self) -> int:
        """The QUIC stream identifier, unique on its connection."""
        ...

    @property
    def peer(self) -> str | None:
        """The address of the other endpoint, as ``host:port`` with an IPv6 host in brackets.

        It names the address the connection uses when the stream is created,
        and does not follow a later migration. ``None`` when the transport
        does not report one.
        """
        ...

    @property
    def remote_settings(self) -> H3Settings | None:
        """The SETTINGS the peer announced on the stream's connection, or ``None`` before they arrive."""
        ...

    @property
    def max_datagram_size(self) -> int | None:
        """Bytes of the largest HTTP datagram payload the stream can send now.

        ``None`` when either side did not enable HTTP datagrams, or the
        peer's QUIC limits leave no room.
        """
        ...

    async def send_data(self, data: bytes, *, end_stream: bool = False) -> None:
        """Send ``data`` as message body, waiting for flow-control and transport capacity; ``end_stream`` also ends the local direction.

        Raises:
            H3StreamResetError: The peer asked this side to stop sending.
            H3UsageError: Another send is active, the header section was not
                sent, or the local direction has ended.
            H3StreamError: The operation is invalid for this stream.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def send_trailers(self, trailers: H3Headers) -> None:
        """Send ``trailers``, the ordered trailing field section, and end the local direction.

        Raises:
            H3StreamResetError: The peer asked this side to stop sending.
            H3UsageError: Another send is active, the header section was not
                sent, or the local direction has ended.
            H3StreamError: The field section is invalid for this stream.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def end(self) -> None:
        """End the local direction without trailers.

        The operation is idempotent after the local direction has ended.

        Raises:
            H3StreamResetError: The peer asked this side to stop sending.
            H3UsageError: Another send is active, or the header section was not
                sent.
            H3StreamError: The operation is invalid for this stream.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def receive_data(self, max_bytes: int = 64 * 1024) -> bytes:
        """Return one nonempty chunk of at most ``max_bytes`` message body bytes, or ``b""`` once the peer has ended its direction.

        The bytes returned count as consumed for QUIC flow control. A client
        receives body only after the final response.

        Raises:
            ValueError: ``max_bytes`` is not positive.
            H3StreamResetError: The peer aborted its direction.
            H3UsageError: Another receive is active, or a client has not
                received the final response.
            H3StreamError: The receive state is invalid.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def receive_data_exactly(self, nbytes: int) -> bytes:
        """Return ``nbytes`` message body bytes, or fewer, ``b""`` for none, once the peer has ended its direction.

        Bytes collected toward the result count as consumed for flow control
        as they are taken. Cancellation puts the bytes collected so far back,
        so the next receive starts with them.

        Raises:
            ValueError: ``nbytes`` is negative.
            H3StreamResetError: The peer aborted its direction.
            H3UsageError: Another receive is active, or a client has not
                received the final response.
            H3StreamError: The receive state is invalid.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def receive_trailers(self) -> H3Headers | None:
        """Return the peer's ordered trailing field section after its body, or ``None`` when it ended its direction without one.

        Body not yet received is discarded.

        Raises:
            H3StreamResetError: The peer aborted its direction.
            H3UsageError: Another receive is active, or a client has not
                received the final response.
            H3StreamError: The receive state is invalid.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    def send_datagram(self, data: bytes) -> None:
        """Queue ``data`` as one HTTP datagram of the stream, RFC 9297.

        Datagrams are unreliable and unordered: one may be lost, or dropped
        when the connection queues too many, without notice. The extension
        the request uses, such as CONNECT-UDP, gives them their meaning.

        Raises:
            H3StreamError: Either side did not enable HTTP datagrams,
                ``data`` exceeds `max_datagram_size`, or the stream cannot
                send now: a server has not sent its response, or the local
                direction has ended.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def receive_datagram(self) -> bytes | None:
        """Return the next HTTP datagram of the stream, or ``None`` once the stream can receive no more.

        Datagrams that arrive while none is awaited wait in a bounded queue,
        which drops the oldest when full. The stream receives no more once
        the peer's direction has ended or failed, or the stream closed.

        Raises:
            H3UsageError: Another datagram receive is active.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def reset(self, error_code: int = H3ErrorCode.H3_REQUEST_CANCELLED) -> None:
        """Abort the request in both directions with ``error_code``, a registered or extension HTTP/3 error code.

        It sends RESET_STREAM for a local direction that has not ended and
        STOP_SENDING for a peer direction that has not ended, RFC 9114
        section 4.1.1.

        Raises:
            ValueError: ``error_code`` is outside the QUIC variable-length
                integer range.
            H3StreamError: The stream cannot be aborted in its current state.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def stop_sending(self, error_code: int = H3ErrorCode.H3_NO_ERROR) -> None:
        """Stop reading the peer's direction with STOP_SENDING and ``error_code``, keeping the local direction open.

        Unread body is discarded and its flow-control credit released; later
        receives raise `H3StreamError`. A server that sent its whole response
        asks so with `H3ErrorCode.H3_NO_ERROR`, RFC 9114 section 4.1. It does
        nothing once the peer's direction has ended.

        Raises:
            ValueError: ``error_code`` is outside the QUIC variable-length
                integer range.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def __aenter__(self) -> Self:
        """Enter this stream's managed lifetime."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the stream without suppressing an active exception.

        A direction that has not ended is aborted as the class describes, and
        unread flow-control credit is released. A cleanup failure is
        suppressed only when preserving an exception already leaving the
        context.

        Raises:
            H3Error: Cleanup fails and no exception is already active.
        """
        ...


class H3ServerStream(H3Stream, typing.Protocol):
    """A request stream as the server that accepted it sees it.

    The request's header section has arrived when the handler receives the
    stream. The server answers with zero or more interim responses and then
    one final response, RFC 9114 section 4.1, before any body.
    """

    @property
    def request_headers(self) -> H3Headers:
        """The request's ordered header section, pseudo-header fields first."""
        ...

    @property
    def priority(self) -> H3Priority:
        """The priority the response is scheduled with: the client's signal until `set_priority` replaces it."""
        ...

    def set_priority(self, priority: H3Priority) -> None:
        """Schedule the response with ``priority`` in place of the client's signal, RFC 9218 section 8.

        Raises:
            ValueError: The urgency is not from 0 to 7.
            H3StreamError: The stream has closed.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def send_informational(self, headers: H3Headers) -> None:
        """Send an interim response, whose ``:status`` is 1xx other than 101, before the final response.

        Raises:
            H3StreamResetError: The peer asked this side to stop sending.
            H3UsageError: Another send is active, or the final response was
                sent.
            H3StreamError: The field section is invalid.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...

    async def send_response(self, headers: H3Headers, *, end_stream: bool = False) -> None:
        """Send the final response's header section; ``end_stream`` also ends the local direction.

        Raises:
            H3StreamResetError: The peer asked this side to stop sending.
            H3UsageError: Another send is active, or the final response was
                already sent.
            H3StreamError: The field section is invalid.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...


class H3ClientStream(H3Stream, typing.Protocol):
    """A request stream as the client that opened it sees it.

    `H3Client.open_stream` sends the request's header section. The response
    arrives as zero or more interim header sections and then the final one,
    RFC 9114 section 4.1, before any body.
    """

    async def receive_response(self) -> H3Headers:
        """Return the next response header section: an interim one, whose ``:status`` is 1xx, or the final one.

        Callers receive until the final response; interim responses that
        nobody receives stay queued before it, up to five: one more resets
        the stream with ``H3_EXCESSIVE_LOAD``.

        Raises:
            H3StreamResetError: The peer aborted its direction.
            H3UsageError: Another receive is active, or the final response was
                already returned.
            H3StreamError: The peer ended the stream without a final response,
                or it sent too many interim responses.
            H3ConnectionError: The owning connection failed or closed.
        """
        ...


# An application coroutine invoked once for each request stream a client opens.
H3StreamHandler: TypeAlias = Callable[[H3ServerStream], Awaitable[None]]


@extension_point("apache_dubbo.transport.http.h3.client", default="aio")
class H3Client(Resource, typing.Protocol):
    """Reusable client-side HTTP/3 connections to one endpoint.

    The implementation is named by the ``client`` parameter, ``aio`` when
    absent, and created with a URL whose host and port name the server.

    HTTP/3 always runs over TLS 1.3: ``ssl-enabled=true`` is required, and
    the client reads ``ca-cert-file``, ``tls-cert-file`` and
    ``tls-key-file`` and checks the server name as
    `dubbo.transport.tls.get_ssl_context` describes. An ``ssl.SSLContext``
    in the URL attributes cannot configure QUIC and is rejected. ALPN
    selects ``h3``.

    These parameters configure each connection, for the side that sets them:

    - ``max-concurrent-streams``: request streams the peer may open at once;
      100 when absent. Only a server accepts request streams; a client opens
      at most as many as the server allows.
    - ``initial-window-size``: bytes the peer may send on one stream before
      it waits for more credit. When absent, the window starts at 1 MiB and
      grows with the measured bandwidth-delay product, up to 16 MiB.
    - ``connection-initial-window-size``: bytes the peer may send on the
      connection before it waits for more credit; 16 MiB when absent.
    - ``max-header-list-size``: bytes of the largest field section accepted,
      advertised as SETTINGS_MAX_FIELD_SECTION_SIZE; 64 KiB when absent.
    - ``keep-alive-interval``: whole milliseconds without inbound traffic
      before a PING keeps the connection alive; no PING when absent.
    - ``idle-timeout``: whole milliseconds without traffic before the
      connection closes silently, as QUIC negotiates the smaller of both
      sides' values; 30000 when absent, 0 for none.
    - ``qpack-max-table-capacity``: bytes of QPACK dynamic table the peer's
      encoder may use for the field sections this side receives, advertised
      as SETTINGS_QPACK_MAX_TABLE_CAPACITY; 4096 when absent, 0 for none.
    - ``qpack-blocked-streams``: streams the peer's QPACK encoder may leave
      waiting for dynamic table updates, advertised as
      SETTINGS_QPACK_BLOCKED_STREAMS; 100 when absent.
    - ``h3-datagram``: ``true`` to accept HTTP datagrams, RFC 9297, through
      QUIC DATAGRAM frames, advertised as SETTINGS_H3_DATAGRAM; ``false``
      when absent. Both sides must enable it.
    - ``enable-connect-protocol``: ``true`` for a server to accept extended
      CONNECT, RFC 9220, whose request carries ``:protocol``, advertised as
      SETTINGS_ENABLE_CONNECT_PROTOCOL; ``false`` when absent.
    - ``max-concurrent-uni-streams``: unidirectional streams the peer may
      open at once; 3 when absent, the fewest HTTP/3 works with.
    - ``initial-uni-window-size``: bytes the peer may send on one
      unidirectional stream; 256 KiB when absent.
    - ``max-ack-delay``: whole milliseconds this side may delay an
      acknowledgement, below 16384; 25 when absent.
    - ``ack-delay-exponent``: exponent of the ACK delays this side encodes,
      from 0 to 20; 3 when absent.
    - ``disable-active-migration``: ``true`` to forbid the peer to move the
      connection to another address; ``false`` when absent.
    - ``congestion-control``: the algorithm of this side's sending,
      ``cubic``, ``reno`` or ``bbr``; ``cubic`` when absent.

    And this one, for the client alone:

    - ``connect-timeout``: whole milliseconds to complete the QUIC
      handshake; 3000 when absent.

    ``keep-alive-timeout`` does not apply: QUIC detects an unresponsive
    peer through loss recovery and the idle timeout, so it is ignored.

    Each connection of the client has a UDP socket of its own on an
    ephemeral port, which closes with it. The client may be shared by
    concurrent callers. It opens every stream on its current connection and
    keeps physical connection state private.
    Entering it starts connecting in the background, so that `available`
    tells whether the server can be reached before the first call.

    Task cancellation propagates unchanged. It holds no task group or cancel
    scope of the tasks that use it, so that its owner may close it from any
    task.

    Attributes:
        url: The URL the instance was created with.
    """

    url: URL

    def __init__(self, url: URL) -> None:
        self.url = url

    @property
    def available(self) -> bool:
        """Whether the client is connected, or closed its connection on the idle timeout and connects again at once.

        It is false until an attempt to connect succeeds, and from a failed
        attempt, a lost connection, or one whose path stopped carrying data,
        until the next succeeds, while the
        client waits a backoff, longer after each failure in a row, and then
        tries again. A connection that closes on its idle timeout is not
        lost: the client stays available and connects again for the next
        stream. Opening a stream still tries to connect while it is false.
        A client that does not say otherwise is always available.
        """
        return True

    @property
    def connected(self) -> bool:
        """Whether a stream opened now goes on a connection already established, without waiting for a handshake.

        It is false where `available` is, and also once the connection
        closed on its idle timeout, until the client connects again. A
        client that does not say otherwise is connected while it is
        available.
        """
        return self.available

    async def connect(self) -> None:
        """Return once the client is connected, connecting at once unless it is, even during a backoff, without opening a stream.

        An attempt under way is joined rather than repeated, and one that
        the caller stops waiting for goes on.

        Raises:
            RuntimeError: The client is closed.
            H3ConnectionError: The attempt failed.
        """
        ...

    async def open_stream(self, headers: H3Headers, *, end_stream: bool = False) -> H3ClientStream:
        """Open a request stream, send ``headers``, the request's ordered header section, and return the stream.

        It waits for the handshake and for room for one more stream;
        ``end_stream`` also ends the local direction. An extended CONNECT,
        whose headers carry ``:protocol``, first waits for the server's
        SETTINGS, which must enable it. The stream returns
        once its headers have entered the connection's bounded write path.
        A cancellation after the stream is allocated aborts it before
        propagating.

        Raises:
            RuntimeError: The client is closed.
            H3StreamResetError: The peer aborted the newly allocated stream
                before it could be returned.
            H3StreamError: The header section is invalid for a new stream,
                or it carries ``:protocol`` and the server does not accept
                extended CONNECT.
            H3ConnectionError: A connection cannot be established, or fails
                before the headers enter it. One that fails after, when the
                server may have the request, fails the returned stream's
                operations instead.
        """
        ...

    async def drain(self) -> None:
        """Stop opening streams, wait as long as the open streams take to finish, and then close as `aclose` does.

        Closing the client meanwhile ends the wait as `aclose` does.
        """
        ...

    async def aclose(self) -> None:
        """Stop opening streams and close the connections and their sockets.

        Each connection sends a GOAWAY and closes with
        `H3ErrorCode.H3_NO_ERROR` once its open streams finish, or is aborted
        after a grace period. A cancelled close aborts the connections at
        once. Closing again waits for the close under way; it returns once
        the sockets are closed.
        """
        ...
