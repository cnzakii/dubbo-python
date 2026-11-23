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
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from types import TracebackType
from typing import TYPE_CHECKING, Self

from dubbo.transport.http.h3.base import (
    H3ClientStream,
    H3ErrorCode,
    H3Headers,
    H3Priority,
    H3ServerStream,
    H3Settings,
    H3Stream,
)
from dubbo.transport.http.h3.exceptions import H3Error, H3StreamError, H3UsageError
from dubbo.transport.http.streams import BufferedReceive, OperationGuard

if TYPE_CHECKING:
    from .connection import AioH3Connection

__all__ = ["AioH3ClientStream", "AioH3ServerStream", "AioH3Stream"]

# The largest QUIC variable-length integer, which bounds an error code.
_MAX_VARINT = (1 << 62) - 1
# HTTP datagrams a stream holds for a receiver; a new one beyond them drops the oldest.
_MAX_QUEUED_DATAGRAMS = 64
# Interim responses a client stream holds unread before it gives up on the server.
_MAX_QUEUED_INTERIM_RESPONSES = 5


class AioH3Stream(BufferedReceive, H3Stream):
    """What the asyncio-backed HTTP/3 request streams of both roles share.

    The stream keeps the state of each direction. It queues frames through
    its connection, which hands it what arrives through the ``_on_*``
    methods, synchronously on the connection's event loop. Sending and
    receiving may overlap, but two operations in the same direction are
    rejected by the guards. Every operation must stay on that event loop.
    """

    def __init__(
        self,
        connection: "AioH3Connection",
        stream_id: int,
        *,
        headers_sent: bool,
        send_ended: bool = False,
        receive_ended: bool = False,
    ) -> None:
        self._connection = connection
        self._stream_id = stream_id

        self._headers_sent = headers_sent
        self._send_ended = send_ended
        self._send_failure: H3Error | None = None
        # Set while the engine holds more of the body than the send buffer, until StreamWritable.
        self._send_blocked = False
        self._send_guard = OperationGuard(partial(_overlap, stream_id, "sending on"))

        self._inbound_trailers: H3Headers | None = None
        self._inbound_chunks: deque[bytes] = deque()
        self._chunk_offset = 0
        # Bytes at the front of the queue whose flow-control credit has already been returned.
        self._credited = 0
        self._receive_ended = receive_ended
        self._receive_failure: H3Error | None = None
        # Set once this side sent STOP_SENDING, which the peer answers with RESET_STREAM (RFC 9000 3.5).
        self._receive_stopped = False
        self._receive_guard = OperationGuard(partial(_overlap, stream_id, "receiving from"))

        self._datagrams: deque[bytes] = deque(maxlen=_MAX_QUEUED_DATAGRAMS)
        self._datagram_guard = OperationGuard(partial(_overlap, stream_id, "receiving datagrams from"))

        # Set once the engine holds no state of the stream, so cleanup has nothing to abort.
        self._closed = False
        # Set once the context closes; nothing reads the stream after that.
        self._released = False
        # Called once when the peer aborts the stream or the connection fails.
        self._on_abort: Callable[[], object] | None = None
        self._aborted = False

        # Set by the task waiting in that direction; whoever wakes it clears it.
        self._receive_waiter: asyncio.Future[None] | None = None
        self._send_waiter: asyncio.Future[None] | None = None
        self._datagram_waiter: asyncio.Future[None] | None = None

    @property
    def stream_id(self) -> int:
        return self._stream_id

    @property
    def peer(self) -> str | None:
        return self._connection.peer

    @property
    def remote_settings(self) -> H3Settings | None:
        return self._connection.remote_settings

    @property
    def max_datagram_size(self) -> int | None:
        if self._closed:
            return None
        return self._connection.max_datagram_size(self._stream_id)

    async def send_data(self, data: bytes, *, end_stream: bool = False) -> None:
        with self._send_guard:
            self._check_sendable()

            if not self._connection.send_data(self._stream_id, data, end_stream=end_stream):
                self._send_blocked = True
            self._send_ended = end_stream
            await self._wait_writable()

    async def send_trailers(self, trailers: H3Headers) -> None:
        with self._send_guard:
            self._check_sendable()

            self._connection.send_trailers(self._stream_id, trailers)
            self._send_ended = True
            await self._wait_writable()

    async def end(self) -> None:
        with self._send_guard:
            self._raise_if_send_failed()
            if self._send_ended:
                return

            self._connection.end_stream(self._stream_id)
            self._send_ended = True
            await self._wait_writable()

    async def receive_trailers(self) -> H3Headers | None:
        with self._receive_guard:
            self._check_receivable()
            while True:
                self._discard_unread()
                if self._receive_ended:
                    break
                self._raise_if_receive_failed()
                await self._wait_to_receive()

            return self._inbound_trailers

    def send_datagram(self, data: bytes) -> None:
        self._connection.send_datagram(self._stream_id, data)

    async def receive_datagram(self) -> bytes | None:
        with self._datagram_guard:
            while not self._datagrams:
                if self._closed or self._receive_ended or self._receive_failure is not None:
                    failure = self._connection.failure
                    if failure is not None:
                        raise failure
                    return None
                waiter = self._datagram_waiter = asyncio.get_running_loop().create_future()
                await waiter

            return self._datagrams.popleft()

    async def reset(self, error_code: int = H3ErrorCode.H3_REQUEST_CANCELLED) -> None:
        _check_error_code(error_code)
        if self._finished:
            return

        # Reset is cleanup and must remain available while another operation unwinds.
        self._reset(error_code)

    async def stop_sending(self, error_code: int = H3ErrorCode.H3_NO_ERROR) -> None:
        _check_error_code(error_code)
        self._stop_sending_locally(error_code)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._released = True
        try:
            # Unread data is credited back to flow control.
            self._connection.consume(self._stream_id, self._drop_unread())
            if self._finished:
                return
            # A request left unfinished is reset; otherwise the peer is told to stop sending.
            if not self._send_ended and self._send_failure is None:
                self._reset(H3ErrorCode.H3_REQUEST_CANCELLED)
            else:
                self._stop_sending_locally(self._release_code())
        except H3Error:
            if exc_value is None:
                raise

    def _release_code(self) -> int:
        """Return the code that stops reading the peer's direction once the local one has ended."""
        return H3ErrorCode.H3_REQUEST_CANCELLED

    def _check_receivable(self) -> None:
        """Raise when the role allows no body or trailers to be received yet."""

    @property
    def _finished(self) -> bool:
        """Return whether neither direction needs cleanup."""
        return self._closed or (
            (self._send_ended or self._send_failure is not None)
            and (self._receive_ended or self._receive_failure is not None)
        )

    def _raise_if_send_failed(self) -> None:
        if self._send_failure is not None:
            raise self._send_failure
        if not self._closed:
            self._connection.raise_if_failed()

    def _consume(self, amount: int) -> None:
        self._connection.consume(self._stream_id, amount)

    def _raise_if_receive_failed(self) -> None:
        if self._receive_failure is not None:
            raise self._receive_failure
        if not self._closed:
            self._connection.raise_if_failed()

    def _check_sendable(self) -> None:
        self._raise_if_send_failed()
        if not self._headers_sent:
            raise H3UsageError("the response header section was not sent", stream_id=self._stream_id)
        if self._send_ended:
            raise H3UsageError("local stream direction is closed", stream_id=self._stream_id)

    def _stop_sending_locally(self, error_code: int) -> None:
        """Stop reading the peer's direction with STOP_SENDING, unless it has ended."""
        if self._closed or self._receive_ended or self._receive_failure is not None:
            return

        self._connection.consume(self._stream_id, self._drop_unread())
        self._connection.stop_sending(self._stream_id, error_code)
        self._receive_stopped = True
        self._receive_failure = H3StreamError("receiving was stopped locally", stream_id=self._stream_id)
        self._wake_receiver()

    async def _wait_writable(self) -> None:
        """Wait for the connection to take more output, aborting the stream if cancelled."""
        try:
            await self._connection.wait_writable(self)
        except asyncio.CancelledError:
            self._reset_after_cancel()
            raise

    def _reset(self, error_code: int) -> None:
        """Abort both directions locally after returning the unread credit."""
        self._connection.consume(self._stream_id, self._drop_unread())
        self._connection.reset_stream(self._stream_id, error_code)
        self._fail(H3StreamError("the HTTP/3 stream was aborted locally", stream_id=self._stream_id))

    def _reset_after_cancel(self) -> None:
        """Abort after cancellation without replacing that exception."""
        try:
            self._reset(H3ErrorCode.H3_REQUEST_CANCELLED)
        except H3Error:
            self._fail(H3StreamError("the HTTP/3 stream operation was cancelled", stream_id=self._stream_id))

    # Waiting and waking. Checking state and starting to wait happen without
    # an await in between, so no wakeup is lost. One task at most waits in
    # each direction, so a plain future serves it.

    async def _wait_to_send(self) -> None:
        waiter = self._send_waiter = asyncio.get_running_loop().create_future()
        await waiter

    def _wake_receiver(self) -> None:
        """Wake the receivers of body and of datagrams, as both follow the peer's direction."""
        waiter = self._receive_waiter
        if waiter is not None:
            self._receive_waiter = None
            if not waiter.done():
                waiter.set_result(None)
        waiter = self._datagram_waiter
        if waiter is not None:
            self._datagram_waiter = None
            if not waiter.done():
                waiter.set_result(None)

    def _wake_sender(self) -> None:
        waiter = self._send_waiter
        if waiter is not None:
            self._send_waiter = None
            if not waiter.done():
                waiter.set_result(None)

    def _wake(self) -> None:
        self._wake_receiver()
        self._wake_sender()

    # Called by the connection as frames arrive or the stream ends.

    def _on_trailers(self, trailers: H3Headers) -> None:
        self._inbound_trailers = trailers
        self._receive_ended = True
        self._wake_receiver()

    def _on_datagram(self, data: bytes) -> None:
        self._datagrams.append(data)
        self._wake_receiver()

    def _on_writable(self) -> None:
        self._send_blocked = False
        self._wake_sender()

    def _on_closed(self) -> None:
        """Record that the engine released the stream; what the peer sent stays readable."""
        self._closed = True
        self._wake()

    def _stop_sending(self, error: H3Error) -> None:
        """Fail the local direction with ``error``, keeping what the peer sent readable."""
        if self._send_failure is None:
            self._send_failure = error
        self._send_blocked = False
        self._wake_sender()

    def _stop_receiving(self, error: H3Error) -> int:
        """Fail the peer's direction with ``error``, dropping its unread input; return the bytes dropped."""
        if self._receive_failure is None:
            self._receive_failure = error
        self._wake_receiver()
        return self._drop_unread()

    def _report_abort(self) -> None:
        """Report once that the peer or the connection aborted the stream, before the failure is seen."""
        self._aborted = True
        on_abort, self._on_abort = self._on_abort, None
        if on_abort is not None:
            on_abort()

    def _cancel_on_abort(self, cancel: Callable[[], object]) -> None:
        """Call ``cancel`` once the stream is aborted, at once if it already was."""
        if self._aborted:
            cancel()
        else:
            self._on_abort = cancel

    def _fail(self, error: H3Error) -> None:
        """Fail each direction that has not ended, and wake both."""
        # A send still waiting for room has not delivered what it ended with.
        if self._send_failure is None and (not self._send_ended or self._send_guard.busy):
            self._send_failure = error
        if self._receive_failure is None and not self._receive_ended:
            self._receive_failure = error
        self._send_blocked = False
        self._closed = True
        self._wake()


