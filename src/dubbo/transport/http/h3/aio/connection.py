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
from collections.abc import Callable
from contextlib import suppress
from typing import Any, NoReturn

import anyio
import ngquic
from ngquic import h3

from dubbo.transport.http.h3.base import H3ErrorCode, H3Headers, H3Priority, H3SensitiveHeader, H3Settings
from dubbo.transport.http.h3.exceptions import H3ConnectionError, H3StreamError, H3StreamResetError
from dubbo.transport.http.streams import StreamAdmission
from dubbo.utils import close_then_abort
from dubbo.utils.net import format_address

from .endpoint import AioQuicEndpoint
from .stream import AioH3ClientStream, AioH3ServerStream, AioH3Stream

__all__ = ["AioH3Connection"]

# Datagrams taken from the engine in one call.
_BATCH = 64
_KNOWN_ERROR_CODES = frozenset(H3ErrorCode)


class AioH3Connection:
    """Runs one ngquic HTTP/3 connection on an `AioQuicEndpoint`.

    Streams queue frames through the stream operations below and keep their
    own state; the connection hands them what arrives through their ``_on_*``
    methods. The endpoint calls `process` once per loop iteration in which
    datagrams arrived or output was queued, and the connection's timer calls
    it when the engine's deadline passes. The client or server that owns the
    connection uses the lifecycle methods.
    """

    def __init__(
        self,
        endpoint: AioQuicEndpoint,
        engine: h3.H3Connection,
        *,
        on_stream: Callable[[AioH3ServerStream], None] | None = None,
        on_closed: Callable[["AioH3Connection"], None] | None = None,
        on_degrading: Callable[["AioH3Connection"], None] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.engine = engine
        self.is_server = not engine.is_client
        remote = engine.remote_addr
        self.peer: str | None = format_address(remote[0], remote[1])
        # Whether the connection ended by a failure rather than a close or its idle timeout.
        self.lost = False
        # Whether the peer's address answered the handshake with an ICMP error, such as port unreachable.
        self.unreachable = False
        # The peer's SETTINGS, once they arrive.
        self.remote_settings: H3Settings | None = None
        self._on_stream = on_stream
        self._on_closed = on_closed
        self._on_degrading = on_degrading

        self._streams: dict[int, AioH3Stream] = {}
        # Streams waiting for the engine or the socket to take more output.
        self._blocked_senders: set[AioH3Stream] = set()
        # The tasks waiting to open a stream.
        self._admission = StreamAdmission(lambda: self.engine.bidi_streams_available, lambda: self.accepting_streams)

        self._established = anyio.Event()
        # Set by the peer's SETTINGS, or by the end of the connection.
        self._settings_received = anyio.Event()
        self._terminated = anyio.Event()
        # A client stops opening streams once the server sends GOAWAY, or once its path stops carrying data.
        self._draining = False
        self._closing = False
        self._close_sent = False
        self._finished = False
        self._failure: H3ConnectionError | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._timer_at: float | None = None

        self._event_handlers: dict[type, Callable[[Any], None]] = {
            ngquic.HandshakeCompleted: self._handle_handshake_completed,
            ngquic.StreamsAvailable: self._handle_streams_available,
            h3.SettingsReceived: self._handle_settings_received,
            h3.RequestReceived: self._handle_request_received,
            h3.InformationalResponseReceived: self._handle_informational_received,
            h3.ResponseReceived: self._handle_response_received,
            h3.DataReceived: self._handle_data_received,
            h3.TrailersReceived: self._handle_trailers_received,
            h3.DatagramReceived: self._handle_datagram_received,
            ngquic.StreamReset: self._handle_stream_reset,
            ngquic.StopSendingReceived: self._handle_stop_sending,
            ngquic.StreamClosed: self._handle_stream_closed,
            ngquic.StreamWritable: self._handle_stream_writable,
            h3.GoAwayReceived: self._handle_goaway_received,
            ngquic.PathDegrading: self._handle_path_degrading,
            ngquic.ConnectionTerminated: self._handle_terminated,
        }

    @property
    def closed(self) -> bool:
        """Return whether the connection carries nothing more."""
        return self._terminated.is_set()

    @property
    def failure(self) -> H3ConnectionError | None:
        """Return why the connection failed, or ``None``."""
        return self._failure

    @property
    def accepting_streams(self) -> bool:
        """Return whether this client connection can still open a stream."""
        return (
            self._established.is_set()
            and not self._terminated.is_set()
            and not self._closing
            and not self._draining
            and self._failure is None
        )

    async def open_stream(self, headers: H3Headers, *, end_stream: bool) -> AioH3ClientStream | None:
        """Open a request stream, or return ``None`` when the connection can no longer open one.

        A connection that fails once the headers entered it fails the
        returned stream's operations with its `H3ConnectionError`.

        Raises:
            H3StreamError: The headers are invalid, or carry ``:protocol``
                and the server does not accept extended CONNECT.
        """
        # Only a CONNECT request carries :protocol, RFC 8441 and RFC 9220.
        if (b":method", b"CONNECT") in headers and any(field[0] == b":protocol" for field in headers):
            await self._settings_received.wait()
            settings = self.remote_settings
            if settings is None:
                return None
            if not settings.enable_connect_protocol:
                raise H3StreamError("the server does not accept extended CONNECT", stream_id=None)

        fields = _fields(headers)
        first = False
        while self.accepting_streams:
            if first or self._admission.ready(first=False):
                try:
                    stream_id = self.engine.send_request(fields, end_stream=end_stream)
                except ngquic.StreamLimitError:
                    pass
                except ngquic.ConnectionStateError:
                    # The server is shutting down, or the connection is closing.
                    self._draining = True
                    self._notify_capacity()
                    return None
                except ValueError as error:
                    raise H3StreamError(str(error), stream_id=None) from error
                except ngquic.QUICError as error:
                    self._raise_engine_failure(error)
                else:
                    stream = AioH3ClientStream(self, stream_id, send_ended=end_stream)
                    self._streams[stream_id] = stream
                    self.endpoint.schedule(self)
                    try:
                        await self.wait_writable(stream)
                    except H3ConnectionError:
                        # The server may have the request, so the stream is returned, failing as its connection did.
                        pass
                    except BaseException:
                        stream._reset_after_cancel()
                        raise
                    return stream

            if not await self._admission.wait(first=first):
                return None
            first = True

        return None

    def raise_if_failed(self) -> None:
        """Raise the connection failure, or an error once the connection is closed."""
        if self._failure is not None:
            raise self._failure
        if self._terminated.is_set():
            raise H3ConnectionError("the HTTP/3 connection is closed")

    # Stream operations. Each queues frames and leaves them to the next pass.

    def send_informational(self, stream_id: int, headers: H3Headers) -> None:
        self._call(stream_id, self.engine.send_informational_response, stream_id, _fields(headers))

    def priority(self, stream_id: int) -> H3Priority:
        """Return the priority the engine schedules the response of ``stream_id`` with, the default once it is gone."""
        try:
            current = self.engine.get_stream_priority(stream_id)
        except ngquic.ConnectionStateError:
            # The connection closed, and its streams with it.
            return H3Priority()
        return H3Priority() if current is None else H3Priority(current.urgency, current.incremental)

    def set_priority(self, stream_id: int, priority: H3Priority) -> None:
        self._call(stream_id, self.engine.set_stream_priority, stream_id, h3.Priority(*priority))

    def send_response(self, stream_id: int, headers: H3Headers, *, end_stream: bool) -> None:
        self._call(stream_id, self.engine.send_response, stream_id, _fields(headers), end_stream=end_stream)

    def send_data(self, stream_id: int, data: bytes, *, end_stream: bool) -> bool:
        """Queue body data; return ``False`` once the stream holds more than its send buffer."""
        return self._call(stream_id, self.engine.send_data, stream_id, data, end_stream=end_stream)

    def send_trailers(self, stream_id: int, trailers: H3Headers) -> None:
        self._call(stream_id, self.engine.send_trailers, stream_id, _fields(trailers))

    def end_stream(self, stream_id: int) -> None:
        self._call(stream_id, self.engine.end_stream, stream_id)

    def reset_stream(self, stream_id: int, error_code: int) -> None:
        if self._terminated.is_set():
            return
        self._call(stream_id, self.engine.reset_stream, stream_id, error_code)

    def stop_sending(self, stream_id: int, error_code: int) -> None:
        if self._terminated.is_set():
            return
        self._call(stream_id, self.engine.stop_sending, stream_id, error_code)

    def send_datagram(self, stream_id: int, data: bytes) -> None:
        self.raise_if_failed()
        self._call(stream_id, self.engine.send_datagram, stream_id, data)

    def max_datagram_size(self, stream_id: int) -> int | None:
        if self._terminated.is_set():
            return None
        return self.engine.max_datagram_size(stream_id)

    def consume(self, stream_id: int, amount: int) -> None:
        """Return flow-control credit for bytes the application read or dropped."""
        if not amount or self._terminated.is_set():
            return
        self._call(stream_id, self.engine.consume_received_data, stream_id, amount)

    async def wait_writable(self, stream: AioH3Stream) -> None:
        """Wait until the socket takes writes and the engine holds at most a send buffer of the stream's body."""
        while True:
            stream._raise_if_send_failed()
            # A stream the connection closed has nothing left to write, its send direction having ended.
            if stream._closed or (not self.endpoint.write_paused and not stream._send_blocked):
                return

            self._blocked_senders.add(stream)
            try:
                await stream._wait_to_send()
            finally:
                self._blocked_senders.discard(stream)

    # Lifecycle.

    def announce_shutdown(self) -> None:
        """Tell the client that a GOAWAY will follow, the first step of a graceful close."""
        if self._terminated.is_set() or self._closing:
            return

        try:
            self.engine.send_shutdown_notice()
        except ngquic.QUICError:
            return
        self.endpoint.schedule(self)

    def start_close(self) -> None:
        """Send GOAWAY and close once the streams finish, without waiting."""
        if self._terminated.is_set() or self._closing:
            return

        self._closing = True
        self._notify_capacity()
        # A connection still in its handshake has no HTTP/3 to shut down; it is closed below.
        with suppress(ngquic.QUICError):
            self.engine.send_goaway()
        self.endpoint.schedule(self)

    async def aclose(self) -> None:
        """Close once the streams finish, waiting a moment; abort after it or when cancelled."""
        self.start_close()
        await close_then_abort(self.wait_closed, lambda: self._terminated.is_set(), self.abort)

    def abort(self, error_code: int = H3ErrorCode.H3_NO_ERROR) -> None:
        """Close at once with ``error_code``, sending CONNECTION_CLOSE without waiting for the closing period."""
        if self._finished:
            return

        self._closing = True
        if not self._close_sent and not self._terminated.is_set():
            self._close_sent = True
            with suppress(ngquic.QUICError):
                self.engine.close(error_code)
            self.process()
        self._terminate(H3ConnectionError("the HTTP/3 connection was aborted"), lost=self._failure is not None)
        self._finish()

    async def wait_established(self) -> None:
        """Wait for the handshake.

        Raises:
            H3ConnectionError: The connection failed or closed first.
        """
        await self._established.wait()
        self.raise_if_failed()

    async def wait_closed(self) -> None:
        await self._terminated.wait()

    # Driven by the endpoint.

    def process(self) -> None:
        """Apply what the engine reports, send what it has ready, and set the timer for its next deadline."""
        if self._finished:
            return

        endpoint = self.endpoint
        engine = self.engine
        try:
            self._dispatch(engine.events())
            self._close_when_done()
            # Datagrams go out in batches until the engine has fewer left or the socket refuses more.
            gso = endpoint.gso
            while not endpoint.write_paused:
                count = 0
                for transmit in engine.datagrams_to_send(endpoint.now(), max_datagrams=_BATCH, gso=gso):
                    endpoint.send(transmit)
                    # A transmit for GSO holds several datagrams of segment_size bytes, the last possibly shorter.
                    count += -(-len(transmit.data) // transmit.segment_size) if transmit.segment_size else 1
                if count < _BATCH:
                    break
            # Writing packets may report further events, which are applied too.
            self._dispatch(engine.events())
        except ngquic.QUICError as error:
            self._fail(H3ConnectionError(str(error) or type(error).__name__))
            self._finish()
            return

        if engine.state is ngquic.ConnectionState.CLOSED:
            self._finish()
        else:
            self._set_timer()

    def writable(self) -> None:
        """Wake the senders blocked while the socket refused writes."""
        self._wake_senders()

    def network_error(self, error: Exception) -> None:
        """Fail a connection still in its handshake on an ICMP error from its peer, such as port unreachable."""
        if not self._established.is_set():
            self.unreachable = True
            self._fail(H3ConnectionError(f"the HTTP/3 connection failed: {error}"))
            self.abort(H3ErrorCode.H3_INTERNAL_ERROR)

    def socket_lost(self, error: Exception | None) -> None:
        """Finish after the endpoint's socket closed."""
        detail = "" if error is None else f": {error}"
        self._terminate(H3ConnectionError(f"the HTTP/3 socket closed{detail}"), lost=error is not None)
        self._finish()

    # Internals.

    def _call(self, stream_id: int, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run an engine operation for a stream, translating its failures, and send its output soon."""
        try:
            result = operation(*args, **kwargs)
        except (ngquic.StreamStateError, ngquic.DatagramError, ValueError) as error:
            raise H3StreamError(str(error) or type(error).__name__, stream_id=stream_id) from error
        except ngquic.ConnectionStateError as error:
            raise self._failure or H3ConnectionError("the HTTP/3 connection is closed") from error
        except ngquic.QUICError as error:
            self._raise_engine_failure(error)
        self.endpoint.schedule(self)
        return result

    def _raise_engine_failure(self, error: Exception) -> NoReturn:
        failure = H3ConnectionError(str(error) or type(error).__name__)
        self._fail(failure)
        self.abort(H3ErrorCode.H3_INTERNAL_ERROR)
        raise failure from error

    def _close_when_done(self) -> None:
        """Send CONNECTION_CLOSE once a closing or draining connection has no request left."""
        if self._close_sent or self._terminated.is_set() or not (self._closing or self._draining):
            return

        # A server waits for the requests the engine still tracks, a client for its own open streams.
        if not self._established.is_set():
            done = True
        elif self.is_server:
            done = self.engine.is_drained
        else:
            done = not self._streams
        if done:
            self._close_sent = True
            self.engine.close(H3ErrorCode.H3_NO_ERROR)

    def _set_timer(self) -> None:
        due = self.engine.get_timer()
        if due == self._timer_at:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer_at = due
        self._timer = None if due is None else self.endpoint.call_at(due, self._on_timer)

    def _on_timer(self) -> None:
        armed_at = self._timer_at
        self._timer = self._timer_at = None
        if armed_at is not None:
            try:
                # Loop timers may fire early, and the engine ignores a call before its deadline, which a datagram
                # received since the timer was armed may have moved later.
                self.engine.handle_timer(max(armed_at, self.endpoint.now()))
            except ngquic.QUICError as error:
                self._fail(H3ConnectionError(str(error) or type(error).__name__))
                self._finish()
                return
        self.process()

    def _finish(self) -> None:
        """Stop routing datagrams and timers to the connection, whose engine holds nothing more."""
        if self._finished:
            return

        self._finished = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = self._timer_at = None
        self.endpoint.remove(self)
        self._terminate(None, lost=False)

    def _fail(self, failure: H3ConnectionError) -> None:
        if self._failure is None:
            self._failure = failure
        self._terminate(self._failure, lost=True)

    def _terminate(self, failure: H3ConnectionError | None, *, lost: bool) -> None:
        """End the connection's streams, once: those unfinished fail with ``failure`` or a closing error."""
        if self._terminated.is_set():
            return

        self.lost = lost
        unfinished = [stream for stream in self._streams.values() if not stream._finished]
        if failure is None and unfinished:
            failure = H3ConnectionError("the HTTP/3 connection closed before its streams completed")
        if failure is not None and self._failure is None:
            self._failure = failure

        for stream in unfinished:
            stream._report_abort()
            stream._fail(self._failure or failure)  # ty: ignore[invalid-argument-type]
        for stream in self._streams.values():
            stream._closed = True
            stream._wake()
        self._streams.clear()

        # Whoever waits is told: the handshake, the settings, the senders and the stream openers.
        self._established.set()
        self._settings_received.set()
        self._terminated.set()
        self._wake_senders()
        self._notify_capacity()
        if self._on_closed is not None:
            self._on_closed(self)

    def _dispatch(self, events: list[Any]) -> None:
        for event in events:
            handler = self._event_handlers.get(type(event))
            if handler is not None:
                handler(event)

    def _handle_handshake_completed(self, event: ngquic.HandshakeCompleted) -> None:
        self._established.set()
        self._notify_capacity()

    def _handle_streams_available(self, event: ngquic.StreamsAvailable) -> None:
        self._notify_capacity()

    def _handle_settings_received(self, event: h3.SettingsReceived) -> None:
        settings = event.settings
        self.remote_settings = H3Settings(
            max_field_section_size=settings.max_field_section_size,
            qpack_max_table_capacity=settings.qpack_max_dtable_capacity,
            qpack_blocked_streams=settings.qpack_blocked_streams,
            enable_connect_protocol=settings.enable_connect_protocol,
            h3_datagram=settings.h3_datagram,
        )
        self._settings_received.set()

    def _handle_request_received(self, event: h3.RequestReceived) -> None:
        stream = AioH3ServerStream(self, event.stream_id, event.headers, receive_ended=event.end_stream)
        self._streams[event.stream_id] = stream
        if self._on_stream is not None:
            self._on_stream(stream)

    def _handle_informational_received(self, event: h3.InformationalResponseReceived) -> None:
        stream = self._streams.get(event.stream_id)
        if isinstance(stream, AioH3ClientStream):
            stream._on_informational(event.headers)

    def _handle_response_received(self, event: h3.ResponseReceived) -> None:
        stream = self._streams.get(event.stream_id)
        if isinstance(stream, AioH3ClientStream):
            stream._on_response(event.headers, end_stream=event.end_stream)

    def _handle_data_received(self, event: h3.DataReceived) -> None:
        stream = self._streams.get(event.stream_id)
        if stream is not None and not stream._released and stream._receive_failure is None:
            stream._on_data(event.data, end_stream=event.end_stream)
            return
        # Data that nothing will read still occupies flow-control credit.
        if event.data:
            self.engine.consume_received_data(event.stream_id, len(event.data))

    def _handle_trailers_received(self, event: h3.TrailersReceived) -> None:
        stream = self._streams.get(event.stream_id)
        if stream is not None:
            stream._on_trailers(event.headers)

    def _handle_datagram_received(self, event: h3.DatagramReceived) -> None:
        stream = self._streams.get(event.stream_id)
        if stream is not None and not stream._released:
            stream._on_datagram(event.data)

    def _handle_stream_reset(self, event: ngquic.StreamReset) -> None:
        stream = self._streams.get(event.stream_id)
        if stream is None:
            return
        # The reset a STOP_SENDING of this side asked for aborts nothing the handler still does.
        if self.is_server and not stream._receive_stopped:
            stream._report_abort()
        dropped = stream._stop_receiving(H3StreamResetError(event.stream_id, event.error_code))
        if dropped:
            self.engine.consume_received_data(event.stream_id, dropped)

    def _handle_stop_sending(self, event: ngquic.StopSendingReceived) -> None:
        stream = self._streams.get(event.stream_id)
        if stream is None:
            return
        # A client that asks the server to stop sending gives up the call; a server asks so after its whole response.
        if self.is_server:
            stream._report_abort()
        stream._stop_sending(H3StreamResetError(event.stream_id, event.error_code))

    def _handle_stream_closed(self, event: ngquic.StreamClosed) -> None:
        stream = self._streams.pop(event.stream_id, None)
        if stream is None:
            return

        self._blocked_senders.discard(stream)
        receive_failed = event.receive_error_code is not None and not stream._receive_ended
        send_failed = event.send_error_code is not None and not stream._send_ended
        # Not aborted locally, a stream closed unfinished was broken by the peer.
        if (receive_failed or send_failed) and self.is_server and not stream._closed:
            stream._report_abort()
        if receive_failed:
            dropped = stream._stop_receiving(H3StreamResetError(event.stream_id, event.receive_error_code))
            if dropped:
                self.consume(event.stream_id, dropped)
        if send_failed:
            stream._stop_sending(H3StreamResetError(event.stream_id, event.send_error_code))
        stream._on_closed()

    def _handle_stream_writable(self, event: ngquic.StreamWritable) -> None:
        stream = self._streams.get(event.stream_id)
        if stream is not None:
            stream._on_writable()

    def _handle_goaway_received(self, event: h3.GoAwayReceived) -> None:
        if not self.is_server:
            self._draining = True
            self._notify_capacity()

    def _handle_path_degrading(self, event: ngquic.PathDegrading) -> None:
        # The streams under way go on, should the path recover.
        self._draining = True
        self._notify_capacity()
        if self._on_degrading is not None:
            self._on_degrading(self)

    def _handle_terminated(self, event: ngquic.ConnectionTerminated) -> None:
        reason = event.reason
        application = reason is ngquic.CloseReason.APPLICATION_ERROR
        # An unknown code means H3_NO_ERROR, RFC 9114 section 9.
        graceful = reason is ngquic.CloseReason.IDLE_TIMEOUT or (
            application and (event.error_code == H3ErrorCode.H3_NO_ERROR or event.error_code not in _KNOWN_ERROR_CODES)
        )
        failure = None
        if not graceful:
            who = "the peer" if event.by_peer else "this side"
            failure = H3ConnectionError(
                f"{who} closed the HTTP/3 connection: {reason.name}, error code {event.error_code}",
                error_code=event.error_code,
                application=application,
            )
        self._terminate(failure, lost=not graceful)

    def _notify_capacity(self) -> None:
        self._admission.notify()

    def _wake_senders(self) -> None:
        for stream in tuple(self._blocked_senders):
            stream._wake_sender()


def _fields(headers: H3Headers) -> H3Headers | list[Any]:
    """Return ``headers`` with each sensitive field marked for the engine, which QPACK never indexes."""
    if H3SensitiveHeader in map(type, headers):
        return [h3.NeverIndexedHeader(*field) if type(field) is H3SensitiveHeader else field for field in headers]
    return headers
