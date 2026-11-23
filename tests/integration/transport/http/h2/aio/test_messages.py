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

import anyio
import pytest

from dubbo.transport.http.aio import AioHttpServer
from dubbo.transport.http.h2 import H2SensitiveHeader, H2StreamError, H2UsageError
from dubbo.transport.http.h2.aio import AioH2Client

pytestmark = pytest.mark.anyio

_CONNECT_HEADERS = (
    (b":method", b"CONNECT"),
    (b":protocol", b"websocket"),
    (b":scheme", b"http"),
    (b":authority", b"127.0.0.1"),
    (b":path", b"/chat"),
)


async def read_body(stream) -> bytes:
    body = bytearray()
    while chunk := await stream.receive_data():
        body += chunk
    return bytes(body)


async def test_interim_responses_arrive_in_order_before_the_final_one(request_headers, response_headers, h2_url):
    hints = ((b":status", b"103"), (b"link", b"</style.css>; rel=preload"))

    async def handler(stream):
        await stream.send_informational(((b":status", b"100"),))
        await stream.send_informational(hints)
        await stream.send_response(response_headers)
        await stream.send_data(b"body", end_stream=True)
        with pytest.raises(H2UsageError, match="already sent"):
            await stream.send_informational(((b":status", b"100"),))

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with (
            AioH2Client(h2_url(host, port)) as client,
            await client.open_stream(request_headers, end_stream=True) as stream,
        ):
            with pytest.raises(H2UsageError, match="final response was not received"):
                await stream.receive_data()
            assert await stream.receive_response() == ((b":status", b"100"),)
            assert await stream.receive_response() == hints
            assert await stream.receive_response() == response_headers
            assert await read_body(stream) == b"body"
            with pytest.raises(H2UsageError, match="already received"):
                await stream.receive_response()


async def test_a_client_gives_up_on_a_server_that_floods_it_with_interim_responses(request_headers, h2_url) -> None:
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
        AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server,
        AioH2Client(h2_url(*server.address)) as client,
        await client.open_stream(request_headers, end_stream=True) as stream,
    ):
        with anyio.fail_after(5):
            await reset.wait()

        for _ in range(5):
            assert await stream.receive_response() == ((b":status", b"103"),)
        with pytest.raises(H2StreamError, match="too many interim responses"):
            await stream.receive_response()


async def test_a_server_cannot_send_body_before_its_response(request_headers, response_headers, h2_url):
    async def handler(stream):
        with pytest.raises(H2UsageError, match="was not sent"):
            await stream.send_data(b"early")
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with (
            AioH2Client(h2_url(host, port)) as client,
            await client.open_stream(request_headers, end_stream=True) as stream,
        ):
            assert await stream.receive_response() == response_headers


async def test_an_extended_connect_opens_a_tunnel_when_the_server_enables_it(response_headers, h2_url):
    async def handler(stream):
        assert (b":protocol", b"websocket") in stream.request_headers
        await stream.send_response(response_headers)
        while chunk := await stream.receive_data():
            await stream.send_data(chunk)
        await stream.end()

    async with AioHttpServer(h2_url("127.0.0.1", 0, enable_connect_protocol="true"), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client, await client.open_stream(_CONNECT_HEADERS) as stream:
            assert stream.remote_settings.enable_connect_protocol
            assert await stream.receive_response() == response_headers
            for message in (b"ping", b"pong"):
                await stream.send_data(message)
                assert await stream.receive_data_exactly(4) == message
            await stream.end()
            assert await stream.receive_data() == b""


async def test_an_extended_connect_fails_when_the_server_does_not_enable_it(response_headers, h2_url):
    async def handler(stream):
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            with pytest.raises(H2StreamError, match="extended CONNECT") as raised:
                await client.open_stream(_CONNECT_HEADERS)
            assert raised.value.stream_id is None


async def test_the_peer_settings_in_force_are_visible(request_headers, response_headers, h2_url):
    async def handler(stream):
        assert not stream.remote_settings.enable_push
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(h2_url("127.0.0.1", 0, max_concurrent_streams="7"), h2=handler) as server:
        host, port = server.address
        async with (
            AioH2Client(h2_url(host, port)) as client,
            await client.open_stream(request_headers, end_stream=True) as stream,
        ):
            assert await stream.receive_response() == response_headers
            settings = stream.remote_settings
            assert settings.max_concurrent_streams == 7
            assert not settings.enable_connect_protocol


async def test_a_sensitive_field_arrives_as_an_ordinary_one(request_headers, response_headers, h2_url):
    received = []

    async def handler(stream):
        received.extend(stream.request_headers)
        await stream.send_response((*response_headers, H2SensitiveHeader(b"set-cookie", b"id=1")), end_stream=True)

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        secret = H2SensitiveHeader(b"authorization", b"Bearer secret")
        async with (
            AioH2Client(h2_url(host, port)) as client,
            await client.open_stream((*request_headers, secret), end_stream=True) as stream,
        ):
            assert await stream.receive_response() == (*response_headers, (b"set-cookie", b"id=1"))
    assert (b"authorization", b"Bearer secret") in received
