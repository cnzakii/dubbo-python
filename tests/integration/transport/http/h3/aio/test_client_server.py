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

import gc
import logging
import socket
from contextlib import ExitStack

import anyio
import pytest

from dubbo.transport.http.aio import AioHttpServer
from dubbo.transport.http.h3 import (
    H3ConnectionError,
    H3ErrorCode,
    H3Priority,
    H3SensitiveHeader,
    H3StreamError,
    H3StreamResetError,
    H3UsageError,
)
from dubbo.transport.http.h3.aio import AioH3Client

pytestmark = pytest.mark.anyio


async def echo(stream) -> None:
    """Answer with the request body, then trailers that repeat the request's."""
    body = bytearray()
    while chunk := await stream.receive_data():
        body += chunk
    trailers = await stream.receive_trailers()
    await stream.send_response(((b":status", b"200"),))
    await stream.send_data(bytes(body))
    await stream.send_trailers(((b"grpc-status", b"0"), *(trailers or ())))


async def keep_sending(stream) -> None:
    while True:
        await stream.send_data(b"more")
        await anyio.sleep(0.01)


async def read_body(stream) -> bytes:
    body = bytearray()
    while chunk := await stream.receive_data():
        body += chunk
    return bytes(body)


async def test_a_request_and_its_response_carry_headers_body_and_trailers(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client:
            async with await client.open_stream(request_headers) as stream:
                await stream.send_data(b"hello")
                await stream.send_trailers(((b"x-request", b"1"),))

                assert await stream.receive_response() == ((b":status", b"200"),)
                assert await read_body(stream) == b"hello"
                assert await stream.receive_trailers() == ((b"grpc-status", b"0"), (b"x-request", b"1"))
                assert stream.peer is not None
            assert client.available


async def test_bodies_larger_than_every_window_and_send_buffer_arrive_whole(
    request_headers, server_url, client_url
) -> None:
    small = {
        "initial_window_size": "16384",
        "connection_initial_window_size": "65536",
        "stream_send_buffer_size": "16384",
    }
    payload = bytes(range(256)) * 4096  # 1 MiB

    async with AioHttpServer(server_url(**small), h3=echo) as server:
        port = server.address[1]
        async with (
            AioH3Client(client_url(port, **small)) as client,
            await client.open_stream(request_headers) as stream,
        ):

            async def upload() -> None:
                for offset in range(0, len(payload), 50_000):
                    await stream.send_data(payload[offset : offset + 50_000])
                await stream.end()

            async with anyio.create_task_group() as task_group:
                task_group.start_soon(upload)
                await stream.receive_response()
                assert await read_body(stream) == payload


async def test_streams_beyond_the_server_limit_wait_for_room_and_all_complete(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(max_concurrent_streams="3"), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client:
            results: list[bytes] = []

            async def call(index: int) -> None:
                async with await client.open_stream(request_headers) as stream:
                    await stream.send_data(str(index).encode(), end_stream=True)
                    await stream.receive_response()
                    results.append(await read_body(stream))

            async with anyio.create_task_group() as task_group:
                for index in range(20):
                    task_group.start_soon(call, index)

            assert sorted(results) == sorted(str(index).encode() for index in range(20))


async def test_a_handler_is_cancelled_when_the_client_gives_up_after_its_whole_request(
    request_headers, server_url, client_url
) -> None:
    started = anyio.Event()
    cancelled = anyio.Event()

    async def handler(stream) -> None:
        assert await read_body(stream) == b"request"
        started.set()
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await anyio.sleep_forever()

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client:
            stream = await client.open_stream(request_headers)
            async with stream:
                await stream.send_data(b"request", end_stream=True)
                await started.wait()
                # Once the server acknowledges the whole request, giving up sends only STOP_SENDING.
                await anyio.sleep(0.1)
            # Leaving the context before the response aborts the call.
            with anyio.fail_after(5):
                await cancelled.wait()


async def test_receiving_the_trailers_discards_a_body_larger_than_the_window(
    request_headers, response_headers, server_url, client_url
) -> None:
    async def handler(stream) -> None:
        trailers = await stream.receive_trailers()
        await stream.send_response(response_headers)
        await stream.send_trailers(trailers)

    async with (
        AioHttpServer(server_url(initial_window_size="65536"), h3=handler) as server,
        AioH3Client(client_url(server.address[1])) as client,
    ):
        stream = await client.open_stream(request_headers)
        with anyio.fail_after(5):
            await stream.send_data(b"x" * (4 * 65536))
            await stream.send_trailers(((b"checksum", b"1"),))
            assert await stream.receive_response() == response_headers
            assert await stream.receive_trailers() == ((b"checksum", b"1"),)


async def test_a_failed_handler_aborts_only_its_stream(response_headers, server_url, client_url, path_headers) -> None:
    async def handler(stream) -> None:
        headers = stream.request_headers
        if (b":path", b"/fail") in headers:
            raise RuntimeError("handler failed")
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client:
            failed = await client.open_stream(path_headers(b"/fail"), end_stream=True)
            with pytest.raises(H3StreamResetError) as raised:
                await failed.receive_response()
            assert raised.value.error_code == H3ErrorCode.H3_INTERNAL_ERROR

            healthy = await client.open_stream(path_headers(b"/ok"), end_stream=True)
            assert await healthy.receive_response() == response_headers


async def test_a_server_that_answers_early_stops_the_rest_of_the_request(
    request_headers, response_headers, server_url, client_url
) -> None:
    async def handler(stream) -> None:
        await stream.send_response(response_headers)
        await stream.send_data(b"done", end_stream=True)

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client, await client.open_stream(request_headers) as stream:
            await stream.send_data(b"first part")
            # The whole response stays readable after the server stops the request.
            assert await stream.receive_response() == response_headers
            assert await read_body(stream) == b"done"
            with anyio.fail_after(5), pytest.raises(H3StreamResetError) as raised:
                await keep_sending(stream)
            assert raised.value.error_code == H3ErrorCode.H3_NO_ERROR


async def test_closing_the_server_finishes_running_handlers_and_refuses_new_requests(
    request_headers, response_headers, server_url, client_url
) -> None:
    release = anyio.Event()
    started = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        await release.wait()
        await stream.send_response(response_headers, end_stream=True)

    server = AioHttpServer(server_url(), h3=handler)
    async with server, AioH3Client(client_url(server.address[1])) as client:
        stream = await client.open_stream(request_headers, end_stream=True)
        await started.wait()
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(server.aclose)
            await anyio.sleep(0.05)
            release.set()
            assert await stream.receive_response() == response_headers

        with pytest.raises(RuntimeError, match="not listening"):
            _ = server.address
        with pytest.raises(H3ConnectionError):
            await client.open_stream(request_headers, end_stream=True)


async def test_a_connection_closed_on_its_idle_timeout_is_replaced_for_the_next_stream(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port, idle_timeout="200")) as client:
            for _ in range(2):
                async with await client.open_stream(request_headers, end_stream=True) as stream:
                    # The first request stream of a new connection.
                    assert stream.stream_id == 0
                    await stream.receive_response()
                    await read_body(stream)
                await anyio.sleep(1)
                # A connection that went quiet is not a failure, but the next stream waits for a handshake.
                assert (client.available, client.connected) == (True, False)


async def test_connect_establishes_a_connection_without_a_stream_and_returns_at_once_while_connected(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port, idle_timeout="200")) as client:
            await client.connect()
            assert client.connected
            connection = client._active_connection
            await client.connect()
            assert client._active_connection is connection

            await anyio.sleep(1)
            await client.connect()
            assert client.connected
            assert client._active_connection is not connection
            async with await client.open_stream(request_headers, end_stream=True) as stream:
                # The connection carried no stream before.
                assert stream.stream_id == 0

    with pytest.raises(RuntimeError, match="closed"):
        await client.connect()


