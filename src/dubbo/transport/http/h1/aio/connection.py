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
from typing import Any, cast

import h11r

from dubbo.transport.http.h1.exceptions import H1ConnectionError, H1ExchangeError
from dubbo.utils.net import format_address, read_buffer

__all__ = ["AioH1Connection"]

# Bytes h11r may hold unparsed, such as pipelined requests or body nobody reads yet, before reading pauses.
_READ_LIMIT = 256 * 1024
# Bytes queued for one write; more are written at once rather than at the end of the loop iteration.
_WRITE_LIMIT = 64 * 1024


class AioH1Connection(asyncio.BufferedProtocol):
    """Runs one h11r connection over an asyncio socket, for a client or a server.

    Received bytes go to the engine, and exchanges pull its events, so
    reading pauses while the engine holds more than a bound of what nobody
    took. Output is queued and written once per loop iteration, so a head,
    its body and its end share one write. After a protocol switch the
    connection carries raw bytes for a tunnel.
    """

    def __init__(
        self,
        role: h11r.Role,
        *,
        max_head_bytes: int,
        on_connected: Callable[["AioH1Connection"], None] | None = None,
        on_closed: Callable[["AioH1Connection"], None] | None = None,
    ) -> None:
        self.engine = h11r.Connection(role, max_head_bytes=max_head_bytes)
        self.peer: str | None = None
        self.tls = False
        # Called once if the connection is lost while an exchange runs on it.
        self.on_lost: Callable[[], object] | None = None
        self._on_connected = on_connected
        self._on_closed = on_closed

        self._loop: asyncio.AbstractEventLoop | None = None
        self._transport: asyncio.Transport | None = None
        self._reading_paused = False
        self._eof = False
        self._lost = False
        self._failure: H1ConnectionError | None = None
        # The peer's first violation of the protocol, which the engine reports only once.
        self._violation: h11r.RemoteProtocolError | None = None
        self._receive_waiter: asyncio.Future[None] | None = None
        self._closed_waiter: asyncio.Future[None] | None = None

        self._pending: list[Any] = []
        self._pending_size = 0
        self._flush_scheduled = False
        self._write_paused = False
        self._drain_waiters: list[asyncio.Future[None]] = []

        # Set once the connection carries raw bytes after a protocol switch.
        self.tunneled = False
        self.tunnel_chunks: deque[bytes] = deque()
        self._tunnel_size = 0

    @property
    def closed(self) -> bool:
        return self._lost

    @property
    def usable(self) -> bool:
        """Return whether the connection may carry another exchange as far as this side knows."""
        transport = self._transport
        return not (self._lost or self._eof or transport is None or transport.is_closing())

    @property
    def peer_finished(self) -> bool:
        """Return whether the peer finished sending, rather than the connection failing."""
        return self._eof

    # Receiving.

    def violation(self, error: h11r.RemoteProtocolError) -> H1ConnectionError:
        """Return the failure that reports the peer's first violation of the protocol, which ``error`` follows or is."""
        if self._violation is None:
            self._violation = error
        first = self._violation
        return H1ConnectionError(str(first), status=first.suggested_status_code)

    async def next_event(self) -> Any:
        """Return the engine's next event, or `h11r.ReceiveStatus.PAUSED`, waiting for bytes while it needs them.

        Raises:
            H1ConnectionError: The peer broke the protocol, or the connection
                closed before the event.
        """
        engine = self.engine
        while True:
            try:
                event = engine.next_event()
            except h11r.RemoteProtocolError as error:
                raise self.violation(error) from error
            if event is not h11r.ReceiveStatus.NEED_DATA:
                return event
            if self._lost and self._failure is not None:
                raise self._failure
            # The engine has consumed its input, so reading paused at the limit may go on.
            self._resume_reading()
            await self._wait_to_receive()

    async def wait_for_bytes(self) -> None:
        """Wait until the peer sends something or stops sending."""
        while not self.engine.buffered_nbytes and not self._eof and not self._lost:
            self._resume_reading()
            await self._wait_to_receive()

    async def _wait_to_receive(self) -> None:
        assert self._loop is not None
        waiter = self._receive_waiter = self._loop.create_future()
        try:
            await waiter
        finally:
            self._receive_waiter = None

    def _wake_receiver(self) -> None:
        waiter = self._receive_waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(None)

    def _resume_reading(self) -> None:
        if self._reading_paused and self._transport is not None and not self._lost:
            self._reading_paused = False
            self._transport.resume_reading()

    def _pause_reading(self) -> None:
        if not self._reading_paused and self._transport is not None:
            self._reading_paused = True
            self._transport.pause_reading()

    def take_tunnel_bytes(self, max_bytes: int) -> bytes:
        """Take up to ``max_bytes`` of what a tunnel received."""
        chunks = self.tunnel_chunks
        chunk = chunks.popleft()
        if len(chunk) > max_bytes:
            chunks.appendleft(chunk[max_bytes:])
            chunk = chunk[:max_bytes]
        self._tunnel_size -= len(chunk)
        if self._tunnel_size < _READ_LIMIT:
            self._resume_reading()
        return chunk

    async def wait_for_tunnel_bytes(self) -> None:
        while not self.tunnel_chunks and not self._eof and not self._lost:
            self._resume_reading()
            await self._wait_to_receive()

    def switch_to_tunnel(self) -> None:
        """Carry raw bytes from now on, starting with those the engine received after the switch."""
        data, closed = self.engine.trailing_data
        self.tunneled = True
        # The tunnel reports its own end; losing the connection no longer cancels the handler.
        self.on_lost = None
        if data:
            self.tunnel_chunks.append(bytes(data))
            self._tunnel_size = len(data)
        self._eof = self._eof or closed
        self._resume_reading()

    # Sending.

    def send(self, produce: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Queue what an engine call produces.

        Raises:
            H1ExchangeError: The engine refuses the call.
            H1ConnectionError: The connection is closed.
        """
        self.raise_if_closed()
        try:
            output = produce(*args, **kwargs)
        except h11r.LocalProtocolError as error:
            raise H1ExchangeError(str(error)) from error
        if type(output) is tuple:
            for part in output:
                self.write(part)
        else:
            self.write(output)

    def write(self, data: Any) -> None:
        """Queue ``data`` for the end of the loop iteration, or write at once beyond a bound."""
        size = len(data) if not isinstance(data, memoryview) else data.nbytes
        if not size:
            return
        # Writes of one loop iteration go to the socket together.
        self._pending.append(data)
        self._pending_size += size
        if self._pending_size >= _WRITE_LIMIT:
            self.flush()
        elif not self._flush_scheduled and self._loop is not None:
            self._flush_scheduled = True
            self._loop.call_soon(self._scheduled_flush)

    def _scheduled_flush(self) -> None:
        self._flush_scheduled = False
        self.flush()

    def flush(self) -> None:
        """Hand the queued output to the socket."""
        pending = self._pending
        if not pending:
            return
        self._pending = []
        self._pending_size = 0
        transport = self._transport
        if transport is None or transport.is_closing():
            return
        if len(pending) == 1:
            transport.write(pending[0])
        else:
            transport.writelines(pending)

    async def drain(self) -> None:
        """Wait while the socket takes no more output.

        Raises:
            H1ConnectionError: The connection closed.
        """
        while self._write_paused:
            self.raise_if_closed()
            assert self._loop is not None
            waiter = self._loop.create_future()
            self._drain_waiters.append(waiter)
            await waiter
        self.raise_if_closed()

    def raise_if_closed(self) -> None:
        if self._lost:
            raise self._failure or H1ConnectionError("the HTTP/1.1 connection is closed")
        transport = self._transport
        if transport is None or transport.is_closing():
            raise H1ConnectionError("the HTTP/1.1 connection is closed")

    def write_eof(self) -> None:
        """Finish sending raw bytes, keeping the connection open to receive.

        Raises:
            H1ConnectionError: The connection is closed or cannot be half-closed.
        """
        self.raise_if_closed()
        transport = self._transport
        assert transport is not None
        if not transport.can_write_eof():
            raise H1ConnectionError("the connection cannot be half-closed")
        self.flush()
        transport.write_eof()

    # Closing.

    def close(self) -> None:
        """Close after the queued output is written."""
        self.flush()
        transport = self._transport
        if transport is None:
            self.connection_lost(None)
        elif not transport.is_closing():
            transport.close()

    def abort(self) -> None:
        """Close at once, discarding output not yet written."""
        self._pending.clear()
        self._pending_size = 0
        transport = self._transport
        if transport is None:
            self.connection_lost(None)
        else:
            transport.abort()

    async def wait_closed(self) -> None:
        if self._lost:
            return
        assert self._loop is not None
        if self._closed_waiter is None:
            self._closed_waiter = self._loop.create_future()
        await asyncio.shield(self._closed_waiter)

    # asyncio callbacks.

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._loop = asyncio.get_running_loop()
        self._transport = cast(asyncio.Transport, transport)
        peername = transport.get_extra_info("peername")
        self.peer = format_address(*peername[:2]) if isinstance(peername, tuple) else None
        self.tls = transport.get_extra_info("ssl_object") is not None
        if self._on_connected is not None:
            self._on_connected(self)

    def get_buffer(self, sizehint: int) -> memoryview:
        return read_buffer()

    def buffer_updated(self, nbytes: int) -> None:
        # The buffer is the thread's: what is kept of it is copied before this returns.
        data = read_buffer()[:nbytes]
        # A tunnel takes raw bytes; otherwise the HTTP/1.1 engine parses them.
        if self.tunneled:
            self.tunnel_chunks.append(bytes(data))
            self._tunnel_size += nbytes
            if self._tunnel_size >= _READ_LIMIT:
                self._pause_reading()
        else:
            try:
                self.engine.receive_data(data)
            except h11r.ProtocolError as error:
                self._failure = H1ConnectionError(str(error))
                self.abort()
                return
            # Reading pauses while the engine holds unread input past the limit.
            if self.engine.buffered_nbytes >= _READ_LIMIT:
                self._pause_reading()

        self._wake_receiver()

    def eof_received(self) -> bool:
        self._eof = True
        if not self.tunneled:
            # The engine reports a message cut short as an error, and one read to the close as complete.
            with suppress(h11r.ProtocolError):
                self.engine.receive_data(b"")
        self._wake_receiver()
        # A tunnel may still send after the peer finished; HTTP/1.1 closes the connection, and so does TLS, which
        # cannot half-close.
        return self.tunneled and not self.tls

    def pause_writing(self) -> None:
        self._write_paused = True

    def resume_writing(self) -> None:
        self._write_paused = False
        self._wake_drainers()

    def connection_lost(self, exc: Exception | None) -> None:
        if self._lost:
            return

        self._lost = True
        if self._failure is None:
            detail = "" if exc is None else f": {exc}"
            self._failure = H1ConnectionError(f"the HTTP/1.1 connection closed{detail}")
        if not self._eof and not self.tunneled:
            self._eof = True
            with suppress(h11r.ProtocolError):
                self.engine.receive_data(b"")
        self._pending.clear()

        # Whoever waits is told: readers, writers, the handler and the owner.
        self._wake_receiver()
        self._wake_drainers()
        on_lost, self.on_lost = self.on_lost, None
        if on_lost is not None:
            on_lost()
        waiter = self._closed_waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(None)
        if self._on_closed is not None:
            self._on_closed(self)

    def _wake_drainers(self) -> None:
        waiters, self._drain_waiters = self._drain_waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)
