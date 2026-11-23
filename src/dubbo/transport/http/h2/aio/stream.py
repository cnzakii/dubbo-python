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

from dubbo.transport.http.h2.base import H2ClientStream, H2ErrorCode, H2Headers, H2ServerStream, H2Settings, H2Stream
from dubbo.transport.http.h2.exceptions import H2Error, H2StreamError, H2UsageError
from dubbo.transport.http.streams import BufferedReceive, OperationGuard

if TYPE_CHECKING:
    from .connection import AioH2Connection

__all__ = ["AioH2ClientStream", "AioH2ServerStream", "AioH2Stream"]

# Interim responses a client stream holds unread before it gives up on the server.
_MAX_QUEUED_INTERIM_RESPONSES = 5


class AioH2Stream(BufferedReceive, H2Stream):
    """What the asyncio-backed HTTP/2 streams of both roles share.

    The stream keeps its own state. It queues frames through its connection,
    which hands it incoming headers, data and trailers through the ``_on_*``
    methods, synchronously on the connection's event loop. Sending and
    receiving may overlap, but two operations in the same direction are
    rejected by the guards. Every operation must stay on that event loop.

    Cancelling an outbound operation after ngh2 accepts it resets the stream
    with `H2ErrorCode.CANCEL` before propagating `asyncio.CancelledError`.
    Cancelling a receive leaves the stream usable; an exact receive puts the
    bytes it collected back at the front, without returning their window
    credit again. Backend and engine failures are exposed through Dubbo's H2
    exception hierarchy.
    """

    def __init__(
        self,
        connection: "AioH2Connection",
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
        self._send_guard = OperationGuard(partial(_overlap, stream_id, "sending on"))

        self._inbound_trailers: H2Headers | None = None
        self._inbound_chunks: deque[bytes] = deque()
        self._chunk_offset = 0
        # Bytes at the front of the queue whose window credit has already been returned.
        self._credited = 0
        self._receive_ended = receive_ended
        self._receive_guard = OperationGuard(partial(_overlap, stream_id, "receiving from"))

        self._closed = False
        self._failure: H2Error | None = None
        # Set when the peer stops the local direction but leaves its own readable.
        self._send_failure: H2Error | None = None
        # Set once the context closes; nothing reads the stream after that.
        self._released = False
        # Called once when the peer resets the stream or the connection fails.
        self._on_abort: Callable[[], object] | None = None
        self._aborted = False

        # Set by the task waiting in that direction; whoever wakes it clears it.
        self._receive_waiter: asyncio.Future[None] | None = None
        self._send_waiter: asyncio.Future[None] | None = None

    @property
    def stream_id(self) -> int:
        return self._stream_id

    @property
    def peer(self) -> str | None:
        return self._connection.peer

    @property
    def remote_settings(self) -> H2Settings:
        return self._connection.remote_settings

    async def send_data(self, data: bytes, *, end_stream: bool = False) -> None:
        with self._send_guard:
            self._check_sendable()

            self._connection.send_data(self._stream_id, data, end_stream=end_stream)
            self._send_ended = end_stream
            await self._wait_writable()

    async def send_trailers(self, trailers: H2Headers) -> None:
        with self._send_guard:
            self._check_sendable()

            self._connection.send_trailers(self._stream_id, trailers)
            self._send_ended = True
            await self._wait_writable()

    async def end(self) -> None:
        with self._send_guard:
            if self._send_failure is not None:
                raise self._send_failure
            if self._send_ended:
                return

            self._raise_if_failed()
            self._connection.end_stream(self._stream_id)
            self._send_ended = True
            await self._wait_writable()

    async def receive_trailers(self) -> H2Headers | None:
        with self._receive_guard:
            self._check_receivable()
            while True:
                self._discard_unread()
                if self._receive_ended:
                    break
                self._raise_if_failed()
                await self._wait_to_receive()

            self._raise_if_failed()
            return self._inbound_trailers

    async def reset(self, error_code: int = H2ErrorCode.CANCEL) -> None:
        if not 0 <= error_code <= 0xFFFFFFFF:
            raise ValueError("error_code must fit an unsigned 32-bit integer")

        if self._finished:
            return

        # Reset is cleanup and must remain available while another operation unwinds.
        self._reset(error_code)

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
            # Return connection-level credit before reset drops the stream state.
            self._connection.consume(self._stream_id, self._drop_unread())
            if self._finished:
                return
            if self._send_ended and self._connection.is_server:
                # RFC 9113 section 8.1: a server stops a request it no longer reads with NO_ERROR.
                self._connection.reset_stream(self._stream_id, H2ErrorCode.NO_ERROR, after_end_stream=True)
            else:
                self._reset(H2ErrorCode.CANCEL)
        except H2Error:
            if exc_value is None:
                raise

    @property
    def _finished(self) -> bool:
        """Return whether both directions no longer require cleanup."""
        return self._closed or (self._send_ended and self._receive_ended)

    def _consume(self, amount: int) -> None:
        self._connection.consume(self._stream_id, amount)

    def _raise_if_receive_failed(self) -> None:
        self._raise_if_failed()

    def _raise_if_failed(self) -> None:
        """Raise the stream failure, or an active connection failure."""
        if self._failure is not None:
            raise self._failure
        if not self._closed:
            # A normally closed stream may still expose data buffered before GOAWAY.
            self._connection.raise_if_failed()

    def _raise_if_send_failed(self) -> None:
        """Raise a failure of the stream or of its local direction alone."""
        self._raise_if_failed()
        if self._send_failure is not None:
            raise self._send_failure

    def _check_receivable(self) -> None:
        """Raise when the role allows no body or trailers to be received yet."""

    def _check_sendable(self) -> None:
        self._raise_if_send_failed()
        if not self._headers_sent:
            raise H2UsageError("the response header section was not sent", stream_id=self._stream_id)
        if self._send_ended:
            raise H2UsageError("local stream direction is closed", stream_id=self._stream_id)

    async def _wait_writable(self) -> None:
        """Wait for the connection to take more output, resetting the stream if cancelled."""
        try:
            await self._connection.wait_writable(self)
        except asyncio.CancelledError:
            self._reset_after_cancel()
            raise

    def _reset(self, error_code: int) -> None:
        """Reset the stream locally after returning its unread credit."""
        self._connection.consume(self._stream_id, self._drop_unread())
        self._connection.reset_stream(self._stream_id, error_code)
        self._fail(H2StreamError("the HTTP/2 stream was reset locally", stream_id=self._stream_id))

    def _reset_after_cancel(self) -> None:
        """Reset after cancellation without replacing that exception."""
        try:
            self._reset(H2ErrorCode.CANCEL)
        except H2Error:
            self._fail(H2StreamError("the HTTP/2 stream operation was cancelled", stream_id=self._stream_id))

    # Waiting and waking. Checking state and starting to wait happen without
    # an await in between, so no wakeup is lost.

    # One task at most waits in each direction, so a plain future serves it;
    # cancelling that task cancels only its own future.

    async def _wait_to_send(self) -> None:
        waiter = self._send_waiter = asyncio.get_running_loop().create_future()
        await waiter

    def _wake_receiver(self) -> None:
        waiter = self._receive_waiter
        if waiter is not None:
            self._receive_waiter = None
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

    def _on_trailers(self, trailers: H2Headers) -> None:
        self._inbound_trailers = trailers
        self._receive_ended = True
        self._wake_receiver()

    def _on_closed(self) -> None:
        """Record that both directions ended normally."""
        self._closed = True
        self._send_ended = True
        self._receive_ended = True
        self._wake()

    def _stop_sending(self, error: H2Error) -> None:
        """Fail the local direction with ``error``, keeping what the peer sent readable."""
        self._send_failure = error
        self._send_ended = True
        self._wake()

    def _abort(self, error: H2Error) -> None:
        """Fail the stream for a cause on the peer's side or the connection's, and report it once.

        The report comes first, so that what it cancels sees the cancellation before the failure.
        """
        self._aborted = True
        on_abort, self._on_abort = self._on_abort, None
        if on_abort is not None:
            on_abort()
        self._fail(error)

    def _cancel_on_abort(self, cancel: Callable[[], object]) -> None:
        """Call ``cancel`` once the stream is aborted, at once if it already was."""
        if self._aborted:
            cancel()
        else:
            self._on_abort = cancel

    def _fail(self, error: H2Error) -> None:
        """Keep the first failure, stop further operations and wake both directions."""
        if self._failure is None:
            self._failure = error
        self._closed = True
        self._wake()


class AioH2ServerStream(AioH2Stream, H2ServerStream):
    """A stream an asyncio-backed HTTP/2 server accepted, handed to its handler."""

    def __init__(
        self, connection: "AioH2Connection", stream_id: int, request_headers: H2Headers, *, receive_ended: bool
    ) -> None:
        super().__init__(connection, stream_id, headers_sent=False, receive_ended=receive_ended)
        self._request_headers = request_headers

    @property
    def request_headers(self) -> H2Headers:
        return self._request_headers

    async def send_informational(self, headers: H2Headers) -> None:
        with self._send_guard:
            self._check_response_unsent()
            self._connection.send_informational(self._stream_id, headers)
            await self._wait_writable()

    async def send_response(self, headers: H2Headers, *, end_stream: bool = False) -> None:
        with self._send_guard:
            self._check_response_unsent()
            self._connection.send_response(self._stream_id, headers, end_stream=end_stream)
            # The engine has accepted the frame; it cannot be taken back.
            self._headers_sent = True
            self._send_ended = end_stream
            await self._wait_writable()

    def _check_response_unsent(self) -> None:
        self._raise_if_failed()
        if self._headers_sent:
            raise H2UsageError("the final response was already sent", stream_id=self._stream_id)


class AioH2ClientStream(AioH2Stream, H2ClientStream):
    """A stream an asyncio-backed HTTP/2 client opened, returned by `AioH2Client.open_stream`."""

    def __init__(self, connection: "AioH2Connection", stream_id: int, *, send_ended: bool) -> None:
        super().__init__(connection, stream_id, headers_sent=True, send_ended=send_ended)
        self._responses: deque[H2Headers] = deque()
        self._final_arrived = False
        self._final_taken = False

    async def receive_response(self) -> H2Headers:
        with self._receive_guard:
            if self._final_taken:
                raise H2UsageError("the final response was already received", stream_id=self._stream_id)
            while not self._responses:
                self._raise_if_failed()
                if self._receive_ended:
                    raise H2StreamError("peer ended the stream without a final response", stream_id=self._stream_id)
                await self._wait_to_receive()

            # Interim 1xx responses come first; the last queued after the final one arrived is the final.
            headers = self._responses.popleft()
            if self._final_arrived and not self._responses:
                self._final_taken = True
            return headers

    def _check_receivable(self) -> None:
        if not self._final_taken:
            self._raise_if_failed()
            raise H2UsageError("the final response was not received", stream_id=self._stream_id)

    def _on_informational(self, headers: H2Headers) -> None:
        if len(self._responses) < _MAX_QUEUED_INTERIM_RESPONSES:
            self._responses.append(headers)
            self._wake_receiver()
            return

        self._fail(H2StreamError("the server sent too many interim responses", stream_id=self._stream_id))
        with suppress(H2Error):
            self._reset(H2ErrorCode.ENHANCE_YOUR_CALM)

    def _on_response(self, headers: H2Headers, *, end_stream: bool) -> None:
        self._responses.append(headers)
        self._final_arrived = True
        self._receive_ended = self._receive_ended or end_stream
        self._wake_receiver()


def _overlap(stream_id: int, action: str) -> H2UsageError:
    return H2UsageError(f"another task is already {action} this stream", stream_id=stream_id)