class AioH3ServerStream(AioH3Stream, H3ServerStream):
    """A request stream an asyncio-backed HTTP/3 server accepted, handed to its handler."""

    def __init__(
        self, connection: "AioH3Connection", stream_id: int, request_headers: H3Headers, *, receive_ended: bool
    ) -> None:
        super().__init__(connection, stream_id, headers_sent=False, receive_ended=receive_ended)
        self._request_headers = request_headers

    @property
    def request_headers(self) -> H3Headers:
        return self._request_headers

    @property
    def priority(self) -> H3Priority:
        return self._connection.priority(self._stream_id)

    def set_priority(self, priority: H3Priority) -> None:
        if not 0 <= priority.urgency <= 7:
            raise ValueError(f"an urgency is from 0 to 7, not {priority.urgency}")
        self._connection.set_priority(self._stream_id, priority)

    async def send_informational(self, headers: H3Headers) -> None:
        with self._send_guard:
            self._check_response_unsent()
            self._connection.send_informational(self._stream_id, headers)
            await self._wait_writable()

    async def send_response(self, headers: H3Headers, *, end_stream: bool = False) -> None:
        with self._send_guard:
            self._check_response_unsent()
            self._connection.send_response(self._stream_id, headers, end_stream=end_stream)
            # The engine has accepted the frame; it cannot be taken back.
            self._headers_sent = True
            self._send_ended = end_stream
            await self._wait_writable()

    def _check_response_unsent(self) -> None:
        self._raise_if_send_failed()
        if self._headers_sent:
            raise H3UsageError("the final response was already sent", stream_id=self._stream_id)

    def _release_code(self) -> int:
        # RFC 9114 section 4.1: a server that answered stops reading the rest of the request with H3_NO_ERROR.
        return H3ErrorCode.H3_NO_ERROR if self._send_failure is None else H3ErrorCode.H3_REQUEST_CANCELLED


