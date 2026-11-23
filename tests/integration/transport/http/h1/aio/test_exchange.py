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
from contextlib import ExitStack
from pathlib import Path

import anyio
import httpx
import pytest

from dubbo.transport.http.aio import AioHttpServer
from dubbo.transport.http.h1 import H1ConnectionError, H1ExchangeError
from dubbo.transport.http.h1.aio import AioH1Client

pytestmark = pytest.mark.anyio

HOST = (b"host", b"127.0.0.1")


async def read_body(exchange) -> bytes:
    body = bytearray()
    while chunk := await exchange.receive_data():
        body += chunk
    return bytes(body)


async def echo(exchange) -> None:
    """Answer with the request method, target and body."""
    request = exchange.request
    body = await read_body(exchange)
    await exchange.send_response(200, ((b"content-type", b"text/plain"),))
    await exchange.send_data(request.method + b" " + request.target + b" " + body, end_stream=True)


async def raw(port: int, data: bytes) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(data)
    return reader, writer


async def test_an_exchange_carries_a_request_and_its_response_and_the_connection_is_reused(h1_url) -> None:
    peers = []

    async def handler(exchange) -> None:
        peers.append(exchange.peer)
        assert not exchange.tls
        await echo(exchange)

    async with AioHttpServer(h1_url(), h1=handler) as server, AioH1Client(h1_url(server.address[1])) as client:
        for index in range(3):
            headers = (HOST, (b"content-length", b"5"))
            async with await client.request(b"POST", b"/echo?%d" % index, headers) as exchange:
                await exchange.send_data(b"hello", end_stream=True)
                response = await exchange.receive_response()
                assert response.status == 200
                assert response.http_version == b"1.1"
                assert (b"content-type", b"text/plain") in response.headers
                assert await read_body(exchange) == b"POST /echo?%d hello" % index
                assert await exchange.receive_trailers() == ()
    # One connection carried every exchange.
    assert len(set(peers)) == 1


async def test_aborting_a_completed_exchange_leaves_the_next_one_on_its_connection(h1_url) -> None:
    async with AioHttpServer(h1_url(), h1=echo) as server, AioH1Client(h1_url(server.address[1])) as client:
        headers = (HOST, (b"content-length", b"1"))
        first = await client.request(b"POST", b"/1", headers)
        await first.send_data(b"a", end_stream=True)
        await first.receive_response()
        assert await read_body(first) == b"POST /1 a"
        async with await client.request(b"POST", b"/2", headers) as second:
            await first.abort()
            await second.send_data(b"b", end_stream=True)
            assert (await second.receive_response()).status == 200
            assert await read_body(second) == b"POST /2 b"


async def test_a_body_read_to_its_last_byte_leaves_the_connection_reusable(h1_url) -> None:
    peers = set()

    async def handler(exchange) -> None:
        peers.add(exchange.peer)
        # The end of each body is never asked for, by the server or by the client.
        assert await exchange.receive_data_exactly(2) == b"hi"
        await exchange.send_response(200, ((b"content-length", b"5"),))
        await exchange.send_data(b"hello", end_stream=True)

    async with AioHttpServer(h1_url(), h1=handler) as server, AioH1Client(h1_url(server.address[1])) as client:
        for _ in range(3):
            headers = (HOST, (b"content-length", b"2"))
            async with await client.request(b"POST", b"/", headers) as exchange:
                await exchange.send_data(b"hi", end_stream=True)
                await exchange.receive_response()
                assert await exchange.receive_data_exactly(5) == b"hello"
    assert len(peers) == 1


