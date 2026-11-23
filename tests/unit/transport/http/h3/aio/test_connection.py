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
import time
from types import SimpleNamespace

import pytest

ngquic = pytest.importorskip("ngquic")

from dubbo.transport.http.h3 import H3ConnectionError, H3Priority  # noqa: E402
from dubbo.transport.http.h3.aio.connection import AioH3Connection  # noqa: E402
from dubbo.transport.http.h3.aio.endpoint import AioQuicEndpoint  # noqa: E402

pytestmark = pytest.mark.anyio


class _Engine:
    """An engine whose next deadline is fixed, recording the times its timer is handled at."""

    is_client = True
    remote_addr = ("127.0.0.1", 4433)
    state = ngquic.ConnectionState.ESTABLISHED

    def __init__(self, due: float) -> None:
        self.due = due
        self.handled: list[float] = []

    def get_timer(self) -> float:
        return self.due

    def handle_timer(self, now: float) -> None:
        self.handled.append(now)

    def events(self) -> list[object]:
        return []

    def datagrams_to_send(self, now: float, *, max_datagrams: int, gso: bool) -> list[object]:
        return []


async def test_a_timer_that_fires_early_hands_the_engine_its_deadline() -> None:
    endpoint = AioQuicEndpoint()
    endpoint._loop = asyncio.get_running_loop()
    endpoint._clock = lambda: 9.9995
    engine = _Engine(due=10.0)
    connection = AioH3Connection(endpoint, engine)  # ty: ignore[invalid-argument-type]
    connection._timer_at = 10.0

    connection._on_timer()

    # The engine ignores a call before its deadline, so an early timer would otherwise do nothing.
    assert engine.handled == [10.0]


async def test_a_timer_that_fires_late_hands_the_engine_the_time() -> None:
    endpoint = AioQuicEndpoint()
    endpoint._loop = asyncio.get_running_loop()
    endpoint._clock = lambda: 10.25
    engine = _Engine(due=10.0)
    connection = AioH3Connection(endpoint, engine)  # ty: ignore[invalid-argument-type]
    connection._timer_at = 10.0

    connection._on_timer()

    assert engine.handled == [10.25]


async def test_a_timer_whose_deadline_moved_later_hands_the_engine_only_the_time_it_was_armed_for() -> None:
    endpoint = AioQuicEndpoint()
    endpoint._loop = asyncio.get_running_loop()
    endpoint._clock = lambda: 9.9995
    engine = _Engine(due=10.0)
    connection = AioH3Connection(endpoint, engine)  # ty: ignore[invalid-argument-type]
    connection._timer_at = 10.0
    # An acknowledgement received in the same loop iteration leaves only the idle timeout due.
    engine.due = 40.0

    connection._on_timer()

    assert engine.handled == [10.0]


def test_the_engine_reads_the_monotonic_clock_under_uvloop_and_the_loop_clock_otherwise() -> None:
    uvloop = pytest.importorskip("uvloop")

    async def clock() -> object:
        endpoint = AioQuicEndpoint()
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: endpoint, local_addr=("127.0.0.1", 0)
        )
        transport.close()
        return endpoint._clock

    # uvloop reads its clock once per iteration, in whole milliseconds.
    assert uvloop.run(clock()) is time.monotonic
    clock_of_asyncio = asyncio.run(clock())
    assert getattr(clock_of_asyncio, "__func__", None) is asyncio.BaseEventLoop.time


class _Requests(_Engine):
    """An engine that takes request headers, for a connection whose socket takes no writes."""

    bidi_streams_available = 100

    def __init__(self) -> None:
        super().__init__(due=float("inf"))

    def send_request(self, fields: object, *, end_stream: bool) -> int:
        return 0


async def test_a_connection_lost_after_the_request_headers_went_out_fails_the_stream_it_returns() -> None:
    endpoint = AioQuicEndpoint()
    endpoint._loop = asyncio.get_running_loop()
    endpoint.schedule = lambda connection: None  # ty: ignore[invalid-assignment]
    endpoint.write_paused = True
    connection = AioH3Connection(endpoint, _Requests())  # ty: ignore[invalid-argument-type]
    connection._established.set()

    opening = asyncio.ensure_future(connection.open_stream(((b":method", b"POST"),), end_stream=True))
    await asyncio.sleep(0)
    connection._fail(H3ConnectionError("lost"))
    stream = await opening

    # The server may have the request, which a new connection must not send again; the call ends as unavailable.
    assert stream is not None
    with pytest.raises(H3ConnectionError, match="lost"):
        await stream.receive_response()


@pytest.mark.parametrize(
    ("error_code", "lost"),
    [(0x100, False), (0x21, False), (0x1F * 7 + 0x21, False), (0x102, True)],
    ids=["no-error", "reserved", "unknown", "internal-error"],
)
async def test_a_peer_closing_with_an_unknown_code_closes_as_with_no_error(error_code: int, lost: bool) -> None:
    endpoint = AioQuicEndpoint()
    endpoint._loop = asyncio.get_running_loop()
    connection = AioH3Connection(endpoint, _Engine(due=float("inf")))  # ty: ignore[invalid-argument-type]
    event = SimpleNamespace(reason=ngquic.CloseReason.APPLICATION_ERROR, error_code=error_code, by_peer=True)

    connection._handle_terminated(event)  # ty: ignore[invalid-argument-type]

    assert connection.lost is lost


async def test_the_priority_of_a_stream_of_a_closed_connection_is_the_default() -> None:
    class _Closed(_Engine):
        def get_stream_priority(self, stream_id: int) -> object:
            raise ngquic.ConnectionStateError("closed")

    endpoint = AioQuicEndpoint()
    connection = AioH3Connection(endpoint, _Closed(due=float("inf")))  # ty: ignore[invalid-argument-type]

    assert connection.priority(0) == H3Priority()