async def test_a_path_that_stops_carrying_data_is_left_for_a_new_connection_after_a_backoff(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client:
            async with await client.open_stream(request_headers, end_stream=True) as stream:
                await stream.receive_response()
                await read_body(stream)
            connection = client._active_connection
            # Nothing reaches the client on that path any more, acknowledgments included.
            assert server._h3_connections is not None
            (served,) = server._h3_connections.connections
            endpoint, path = served.endpoint, served.engine.remote_addr
            send = endpoint.send
            endpoint.send = lambda transmit: None if transmit.remote_addr == path else send(transmit)

            async with await client.open_stream(request_headers) as stalled:
                await stalled.send_data(b"x")
                with anyio.fail_after(5):
                    while client.available:
                        await anyio.sleep(0.01)
                # Its streams stay open, should the path recover.
                assert not connection.closed

                with anyio.fail_after(5):
                    while not client.available:
                        await anyio.sleep(0.05)
                assert client._active_connection is not connection
                async with await client.open_stream(request_headers, end_stream=True) as stream:
                    await stream.receive_response()


async def test_a_closed_port_fails_the_attempt_at_once(request_headers, client_url) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    client = AioH3Client(client_url(port, host="127.0.0.1", connect_timeout="60000"))
    async with client:
        with anyio.fail_after(5), pytest.raises(H3ConnectionError):
            await client.open_stream(request_headers, end_stream=True)
        assert not client.available
        with anyio.fail_after(5), pytest.raises(H3ConnectionError):
            await client.connect()
        assert not client.connected


async def test_entering_connects_in_the_background_and_a_failed_handshake_leaves_it_unavailable(
    server_url, client_url, request_headers
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        url = client_url(server.address[1])
        async with AioH3Client(url) as client:
            with anyio.fail_after(5):
                while not client.available:
                    await anyio.sleep(0.01)

        untrusting = url.replace(
            parameters={key: value for key, value in url.parameters.items() if key != "ca-cert-file"}
        )
        async with AioH3Client(untrusting) as client:
            with anyio.fail_after(5), pytest.raises(H3ConnectionError):
                await client.open_stream(request_headers, end_stream=True)
            assert not client.available


async def test_an_attempt_whose_engine_fails_to_start_releases_its_socket(
    monkeypatch, request_headers, client_url
) -> None:
    from dubbo.transport.http.h3.aio.endpoint import AioQuicEndpoint

    endpoints: list[AioQuicEndpoint] = []

    def refuse(endpoint: AioQuicEndpoint, settings: object) -> None:
        endpoints.append(endpoint)
        raise RuntimeError("the engine refused the settings")

    monkeypatch.setattr(AioQuicEndpoint, "connect", refuse)
    async with AioH3Client(client_url(443, host="127.0.0.1")) as client:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="refused"):
                await client.open_stream(request_headers, end_stream=True)
        await anyio.sleep(0)

        assert len(endpoints) == 2
        assert all(endpoint.closed for endpoint in endpoints)


async def test_a_name_that_resolves_to_a_closed_address_first_falls_back_to_the_next(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        port = server.address[1]
        # localhost names ::1 before 127.0.0.1 on the systems that resolve both, and the server binds only 127.0.0.1.
        async with (
            AioH3Client(client_url(port, host="localhost")) as client,
            await client.open_stream(request_headers, end_stream=True) as stream,
        ):
            assert await stream.receive_response() == ((b":status", b"200"),)


async def test_a_client_that_does_not_trust_the_certificate_fails_to_connect(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        url = client_url(server.address[1])
        url = url.__class__(
            protocol=url.protocol,
            host=url.host,
            port=url.port,
            parameters={key: value for key, value in url.parameters.items() if key != "ca-cert-file"},
        )
        async with AioH3Client(url) as client:
            with anyio.fail_after(5), pytest.raises(H3ConnectionError):
                await client.open_stream(request_headers, end_stream=True)


async def test_invalid_request_headers_fail_before_a_stream_is_allocated(
    request_headers, server_url, client_url
) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client:
            with pytest.raises(H3StreamError) as raised:
                await client.open_stream((*request_headers, (b"bad name", b"x")), end_stream=True)
            assert raised.value.stream_id is None
            assert "bad name" in str(raised.value)


async def test_cancelling_a_client_close_aborts_its_connections_at_once(
    request_headers, server_url, client_url
) -> None:
    async def handler(stream) -> None:
        await anyio.sleep_forever()

    async with AioHttpServer(server_url(), h3=handler) as server:
        client = AioH3Client(client_url(server.address[1]))
        async with client:
            stream = await client.open_stream(request_headers, end_stream=True)
            with anyio.move_on_after(0.1):
                # Waits for the open stream, until the cancellation aborts the connection.
                await client.aclose()
            with pytest.raises(H3ConnectionError):
                await stream.receive_response()


async def test_client_closed_while_connecting_releases_its_sockets(server_url, client_url) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        # The attempt is cancelled at each step it can reach before the close.
        for steps in range(20):
            client = await AioH3Client(client_url(server.address[1])).__aenter__()
            for _ in range(steps % 5):
                await anyio.sleep(0)
            await client.aclose()

    # A socket left open warns as it is collected, which fails the test.
    gc.collect()


async def test_a_sender_waits_while_its_peer_reads_nothing(request_headers, server_url, client_url) -> None:
    received = anyio.Event()

    async def handler(stream) -> None:
        received.set()
        await anyio.sleep_forever()

    small = {"initial_window_size": "16384", "stream_send_buffer_size": "16384"}
    async with AioHttpServer(server_url(**small), h3=handler) as server:
        port = server.address[1]
        async with (
            AioH3Client(client_url(port, **small)) as client,
            await client.open_stream(request_headers) as stream,
        ):
            await received.wait()
            sent = 0
            with anyio.move_on_after(1) as waiting:
                while sent < 4 * 1024 * 1024:
                    await stream.send_data(b"x" * 8192)
                    sent += 8192
            assert waiting.cancelled_caught
            # The peer's window and this side's send buffer bound what left the sender.
            assert sent <= 64 * 1024


async def test_interim_responses_arrive_in_order_before_the_final_one(
    request_headers, response_headers, server_url, client_url
) -> None:
    hints = ((b":status", b"103"), (b"link", b"</style.css>; rel=preload"))

    async def handler(stream) -> None:
        await stream.send_informational(((b":status", b"100"),))
        await stream.send_informational(hints)
        await stream.send_response(response_headers)
        await stream.send_data(b"body", end_stream=True)
        with pytest.raises(H3UsageError, match="already sent"):
            await stream.send_informational(((b":status", b"100"),))

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        async with (
            AioH3Client(client_url(port)) as client,
            await client.open_stream(request_headers, end_stream=True) as stream,
        ):
            with pytest.raises(H3UsageError, match="final response was not received"):
                await stream.receive_data()
            assert await stream.receive_response() == ((b":status", b"100"),)
            assert await stream.receive_response() == hints
            assert await stream.receive_response() == response_headers
            assert await read_body(stream) == b"body"
            with pytest.raises(H3UsageError, match="already received"):
                await stream.receive_response()


async def test_a_client_gives_up_on_a_server_that_floods_it_with_interim_responses(
    request_headers, server_url, client_url
) -> None:
    reset = anyio.Event()

    async def handler(stream) -> None:
        for _ in range(6):
            await stream.send_informational(((b":status", b"103"),))
        # The client resets the stream once the sixth finds its queue full, which cancels the handler.
        try:
            await anyio.sleep_forever()
        finally:
            reset.set()

    async with (
        AioHttpServer(server_url(), h3=handler) as server,
        AioH3Client(client_url(server.address[1])) as client,
        await client.open_stream(request_headers, end_stream=True) as stream,
    ):
        with anyio.fail_after(5):
            await reset.wait()

        for _ in range(5):
            assert await stream.receive_response() == ((b":status", b"103"),)
        with pytest.raises(H3StreamError, match="too many interim responses"):
            await stream.receive_response()


async def test_a_server_may_stop_reading_a_request_and_still_answer(
    request_headers, response_headers, server_url, client_url
) -> None:
    stopped = anyio.Event()
    reset = anyio.Event()

    async def handler(stream) -> None:
        await stream.stop_sending(H3ErrorCode.H3_REQUEST_REJECTED)
        with pytest.raises(H3StreamError):
            await stream.receive_data()
        stopped.set()
        # The client's RESET_STREAM, which STOP_SENDING asks for, arrives before the response.
        await reset.wait()
        await anyio.sleep(0.2)
        await stream.send_response(response_headers)
        await stream.send_data(b"answer", end_stream=True)

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client, await client.open_stream(request_headers) as stream:
            await stopped.wait()
            with anyio.fail_after(5), pytest.raises(H3StreamResetError) as raised:
                await keep_sending(stream)
            reset.set()
            assert raised.value.error_code == H3ErrorCode.H3_REQUEST_REJECTED
            assert await stream.receive_response() == response_headers
            assert await read_body(stream) == b"answer"


async def test_a_client_that_stops_reading_the_response_cancels_the_handler(
    request_headers, response_headers, server_url, client_url
) -> None:
    cancelled = anyio.Event()

    async def handler(stream) -> None:
        await stream.send_response(response_headers)
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await keep_sending(stream)

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        async with (
            AioH3Client(client_url(port)) as client,
            await client.open_stream(request_headers, end_stream=True) as stream,
        ):
            await stream.receive_response()
            await stream.stop_sending(H3ErrorCode.H3_REQUEST_CANCELLED)
            with pytest.raises(H3StreamError):
                await stream.receive_data()
            with anyio.fail_after(5):
                await cancelled.wait()


_CONNECT_HEADERS = (
    (b":method", b"CONNECT"),
    (b":protocol", b"websocket"),
    (b":scheme", b"https"),
    (b":authority", b"localhost"),
    (b":path", b"/chat"),
)


async def test_an_extended_connect_opens_a_tunnel_when_the_server_enables_it(
    response_headers, server_url, client_url
) -> None:
    async def handler(stream) -> None:
        assert (b":protocol", b"websocket") in stream.request_headers
        await stream.send_response(response_headers)
        while chunk := await stream.receive_data():
            await stream.send_data(chunk)
        await stream.end()

    async with AioHttpServer(server_url(enable_connect_protocol="true"), h3=handler) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client, await client.open_stream(_CONNECT_HEADERS) as stream:
            assert stream.remote_settings is not None
            assert stream.remote_settings.enable_connect_protocol
            assert await stream.receive_response() == response_headers
            for message in (b"ping", b"pong"):
                await stream.send_data(message)
                assert await stream.receive_data_exactly(4) == message
            await stream.end()
            assert await stream.receive_data() == b""


async def test_an_extended_connect_fails_when_the_server_does_not_enable_it(server_url, client_url) -> None:
    async with AioHttpServer(server_url(), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client:
            with pytest.raises(H3StreamError, match="extended CONNECT") as raised:
                await client.open_stream(_CONNECT_HEADERS)
            assert raised.value.stream_id is None


async def test_datagrams_travel_on_a_request_stream_when_both_sides_enable_them(
    request_headers, response_headers, server_url, client_url
) -> None:
    async def handler(stream) -> None:
        await stream.send_response(response_headers)
        while (datagram := await stream.receive_datagram()) is not None:
            stream.send_datagram(datagram.upper())
        await stream.end()

    async with AioHttpServer(server_url(h3_datagram="true"), h3=handler) as server:
        port = server.address[1]
        async with (
            AioH3Client(client_url(port, h3_datagram="true")) as client,
            await client.open_stream(request_headers) as stream,
        ):
            assert await stream.receive_response() == response_headers
            assert stream.remote_settings is not None
            assert stream.remote_settings.h3_datagram
            size = stream.max_datagram_size
            assert size is not None
            assert size > 1000
            with pytest.raises(H3StreamError):
                stream.send_datagram(b"x" * (size + 1))
            with anyio.fail_after(5):
                # Datagrams may be lost, so each is sent until its answer arrives.
                for message in (b"one", b"two"):
                    stream.send_datagram(message)
                    while True:
                        with anyio.move_on_after(0.2):
                            assert await stream.receive_datagram() == message.upper()
                            break
                        stream.send_datagram(message)
            await stream.end()
            assert await read_body(stream) == b""
            assert await stream.receive_datagram() is None


async def test_datagrams_need_both_sides_to_enable_them(request_headers, server_url, client_url) -> None:
    async with AioHttpServer(server_url(h3_datagram="true"), h3=echo) as server:
        port = server.address[1]
        async with AioH3Client(client_url(port)) as client, await client.open_stream(request_headers) as stream:
            assert stream.max_datagram_size is None
            with pytest.raises(H3StreamError):
                stream.send_datagram(b"x")


async def test_a_sensitive_field_arrives_as_an_ordinary_one(
    request_headers, response_headers, server_url, client_url
) -> None:
    received = []

    async def handler(stream) -> None:
        received.extend(stream.request_headers)
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        secret = H3SensitiveHeader(b"authorization", b"Bearer secret")
        async with (
            AioH3Client(client_url(port)) as client,
            await client.open_stream((*request_headers, secret), end_stream=True) as stream,
        ):
            assert await stream.receive_response() == response_headers
    assert (b"authorization", b"Bearer secret") in received


async def test_draining_a_client_waits_for_its_open_streams_however_long_they_take(
    request_headers, response_headers, server_url, client_url
) -> None:
    release = anyio.Event()

    async def handler(stream) -> None:
        await release.wait()
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(server_url(), h3=handler) as server:
        client = AioH3Client(client_url(server.address[1]))
        await client.__aenter__()
        stream = await client.open_stream(request_headers, end_stream=True)
        opened = []
        connect = client._dialer._connect
        client._dialer._connect = lambda: opened.append(True) or connect()
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(client.drain)
            await anyio.sleep(0)
            # A loss reported while draining connects no more.
            client._dialer.lost()
            await anyio.sleep(5)

            assert not client.available
            assert opened == []
            with pytest.raises(RuntimeError, match="closed"):
                await client.open_stream(request_headers, end_stream=True)
            release.set()
            assert await stream.receive_response() == response_headers
            await stream.__aexit__(None, None, None)


async def test_a_send_blocked_when_its_connection_fails_raises(request_headers, server_url, client_url) -> None:
    received = anyio.Event()

    async def handler(stream) -> None:
        received.set()
        await anyio.sleep_forever()

    small = {"initial_window_size": "16384", "stream_send_buffer_size": "16384"}
    async with (
        AioHttpServer(server_url(**small), h3=handler) as server,
        AioH3Client(client_url(server.address[1], **small)) as client,
    ):
        stream = await client.open_stream(request_headers)
        await received.wait()
        async with anyio.create_task_group() as task_group:

            async def send() -> None:
                with pytest.raises(H3ConnectionError):
                    await stream.send_data(b"x" * 200_000, end_stream=True)

            task_group.start_soon(send)
            await anyio.wait_all_tasks_blocked()
            for connection in client._owned():
                connection.abort()


@pytest.mark.parametrize(
    ("signal", "signalled"),
    [((), H3Priority()), (((b"priority", b"u=1, i"),), H3Priority(1, True))],
    ids=["default", "client-signal"],
)
async def test_a_server_schedules_a_response_by_the_clients_signal_until_it_sets_another(
    server_url, client_url, request_headers, signal, signalled
) -> None:
    seen = []

    async def handler(stream) -> None:
        seen.append(stream.priority)
        with pytest.raises(ValueError, match="urgency"):
            stream.set_priority(H3Priority(8))
        stream.set_priority(H3Priority(5, incremental=True))
        seen.append(stream.priority)
        await stream.send_response(((b":status", b"200"),), end_stream=True)

    async with AioHttpServer(server_url(), h3=handler) as server:
        port = server.address[1]
        async with (
            AioH3Client(client_url(port)) as client,
            await client.open_stream((*request_headers, *signal), end_stream=True) as stream,
        ):
            await stream.receive_response()

    assert seen == [signalled, H3Priority(5, incremental=True)]


async def test_a_handler_that_uses_its_stream_out_of_order_is_logged(
    request_headers, server_url, client_url, caplog
) -> None:
    async def handler(stream) -> None:
        await stream.send_data(b"before any response")

    async with (
        AioHttpServer(server_url(), h3=handler) as server,
        AioH3Client(client_url(server.address[1])) as client,
    ):
        stream = await client.open_stream(request_headers, end_stream=True)
        with anyio.fail_after(5), pytest.raises(H3StreamResetError):
            await stream.receive_response()

    [record] = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert record.getMessage() == "The HTTP/3 stream handler used its stream out of order"
    assert isinstance(record.exc_info[1], H3UsageError)


async def test_a_stream_its_handler_reset_fails_later_operations_without_a_warning(
    request_headers, server_url, client_url, caplog
) -> None:
    caplog.set_level(logging.DEBUG, "dubbo.transport.http.h3.aio.server")

    async def handler(stream) -> None:
        await stream.reset(H3ErrorCode.H3_REQUEST_CANCELLED)
        await stream.send_response(((b":status", b"200"),))

    async with (
        AioHttpServer(server_url(), h3=handler) as server,
        AioH3Client(client_url(server.address[1])) as client,
    ):
        stream = await client.open_stream(request_headers, end_stream=True)
        with anyio.fail_after(5), pytest.raises(H3StreamResetError):
            await stream.receive_response()

    [record] = [record for record in caplog.records if record.name == "dubbo.transport.http.h3.aio.server"]
    assert (record.levelno, record.getMessage()) == (
        logging.DEBUG,
        "The HTTP/3 stream handler ended on an error of its stream",
    )
    assert isinstance(record.exc_info[1], H3StreamError)
    assert not isinstance(record.exc_info[1], H3UsageError)
