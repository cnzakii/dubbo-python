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

import logging

import anyio
import pytest

from dubbo.exceptions import RpcError, Status
from dubbo.extension import extensions
from dubbo.protocol.triple import connection
from dubbo.protocol.triple.connection import TripleClient, http3_server_url
from dubbo.transport.http.h2 import H2Client
from dubbo.transport.http.h3 import H3Client, H3ConnectionError, H3StreamError
from dubbo.url import URL

pytestmark = pytest.mark.anyio


class _Client:
    """A client of one HTTP version whose streams are itself.

    It connects once entered, while the server is not ``unreachable``, and fails its handshake while ``refusing``.
    """

    def __init__(self, url: URL) -> None:
        self.url = url
        self.live = False
        self.closed = False
        self.unreachable = False
        self.refusing = False
        self.drained = False
        self.requests: list[tuple] = []

    @property
    def available(self) -> bool:
        return not self.unreachable and not self.refusing and not self.closed

    @property
    def connected(self) -> bool:
        return self.live and self.available

    async def connect(self) -> None:
        if self.refusing:
            raise H3ConnectionError("the handshake failed")
        while self.unreachable:
            await anyio.sleep(0.01)
        self.live = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> None:
        pass

    async def aclose(self) -> None:
        self.closed = True

    async def drain(self) -> None:
        self.drained = True
        await self.aclose()

    async def open_stream(self, headers, *, end_stream=False):
        if self.unreachable:
            raise H3ConnectionError("the server cannot be reached")
        self.requests.append(headers)
        return self


@pytest.fixture
def made(isolated_extensions) -> dict[str, list[_Client]]:
    """The clients made of each version; those of HTTP/3 cannot reach the server while ``unreachable`` is set."""
    made: dict[str, list[_Client]] = {"h2": [], "h3": []}
    unreachable: list[bool] = []
    refusing: list[bool] = []

    def h2(url: URL) -> _Client:
        made["h2"].append(client := _Client(url))
        client.live = True
        return client

    def h3(url: URL) -> _Client:
        made["h3"].append(client := _Client(url))
        client.unreachable = bool(unreachable)
        client.refusing = bool(refusing)
        return client

    extensions.register(H2Client, "memory", h2)
    extensions.register(H3Client, "memory", h3)
    made["unreachable"] = unreachable  # type: ignore[assignment]
    made["refusing"] = refusing  # type: ignore[assignment]
    return made


def _url(**parameters: str) -> URL:
    return URL(
        protocol="tri",
        host="127.0.0.1",
        port=1,
        parameters={"client": "memory", "ssl-enabled": "true", "http3.enabled": "true"} | parameters,
    )


async def _announce(client: TripleClient, alt_svc: bytes = b'h3=":2"') -> None:
    client.alt_svc(alt_svc)
    await anyio.wait_all_tasks_blocked()


async def test_calls_move_to_http3_once_it_is_available(made) -> None:
    async with TripleClient(_url(**{"http3.max-concurrent-streams": "5"})) as client:
        assert await client.open_stream(()) is made["h2"][0]

        await _announce(client, b'h2=":1", h3=":2"; ma=3600')
        (h3,) = made["h3"]
        assert await client.open_stream(((b":path", b"/s/m"),)) is h3

    assert h3.requests == [((b":path", b"/s/m"),)]
    assert (h3.url.port, h3.url.parameters["max-concurrent-streams"]) == (2, "5")
    assert not any(key.startswith("http3.") for key in h3.url.parameters)
    assert h3.closed
    assert made["h2"][0].closed


async def test_calls_stay_on_http2_while_http3_connects_and_move_once_it_has(made, caplog) -> None:
    made["unreachable"].append(True)
    async with TripleClient(_url()) as client:
        await _announce(client)
        (h3,) = made["h3"]
        assert await client.open_stream(()) is made["h2"][0]

        await _announce(client)
        h3.unreachable = False
        with caplog.at_level(logging.INFO):
            await anyio.sleep(0.01)
        assert await client.open_stream(()) is h3
        assert len(made["h3"]) == 1

    assert caplog.messages == ["Calls to 127.0.0.1:1 move to HTTP/3"]


async def test_a_call_after_the_idle_timeout_goes_over_http2_while_http3_connects_again(made, caplog) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client)
        (h3,) = made["h3"]
        h3.live = False
        h3.unreachable = True

        with caplog.at_level(logging.INFO):
            assert await client.open_stream(()) is made["h2"][0]
            assert await client.open_stream(()) is made["h2"][0]
            h3.unreachable = False
            await anyio.sleep(0.01)
            assert await client.open_stream(()) is h3

    # Connecting again is routine.
    assert caplog.messages == []


