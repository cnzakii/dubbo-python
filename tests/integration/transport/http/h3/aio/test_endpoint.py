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
import errno
import socket
import sys
from types import SimpleNamespace
from typing import ClassVar

import anyio
import pytest

pytest.importorskip("ngquic")

from dubbo.transport.http.h3.aio.endpoint import AioQuicEndpoint, open_endpoint

pytestmark = pytest.mark.anyio


@pytest.mark.skipif(sys.platform == "win32", reason="ngquic has no batched UDP on Windows")
async def test_a_loop_with_add_reader_carries_the_socket_through_batched_system_calls() -> None:
    from dubbo.transport.http.h3.aio.datagram import BatchedSocket

    endpoint = AioQuicEndpoint()

    await open_endpoint(endpoint, local=("127.0.0.1", 0))

    assert isinstance(endpoint.transport, BatchedSocket)
    endpoint.close()
    await endpoint.wait_closed()


async def test_a_loop_without_add_reader_carries_the_socket_through_its_own_transport(monkeypatch) -> None:
    loop = asyncio.get_running_loop()

    def unsupported(*args):
        raise NotImplementedError

    monkeypatch.setattr(loop, "add_reader", unsupported)
    endpoint = AioQuicEndpoint()

    await open_endpoint(endpoint, local=("127.0.0.1", 0))

    transport = endpoint.transport
    endpoint.close()
    await endpoint.wait_closed()

    assert type(transport).__module__.startswith("asyncio.")


async def test_a_loop_without_add_reader_sends_to_the_peer_of_a_client_endpoint(monkeypatch) -> None:
    loop = asyncio.get_running_loop()

    def unsupported(*args):
        raise NotImplementedError

    monkeypatch.setattr(loop, "add_reader", unsupported)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind(("127.0.0.1", 0))
        peer.settimeout(1)
        endpoint = AioQuicEndpoint()
        await open_endpoint(endpoint, remote=peer.getsockname(), family=socket.AF_INET)

        endpoint.send(_transmit(b"ping", 0))
        received = peer.recv(16)
        endpoint.close()
        await endpoint.wait_closed()

    assert received == b"ping"


class _ScriptedSocket:
    """Stands for ngquic's UDPSocket: each send takes the next item of ``script``, an error to raise or None to send."""

    script: ClassVar[list[Exception | None]] = []
    sent: ClassVar[list[bytes]] = []

    def __init__(self, sock: socket.socket) -> None:
        self.gso = False

    def send(self, transmit) -> None:
        result = self.script.pop(0)
        if result is not None:
            raise result
        self.sent.append(transmit.data)


class _Endpoint:
    def __init__(self) -> None:
        self.errors: list[Exception] = []
        self.engine = None

    def error_received(self, error: Exception) -> None:
        self.errors.append(error)

    def connection_lost(self, error) -> None:
        pass


def _transmit(data: bytes, segment_size: int) -> SimpleNamespace:
    return SimpleNamespace(data=data, remote_addr=("127.0.0.1", 1), local_addr=None, segment_size=segment_size, ecn=0)


@pytest.mark.skipif(sys.platform == "win32", reason="ngquic has no batched UDP on Windows")
async def test_transmits_the_socket_refuses_go_out_in_order_once_it_is_writable(monkeypatch) -> None:
    from dubbo.transport.http.h3.aio import datagram

    monkeypatch.setattr(datagram, "UDPSocket", _ScriptedSocket)
    monkeypatch.setattr(_ScriptedSocket, "sent", [])
    # The buffer is full for the first transmit; the second waits behind it without a send.
    monkeypatch.setattr(_ScriptedSocket, "script", [BlockingIOError(), None, None])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        transport = datagram.BatchedSocket(asyncio.get_running_loop(), sock, _Endpoint())

        transport.send(_transmit(b"a" * 20, 10))
        transport.send(_transmit(b"b" * 10, 0))
        assert transport.get_write_buffer_size() == 30

        with anyio.fail_after(1):
            while transport.get_write_buffer_size():
                await anyio.sleep(0)
        transport.abort()

    assert _ScriptedSocket.sent == [b"a" * 20, b"b" * 10]


@pytest.mark.skipif(sys.platform == "win32", reason="ngquic has no batched UDP on Windows")
async def test_a_send_that_fails_reports_the_error_and_queues_nothing(monkeypatch) -> None:
    from dubbo.transport.http.h3.aio import datagram

    refused = OSError(errno.EMSGSIZE, "Message too long")
    monkeypatch.setattr(datagram, "UDPSocket", _ScriptedSocket)
    monkeypatch.setattr(_ScriptedSocket, "sent", [])
    monkeypatch.setattr(_ScriptedSocket, "script", [refused])
    endpoint = _Endpoint()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        transport = datagram.BatchedSocket(asyncio.get_running_loop(), sock, endpoint)

        transport.send(_transmit(bytes(30), 10))

        assert (endpoint.errors, transport.get_write_buffer_size()) == ([refused], 0)
        transport.abort()
