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
from functools import partial
from types import TracebackType
from typing import Self

from dubbo.transport.http.h1.base import H1Tunnel
from dubbo.transport.http.h1.exceptions import H1ConnectionError, H1ExchangeError
from dubbo.transport.http.streams import OperationGuard
from dubbo.utils import close_then_abort

from .connection import AioH1Connection

__all__ = ["AioH1Tunnel"]


class AioH1Tunnel(H1Tunnel):
    """The raw bytes of an asyncio-backed HTTP/1.1 connection after a protocol switch."""

    def __init__(self, connection: AioH1Connection) -> None:
        self._connection = connection
        self._send_guard = OperationGuard(partial(H1ExchangeError, "another task is already sending on this tunnel"))
        self._receive_guard = OperationGuard(
            partial(H1ExchangeError, "another task is already receiving from this tunnel")
        )
        self._eof_sent = False

    @property
    def peer(self) -> str | None:
        return self._connection.peer

    @property
    def closed(self) -> bool:
        return self._connection.closed

    async def receive(self, max_bytes: int = 64 * 1024) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        connection = self._connection
        with self._receive_guard:
            while not connection.tunnel_chunks:
                if connection.peer_finished:
                    return b""
                connection.raise_if_closed()
                await connection.wait_for_tunnel_bytes()
            return connection.take_tunnel_bytes(max_bytes)

    async def send(self, data: bytes) -> None:
        with self._send_guard:
            if self._eof_sent:
                raise H1ConnectionError("this side finished sending on the tunnel")
            connection = self._connection
            connection.raise_if_closed()
            connection.write(data)
            try:
                await connection.drain()
            except asyncio.CancelledError:
                connection.abort()
                raise

    async def send_eof(self) -> None:
        with self._send_guard:
            if self._eof_sent:
                return
            self._connection.write_eof()
            self._eof_sent = True

    async def aclose(self) -> None:
        connection = self._connection
        connection.close()
        await close_then_abort(connection.wait_closed, lambda: connection.closed, connection.abort)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.aclose()