async def test_httpx_reaches_the_server_with_fixed_and_chunked_bodies(h1_url) -> None:
    async def handler(exchange) -> None:
        body = await read_body(exchange)
        if exchange.request.target == b"/fixed":
            await exchange.send_response(200, ((b"content-length", b"%d" % len(body)),))
        else:
            await exchange.send_response(201)
        await exchange.send_data(body, end_stream=True)

    payload = bytes(range(256)) * 8192  # 2 MiB
    async with AioHttpServer(h1_url(), h1=handler) as server, httpx.AsyncClient() as client:
        base = f"http://127.0.0.1:{server.address[1]}"
        fixed = await client.post(f"{base}/fixed", content=payload)
        assert fixed.status_code == 200
        assert fixed.content == payload
        assert "transfer-encoding" not in fixed.headers

        async def stream():
            for offset in range(0, len(payload), 100_000):
                yield payload[offset : offset + 100_000]

        chunked = await client.post(f"{base}/chunked", content=stream())
        assert chunked.status_code == 201
        assert chunked.headers["transfer-encoding"] == "chunked"
        assert chunked.content == payload


async def test_an_http_1_0_response_ends_with_the_connection(h1_url) -> None:
    async with AioHttpServer(h1_url(), h1=echo) as server:
        reader, writer = await raw(server.address[1], b"GET /old HTTP/1.0\r\n\r\n")
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()
    assert answer.startswith(b"HTTP/1.1 200 ")
    assert b"connection: close" in answer.lower()
    assert answer.endswith(b"\r\n\r\nGET /old ")


async def test_pipelined_requests_are_answered_in_order(h1_url) -> None:
    requests = b"".join(b"GET /%d HTTP/1.1\r\nHost: a\r\n\r\n" % index for index in range(3))
    async with AioHttpServer(h1_url(), h1=echo) as server:
        reader, writer = await raw(
            server.address[1], requests + b"GET /last HTTP/1.1\r\nHost: a\r\nConnection: close\r\n\r\n"
        )
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()
    assert answer.count(b"HTTP/1.1 200 ") == 4
    positions = [answer.index(body) for body in (b"GET /0 ", b"GET /1 ", b"GET /2 ", b"GET /last ")]
    assert positions == sorted(positions)


async def test_a_request_that_expects_100_continue_gets_it_once_the_body_is_read(h1_url) -> None:
    async with AioHttpServer(h1_url(), h1=echo) as server:
        head = b"POST /up HTTP/1.1\r\nHost: a\r\nContent-Length: 4\r\nExpect: 100-continue\r\n\r\n"
        reader, writer = await raw(server.address[1], head)
        with anyio.fail_after(5):
            assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 100 \r\n\r\n"
            writer.write(b"data")
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200 ")
        writer.close()


async def test_interim_responses_arrive_in_order_before_the_final_one(h1_url) -> None:
    async def handler(exchange) -> None:
        await exchange.send_informational(103, ((b"link", b"</style.css>; rel=preload"),), reason=b"Early Hints")
        with pytest.raises(H1ExchangeError, match="switch_protocols"):
            await exchange.send_informational(101)
        await exchange.send_response(200, ((b"content-length", b"2"),), reason=b"OK")
        await exchange.send_data(b"ok", end_stream=True)
        with pytest.raises(H1ExchangeError, match="already sent"):
            await exchange.send_informational(100)

    async with (
        AioHttpServer(h1_url(), h1=handler) as server,
        AioH1Client(h1_url(server.address[1])) as client,
        await client.request(b"GET", b"/", (HOST,), end_stream=True) as exchange,
    ):
        with pytest.raises(H1ExchangeError, match="final response was not received"):
            await exchange.receive_data()
        hints = await exchange.receive_response()
        assert (hints.status, hints.reason) == (103, b"Early Hints")
        assert hints.headers == ((b"link", b"</style.css>; rel=preload"),)
        final = await exchange.receive_response()
        assert (final.status, final.reason) == (200, b"OK")
        assert await read_body(exchange) == b"ok"
        with pytest.raises(H1ExchangeError, match="already received"):
            await exchange.receive_response()


async def test_an_http_1_0_client_receives_no_interim_response(h1_url) -> None:
    refused = []

    async def handler(exchange) -> None:
        with pytest.raises(H1ExchangeError, match=r"HTTP/1\.0"):
            await exchange.send_informational(103)
        refused.append(True)
        await exchange.send_response(204, end_stream=True)

    async with AioHttpServer(h1_url(), h1=handler) as server:
        reader, writer = await raw(server.address[1], b"GET / HTTP/1.0\r\n\r\n")
        with anyio.fail_after(5):
            assert (await reader.read()).startswith(b"HTTP/1.1 204 ")
        writer.close()
    assert refused == [True]