async def test_a_failed_handshake_drops_http3_on_its_port_for_twice_as_long_each_time(
    made, monkeypatch, caplog
) -> None:
    # 2 seconds in place of 5 minutes, so that the test stays within its time.
    monkeypatch.setattr(connection, "_BROKEN_DELAY", 2.0)
    made["refusing"].append(True)
    async with TripleClient(_url()) as client:
        with caplog.at_level(logging.INFO):
            await _announce(client)
            assert made["h3"][0].drained
            assert await client.open_stream(()) is made["h2"][0]

            await anyio.sleep(1.5)
            await _announce(client)
            assert len(made["h3"]) == 1
            await anyio.sleep(0.5)
            await _announce(client)
            assert len(made["h3"]) == 2

            await anyio.sleep(3)
            await _announce(client)
            assert len(made["h3"]) == 2
            await anyio.sleep(1)
            made["refusing"].clear()
            await _announce(client)
            assert await client.open_stream(()) is made["h3"][2]

            made["h3"][2].refusing = True
            made["h3"][2].live = False
            assert await client.open_stream(()) is made["h2"][0]
            await anyio.wait_all_tasks_blocked()

    assert caplog.messages == [
        "Calls to 127.0.0.1:1 go back to HTTP/2 for 2 seconds: the handshake failed",
        "Calls to 127.0.0.1:1 go back to HTTP/2 for 4 seconds: the handshake failed",
        "Calls to 127.0.0.1:1 move to HTTP/3",
        # A success forgets the failures before it.
        "Calls to 127.0.0.1:1 go back to HTTP/2 for 2 seconds: the handshake failed",
    ]


async def test_a_failed_handshake_leaves_http3_on_another_port_to_try(made) -> None:
    made["refusing"].append(True)
    async with TripleClient(_url()) as client:
        await _announce(client, b'h3=":2"')
        made["refusing"].clear()
        await _announce(client, b'h3=":3"')

        assert await client.open_stream(()) is made["h3"][1]


async def test_a_lost_http3_connection_moves_calls_back_to_http2_until_it_connects_again(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client)
        (h3,) = made["h3"]
        h3.live = False

        assert await client.open_stream(()) is made["h2"][0]
        await anyio.wait_all_tasks_blocked()
        assert await client.open_stream(()) is h3
        assert not h3.drained


async def test_a_call_whose_http3_stream_fails_before_its_request_goes_out_moves_to_http2(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client)
        (h3,) = made["h3"]

        async def fail_before_sending(headers, *, end_stream=False):
            raise H3ConnectionError("the connection failed before the headers")

        h3.open_stream = fail_before_sending
        assert await client.open_stream(()) is made["h2"][0]
        assert not h3.drained


async def test_a_call_waiting_on_http3_as_it_is_closed_goes_over_http2(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client)
        released = anyio.Event()

        async def open_once_closed(headers, *, end_stream=False):
            await released.wait()
            raise RuntimeError("the HTTP/3 client is closed")

        made["h3"][0].open_stream = open_once_closed
        opened = []

        async def call() -> None:
            opened.append(await client.open_stream(()))

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(call)
            await anyio.wait_all_tasks_blocked()
            await _announce(client, b"clear")
            released.set()

        assert opened == [made["h2"][0]]


async def test_calls_leave_a_stale_announcement_for_http2_until_it_is_renewed(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client, b'h3=":2"; ma=2')
        await anyio.sleep(2)

        assert await client.open_stream(()) is made["h2"][0]
        await _announce(client, b'h3=":2"; ma=2')
        assert await client.open_stream(()) is made["h3"][0]
        assert len(made["h3"]) == 1


async def test_clear_closes_http3(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client)
        await _announce(client, b"clear")

        assert made["h3"][0].closed
        assert await client.open_stream(()) is made["h2"][0]


@pytest.mark.parametrize(
    "alt_svc",
    [b'h3="other.example:2"', b'h2=":2"', b'h3=":2"; ma=0', b"Clear", b'clear, h3=":2"'],
    ids=["other-host", "other-protocol", "expired", "clear", "clear-among-others"],
)
async def test_an_announcement_without_fresh_http3_on_the_host_drains_it(made, alt_svc) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client, b'h3="127.0.0.1:2"')
        await _announce(client, alt_svc)

        assert made["h3"][0].drained
        assert await client.open_stream(()) is made["h2"][0]
        await _announce(client, alt_svc)
        assert len(made["h3"]) == 1


async def test_an_announcement_that_names_nothing_valid_leaves_the_earlier_one(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client, b'h3="127.0.0.1:2"')
        await _announce(client, b'h3=":0", h3="2"')

        assert await client.open_stream(()) is made["h3"][0]


async def test_an_announcement_of_another_port_moves_http3_there(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client, b'h3=":2"')
        await _announce(client, b'h3=":3"')

        first, second = made["h3"]
        assert first.drained
        assert second.url.port == 3
        assert await client.open_stream(()) is second


