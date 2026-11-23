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
from types import SimpleNamespace

import pytest

pytest.importorskip("ngquic")

from dubbo.transport.http.h3.aio.endpoint import AioQuicEndpoint

pytestmark = pytest.mark.anyio


def _windows_error(code: int) -> OSError:
    """An error as an overlapped read on Windows reports it: a Windows error code, whose errno names no ICMP error."""
    error = OSError(errno.EINVAL, "unreachable")
    error.winerror = code  # ty: ignore[unresolved-attribute]
    return error


@pytest.mark.parametrize(
    ("error", "unreachable"),
    [
        (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), True),
        (OSError(errno.EHOSTUNREACH, "no route"), True),
        (OSError(errno.ENOBUFS, "no buffer space"), False),
        (OSError(errno.EMSGSIZE, "message too long"), False),
        (_windows_error(1234), True),
        (_windows_error(1231), True),
    ],
    ids=["port", "host", "buffer", "size", "windows-port", "windows-network"],
)
def test_only_an_icmp_unreachable_error_fails_the_handshakes_of_a_connected_socket(error, unreachable) -> None:
    endpoint = AioQuicEndpoint()
    endpoint._connected = True
    failed: list[Exception] = []
    endpoint._connections = {b"id": SimpleNamespace(network_error=failed.append)}  # ty: ignore[invalid-assignment]

    endpoint.error_received(error)

    assert failed == ([error] if unreachable else [])


@pytest.mark.parametrize(
    ("remote", "host"),
    [(("fe80::1", 443, 0, 3), "fe80::1%3"), (("::1", 443, 0, 0), "::1"), (("127.0.0.1", 443), "127.0.0.1")],
    ids=["link-local", "ipv6", "ipv4"],
)
async def test_a_loop_without_add_reader_connects_to_the_scope_of_an_ipv6_address(monkeypatch, remote, host) -> None:
    from dubbo.transport.http.h3.aio import endpoint as module

    asked = []

    async def create_datagram_endpoint(factory, *, remote_addr, family):
        asked.append(remote_addr)
        return SimpleNamespace(get_extra_info=lambda name: SimpleNamespace(setsockopt=lambda *args: None)), None

    # As on Windows, whose loop reads and writes datagrams itself.
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", create_datagram_endpoint)

    await module.open_endpoint(AioQuicEndpoint(), remote=remote)

    assert asked == [(host, 443)]