async def test_chunked_bodies_carry_trailers_both_ways(h1_url) -> None:
    async def handler(exchange) -> None:
        body = await read_body(exchange)
        trailers = await exchange.receive_trailers()
        await exchange.send_response(200, ((b"transfer-encoding", b"chunked"),))
        await exchange.send_data(body)
        await exchange.send_trailers(((b"x-echo", dict(trailers)[b"x-sum"]),))

    async with AioHttpServer(h1_url(), h1=handler) as server, AioH1Client(h1_url(server.address[1])) as client:
        # TE is hop-by-hop, so Connection names it, RFC 9110 section 10.1.4.
        headers = (HOST, (b"transfer-encoding", b"chunked"), (b"te", b"trailers"), (b"connection", b"te"))
        async with await client.request(b"POST", b"/", headers) as exchange:
            await exchange.send_data(b"one ")
            await exchange.send_data(b"two")
            await exchange.send_trailers(((b"x-sum", b"42"),))
            await exchange.receive_response()
            assert await read_body(exchange) == b"one two"
            assert await exchange.receive_trailers() == ((b"x-echo", b"42"),)


async def test_trailers_need_a_chunked_message(h1_url) -> None:
    async def handler(exchange) -> None:
        await exchange.send_response(200, ((b"content-length", b"2"),))
        await exchange.send_data(b"ok")
        with pytest.raises(H1ExchangeError, match="chunked"):
            await exchange.send_trailers(((b"x", b"1"),))
        await exchange.end()

    async with AioHttpServer(h1_url(), h1=handler) as server, httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{server.address[1]}/")
        assert response.content == b"ok"


@pytest.mark.parametrize(
    ("request_bytes", "parameters", "status"),
    [
        (b"GET / HTTP/1.1\r\n\r\n", {}, b"400"),
        (b"GET / HTTP/1.1\r\nHost: a\r\nX-Big: " + b"y" * 4096 + b"\r\n\r\n", {"max_header_list_size": "1024"}, b"431"),
    ],
    ids=["missing-host", "head-too-large"],
)
async def test_a_request_that_breaks_the_protocol_is_answered_and_its_connection_closed(
    request_bytes, parameters, status, h1_url
) -> None:
    async with AioHttpServer(h1_url(**parameters), h1=echo) as server:
        reader, writer = await raw(server.address[1], request_bytes)
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()
    assert answer.startswith(b"HTTP/1.1 " + status + b" ")


async def test_a_failed_handler_closes_only_its_connection(h1_url, caplog) -> None:
    async def handler(exchange) -> None:
        if exchange.request.target == b"/fail":
            raise RuntimeError("handler failed")
        await echo(exchange)

    async with AioHttpServer(h1_url(), h1=handler) as server, AioH1Client(h1_url(server.address[1])) as client:
        async with await client.request(b"GET", b"/fail", (HOST,), end_stream=True) as exchange:
            with pytest.raises(H1ConnectionError):
                await exchange.receive_response()
        async with await client.request(b"GET", b"/ok", (HOST,), end_stream=True) as exchange:
            assert (await exchange.receive_response()).status == 200
    assert "handler failed" in caplog.text