class AioH3ClientStream(AioH3Stream, H3ClientStream):
    """A request stream an asyncio-backed HTTP/3 client opened, returned by `AioH3Client.open_stream`."""

    def __init__(self, connection: "AioH3Connection", stream_id: int, *, send_ended: bool) -> None:
        super().__init__(connection, stream_id, headers_sent=True, send_ended=send_ended)
        self._responses: deque[H3Headers] = deque()
        self._final_arrived = False
        self._final_taken = False

    async def receive_response(self) -> H3Headers:
        with self._receive_guard:
            if self._final_taken:
                raise H3UsageError("the final response was already received", stream_id=self._stream_id)
            while not self._responses:
                self._raise_if_receive_failed()
                if self._receive_ended or self._closed:
                    raise H3StreamError("peer ended the stream without a final response", stream_id=self._stream_id)
                await self._wait_to_receive()

            headers = self._responses.popleft()
            if self._final_arrived and not self._responses:
                self._final_taken = True
            return headers

    def _check_receivable(self) -> None:
        if not self._final_taken:
            self._raise_if_receive_failed()
            raise H3UsageError("the final response was not received", stream_id=self._stream_id)

    def _on_informational(self, headers: H3Headers) -> None:
        if len(self._responses) < _MAX_QUEUED_INTERIM_RESPONSES:
            self._responses.append(headers)
            self._wake_receiver()
            return

        self._fail(H3StreamError("the server sent too many interim responses", stream_id=self._stream_id))
        with suppress(H3Error):
            self._reset(H3ErrorCode.H3_EXCESSIVE_LOAD)

    def _on_response(self, headers: H3Headers, *, end_stream: bool) -> None:
        self._responses.append(headers)
        self._final_arrived = True
        self._receive_ended = self._receive_ended or end_stream
        self._wake_receiver()


def _check_error_code(error_code: int) -> None:
    if not 0 <= error_code <= _MAX_VARINT:
        raise ValueError("error_code must fit a QUIC variable-length integer")


def _overlap(stream_id: int, action: str) -> H3UsageError:
    return H3UsageError(f"another task is already {action} this stream", stream_id=stream_id)