async def test_announcements_in_one_turn_of_the_loop_use_the_last(made) -> None:
    async with TripleClient(_url()) as client:
        client.alt_svc(b'h3=":2"')
        client.alt_svc(b"clear")
        client.alt_svc(b'h3=":3"')
        await anyio.wait_all_tasks_blocked()

        first, last = made["h3"]
        assert (first.drained, last.url.port) == (True, 3)
        assert await client.open_stream(()) is last


async def test_another_port_announced_while_http3_connects_moves_there_at_once(made) -> None:
    made["unreachable"].append(True)
    async with TripleClient(_url()) as client:
        await _announce(client, b'h3=":2"')
        made["unreachable"].clear()
        await _announce(client, b'h3=":3"')

        first, second = made["h3"]
        assert first.drained
        assert await client.open_stream(()) is second


async def test_an_http3_client_that_cannot_start_leaves_calls_on_http2(made, monkeypatch, caplog) -> None:
    entered = _Client.__aenter__

    async def refuse(self):
        if self.url.port != 1:
            raise ValueError("the engine refused the address")
        return await entered(self)

    monkeypatch.setattr(_Client, "__aenter__", refuse)
    async with TripleClient(_url()) as client:
        with caplog.at_level(logging.WARNING):
            await _announce(client)
        await _announce(client, b'h3=":3"')

        assert made["h3"][0].drained
        assert len(made["h3"]) == 1
        assert await client.open_stream(()) is made["h2"][0]
        assert "the engine refused the address" in caplog.text


async def test_a_failure_after_the_request_headers_went_out_stays_on_http3(made) -> None:
    async with TripleClient(_url()) as client:
        await _announce(client)

        async def fail_after_sending(headers, *, end_stream=False):
            raise H3StreamError("the connection failed after the headers", stream_id=0)

        made["h3"][0].open_stream = fail_after_sending
        with pytest.raises(H3StreamError):
            await client.open_stream(())
        assert made["h2"][0].requests == []


async def test_closing_the_client_closes_http3_still_connecting(made) -> None:
    made["unreachable"].append(True)
    async with TripleClient(_url()) as client:
        await _announce(client)
        (h3,) = made["h3"]
        assert await client.open_stream(()) is made["h2"][0]

    assert h3.closed


async def test_without_negotiation_calls_go_over_http3_alone(made) -> None:
    async with TripleClient(_url(**{"http3.negotiation": "false"})) as client:
        (h3,) = made["h3"]
        h3.live = False

        assert await client.open_stream(()) is h3
        assert not made["h2"]
        assert h3.url.port == 1


@pytest.mark.parametrize(
    "parameters",
    [{"http3.enabled": "false"}, {"ssl-enabled": "false"}],
    ids=["http3-disabled", "cleartext"],
)
async def test_announcements_are_ignored_unless_http3_is_enabled_over_tls(made, parameters) -> None:
    async with TripleClient(_url(**parameters)) as client:
        await _announce(client)

        assert not made["h3"]


async def test_an_http3_client_that_cannot_be_made_leaves_calls_on_http2(made, caplog) -> None:
    def refuse(url: URL) -> None:
        raise ValueError("no TLS files")

    extensions.register(H2Client, "refusing", extensions.get(H2Client, "memory"))
    extensions.register(H3Client, "refusing", refuse)
    async with TripleClient(_url(client="refusing")) as client:
        with caplog.at_level(logging.WARNING):
            await _announce(client)
        await _announce(client, b'h3=":3"')

        assert await client.open_stream(()) is made["h2"][0]
        assert "no TLS files" in caplog.text


def test_the_http_server_reads_the_http3_settings_by_their_own_prefix_without_the_switches() -> None:
    url = URL.parse("tri://h:1?http3.enabled=true&http3.negotiation=false&http3.idle-timeout=5&idle-timeout=9")

    assert http3_server_url(url) == URL.parse("tri://h:1?http3.idle-timeout=5&idle-timeout=9")


async def test_a_call_waiting_for_a_connection_as_the_client_closes_fails_as_unavailable(made) -> None:
    client = TripleClient(_url())
    async with client:
        released = anyio.Event()

        async def open_once_closed(headers, *, end_stream=False):
            await released.wait()
            raise RuntimeError("the HTTP/2 client is closed")

        made["h2"][0].open_stream = open_once_closed
        outcome: list[BaseException] = []

        async def call() -> None:
            try:
                await client.open_stream(())
            except Exception as error:
                outcome.append(error)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(call)
            await anyio.wait_all_tasks_blocked()
            await client.aclose()
            released.set()

    assert isinstance(outcome[0], RpcError)
    assert outcome[0].status is Status.UNAVAILABLE