async def test_a_response_before_the_whole_request_closes_the_connection_after_it(h1_url) -> None:
    async def handler(exchange) -> None:
        await exchange.send_response(413, ((b"content-length", b"0"),), end_stream=True)

    async with AioHttpServer(h1_url(), h1=handler) as server:
        reader, writer = await raw(server.address[1], b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 1000\r\n\r\npart")
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()
    assert answer.startswith(b"HTTP/1.1 413 ")
    assert b"connection: close" in answer.lower()


async def test_a_connection_option_that_only_contains_close_still_gets_close(h1_url) -> None:
    async def handler(exchange) -> None:
        headers = ((b"content-length", b"0"), (b"connection", b"x-close-reason"))
        await exchange.send_response(413, headers, end_stream=True)

    async with AioHttpServer(h1_url(), h1=handler) as server:
        reader, writer = await raw(server.address[1], b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 1000\r\n\r\npart")
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()
    assert b"connection: x-close-reason, close" in answer.lower()


async def test_ending_the_response_to_connect_closes_the_tunnel_at_once(h1_url) -> None:
    answered = anyio.Event()

    async def handler(exchange) -> None:
        await exchange.send_response(200, end_stream=True)
        answered.set()

    async with AioHttpServer(h1_url(), h1=handler) as server:
        reader, writer = await raw(
            server.address[1], b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
        )
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()
    assert answer.startswith(b"HTTP/1.1 200 ")
    assert answered.is_set()


async def test_a_client_that_goes_away_cancels_its_handler(h1_url) -> None:
    started = anyio.Event()
    cancelled = anyio.Event()

    async def handler(exchange) -> None:
        started.set()
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await anyio.sleep_forever()

    async with AioHttpServer(h1_url(), h1=handler) as server:
        _, writer = await raw(server.address[1], b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        with anyio.fail_after(5):
            await started.wait()
            writer.close()
            await cancelled.wait()


async def test_an_idle_connection_closes_after_its_timeout(h1_url) -> None:
    async with AioHttpServer(h1_url(idle_timeout="100"), h1=echo) as server:
        reader, writer = await raw(server.address[1], b"GET / HTTP/1.1\r\nHost: a\r\n\r\n")
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()
    # The response arrived, then the connection closed while it waited for the next request.
    assert answer.startswith(b"HTTP/1.1 200 ")


async def test_a_request_head_that_arrives_too_slowly_closes_the_connection(h1_url) -> None:
    async with AioHttpServer(h1_url(request_header_timeout="100"), h1=echo) as server:
        reader, writer = await raw(server.address[1], b"GET / HTTP/1.1\r\nHo")
        with anyio.fail_after(5):
            assert await reader.read() == b""
        writer.close()


async def test_a_request_body_that_arrives_too_slowly_is_answered_408_and_closes_the_connection(h1_url) -> None:
    failures = []

    async def handler(exchange) -> None:
        try:
            await read_body(exchange)
        except H1ConnectionError as error:
            failures.append(error.status)
            raise

    async with AioHttpServer(h1_url(request_body_timeout="100"), h1=handler) as server:
        reader, writer = await raw(server.address[1], b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 9\r\n\r\nab")
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()

    assert answer.startswith(b"HTTP/1.1 408 ")
    assert failures == [408]


async def test_a_request_body_that_breaks_the_protocol_is_answered_400_and_closes_the_connection(h1_url) -> None:
    async def handler(exchange) -> None:
        with pytest.raises(H1ConnectionError) as raised:
            await read_body(exchange)
        assert raised.value.status == 400
        raise raised.value

    async with AioHttpServer(h1_url(), h1=handler) as server:
        request = b"POST / HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\nab\r\n"
        reader, writer = await raw(server.address[1], request)
        with anyio.fail_after(5):
            answer = await reader.read()
        writer.close()

    assert answer.startswith(b"HTTP/1.1 400 ")


async def test_an_upgrade_hands_the_connection_to_a_tunnel_both_sides(h1_url) -> None:
    async def handler(exchange) -> None:
        tunnel = await exchange.switch_protocols(((b"upgrade", b"echo"), (b"connection", b"upgrade")))
        with pytest.raises(H1ExchangeError, match="already taken"):
            await exchange.tunnel()
        async with tunnel:
            while data := await tunnel.receive():
                await tunnel.send(data.upper())

    async with AioHttpServer(h1_url(), h1=handler) as server, AioH1Client(h1_url(server.address[1])) as client:
        headers = (HOST, (b"connection", b"upgrade"), (b"upgrade", b"echo"))
        async with await client.request(b"GET", b"/", headers, end_stream=True) as exchange:
            response = await exchange.receive_response()
            assert response.status == 101
            async with await exchange.tunnel() as tunnel:
                for message in (b"ping", b"pong"):
                    await tunnel.send(message)
                    assert await tunnel.receive() == message.upper()
        # The tunnel took its connection out of the pool.
        assert not client._idle


async def test_bytes_sent_right_after_an_upgrade_request_reach_the_tunnel_first(h1_url) -> None:
    async def handler(exchange) -> None:
        async with await exchange.switch_protocols(((b"upgrade", b"echo"), (b"connection", b"upgrade"))) as tunnel:
            assert await tunnel.receive(5) == b"early"
            await tunnel.send(b"seen")

    async with AioHttpServer(h1_url(), h1=handler) as server:
        request = b"GET / HTTP/1.1\r\nHost: a\r\nConnection: upgrade\r\nUpgrade: echo\r\n\r\nearly"
        reader, writer = await raw(server.address[1], request)
        with anyio.fail_after(5):
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 101 ")
            assert await reader.read() == b"seen"
        writer.close()


async def test_a_request_without_upgrade_cannot_switch_protocols(h1_url) -> None:
    async def handler(exchange) -> None:
        with pytest.raises(H1ExchangeError, match="no upgrade"):
            await exchange.switch_protocols(((b"upgrade", b"echo"),))
        await exchange.send_response(204, end_stream=True)

    async with AioHttpServer(h1_url(), h1=handler) as server, httpx.AsyncClient() as client:
        assert (await client.get(f"http://127.0.0.1:{server.address[1]}/")).status_code == 204


async def test_a_connect_tunnel_half_closes(h1_url) -> None:
    async def handler(exchange) -> None:
        assert exchange.request.method == b"CONNECT"
        assert exchange.request.target == b"example.com:443"
        await exchange.send_response(200)
        async with await exchange.tunnel() as tunnel:
            received = bytearray()
            while data := await tunnel.receive():
                received += data
            # The client finished sending; this side still answers.
            await tunnel.send(b"got " + bytes(received))
            await tunnel.send_eof()

    async with AioHttpServer(h1_url(), h1=handler) as server, AioH1Client(h1_url(server.address[1])) as client:
        headers = ((b"host", b"example.com:443"),)
        async with await client.request(b"CONNECT", b"example.com:443", headers, end_stream=True) as exchange:
            assert (await exchange.receive_response()).status == 200
            async with await exchange.tunnel() as tunnel:
                await tunnel.send(b"secret")
                await tunnel.send_eof()
                with pytest.raises(H1ConnectionError, match="finished sending"):
                    await tunnel.send(b"more")
                received = bytearray()
                with anyio.fail_after(5):
                    while data := await tunnel.receive():
                        received += data
                assert received == b"got secret"


# Windows refuses the connection after its own retries, in real time.
@pytest.mark.real_clock
async def test_closing_the_server_finishes_handlers_and_closes_idle_connections(h1_url) -> None:
    started = anyio.Event()
    release = anyio.Event()

    async def handler(exchange) -> None:
        if exchange.request.target == b"/slow":
            started.set()
            await release.wait()
        await exchange.send_response(200, ((b"content-length", b"0"),), end_stream=True)

    server = AioHttpServer(h1_url(), h1=handler)
    async with server:
        port = server.address[1]
        idle_reader, idle_writer = await raw(port, b"GET /quick HTTP/1.1\r\nHost: a\r\n\r\n")
        await idle_reader.readuntil(b"\r\n\r\n")
        busy_reader, busy_writer = await raw(port, b"GET /slow HTTP/1.1\r\nHost: a\r\n\r\n")
        await started.wait()
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(server.aclose)
            with anyio.fail_after(5):
                # The idle connection closes at once.
                assert await idle_reader.read() == b""
            release.set()
            with anyio.fail_after(5):
                answer = await busy_reader.read()
        assert answer.startswith(b"HTTP/1.1 200 ")
        assert b"connection: close" in answer.lower()
        idle_writer.close()
        busy_writer.close()
        with pytest.raises(ConnectionRefusedError):
            await raw(port, b"")


async def test_the_pool_opens_at_most_its_connections_and_serves_every_waiter(h1_url) -> None:
    peers = set()
    running = 0
    most = 0

    async def handler(exchange) -> None:
        nonlocal running, most
        peers.add(exchange.peer)
        running += 1
        most = max(most, running)
        await anyio.sleep(0.02)
        running -= 1
        await echo(exchange)

    async with (
        AioHttpServer(h1_url(), h1=handler) as server,
        AioH1Client(h1_url(server.address[1], max_connections="2")) as client,
    ):

        async def call(index: int) -> None:
            async with await client.request(b"GET", b"/%d" % index, (HOST,), end_stream=True) as exchange:
                await exchange.receive_response()
                assert await read_body(exchange) == b"GET /%d " % index

        async with anyio.create_task_group() as task_group:
            for index in range(10):
                task_group.start_soon(call, index)
    assert most == 2
    assert len(peers) == 2


async def test_an_idle_pooled_connection_closes_after_its_timeout(h1_url) -> None:
    peers = []

    async def handler(exchange) -> None:
        peers.append(exchange.peer)
        await echo(exchange)

    async with (
        AioHttpServer(h1_url(), h1=handler) as server,
        AioH1Client(h1_url(server.address[1], idle_timeout="50")) as client,
    ):
        for _ in range(2):
            async with await client.request(b"GET", b"/", (HOST,), end_stream=True) as exchange:
                await exchange.receive_response()
                await read_body(exchange)
            await anyio.sleep(0.2)
            assert not client._idle
    assert len(set(peers)) == 2


async def test_an_unfinished_exchange_closes_its_connection(h1_url) -> None:
    async with AioHttpServer(h1_url(), h1=echo) as server, AioH1Client(h1_url(server.address[1])) as client:
        async with await client.request(b"GET", b"/", (HOST,), end_stream=True) as exchange:
            await exchange.receive_response()
        assert not client._idle
        with anyio.fail_after(5):
            while client._connections:
                await anyio.sleep(0)


async def test_a_closed_port_fails_to_connect(h1_url) -> None:
    async with AioHttpServer(h1_url(), h1=echo) as server:
        port = server.address[1]
    async with AioH1Client(h1_url(port)) as client:
        with pytest.raises(H1ConnectionError, match="failed to connect"):
            await client.request(b"GET", b"/", (HOST,), end_stream=True)


async def test_closing_the_client_ends_a_connection_attempt_under_way(h1_url) -> None:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # The TLS handshake never gets an answer.
        await reader.read()
        writer.close()

    certificate = Path(__file__).resolve().parents[5] / "certificates" / "localhost-cert.pem"
    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    async with server, anyio.create_task_group() as task_group:
        port = server.sockets[0].getsockname()[1]
        client = AioH1Client(h1_url(port, ssl_enabled="true", ca_cert_file=str(certificate), connect_timeout="60000"))
        attempted = anyio.Event()

        async def request() -> None:
            attempted.set()
            with pytest.raises(RuntimeError, match="closed"):
                await client.request(b"GET", b"/", (HOST,), end_stream=True)

        task_group.start_soon(request)
        await attempted.wait()
        await anyio.sleep(0.1)

        with anyio.fail_after(1):
            await client.aclose()


async def test_an_invalid_request_is_refused_before_it_is_sent(h1_url) -> None:
    async with AioHttpServer(h1_url(), h1=echo) as server, AioH1Client(h1_url(server.address[1])) as client:
        with pytest.raises(H1ExchangeError, match="Host"):
            await client.request(b"GET", b"/", (), end_stream=True)


async def test_body_needs_the_final_response_and_framing(h1_url) -> None:
    async def handler(exchange) -> None:
        with pytest.raises(H1ExchangeError, match="final response was not sent"):
            await exchange.send_data(b"early")
        await exchange.send_response(200, ((b"content-length", b"2"),))
        with pytest.raises(H1ExchangeError, match="Content-Length"):
            await exchange.send_data(b"too long")
        await exchange.send_data(b"ok", end_stream=True)

    async with AioHttpServer(h1_url(), h1=handler) as server, httpx.AsyncClient() as client:
        assert (await client.get(f"http://127.0.0.1:{server.address[1]}/")).content == b"ok"
