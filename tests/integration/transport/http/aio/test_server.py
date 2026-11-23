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
import contextlib
import errno
import ssl
from pathlib import Path

import anyio
import pytest

from dubbo.extension import extensions
from dubbo.transport.http import HttpServer
from dubbo.transport.http.aio import AioHttpServer
from dubbo.transport.http.h1.aio import AioH1Client
from dubbo.transport.http.h2.aio import AioH2Client
from dubbo.url import URL

pytestmark = pytest.mark.anyio

_CERTIFICATES = Path(__file__).resolve().parents[4] / "certificates"
_SERVER_TLS = {
    "ssl-enabled": "true",
    "tls-cert-file": str(_CERTIFICATES / "localhost-cert.pem"),
    "tls-key-file": str(_CERTIFICATES / "localhost-key.pem"),
}
_CLIENT_TLS = {"ssl-enabled": "true", "ca-cert-file": str(_CERTIFICATES / "localhost-cert.pem")}
_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
_H1_REQUEST = b"GET / HTTP/1.1\r\nHost: a\r\n\r\n"
_H2_REQUEST = ((b":method", b"GET"), (b":scheme", b"http"), (b":authority", b"a"), (b":path", b"/"))


def url(port: int = 0, *, host: str = "127.0.0.1", tls: dict[str, str] | None = None, **parameters: str) -> URL:
    """Underscores in parameter names stand for hyphens."""
    return URL(
        protocol="tri",
        host=host,
        port=port,
        parameters=(tls or {}) | {name.replace("_", "-"): value for name, value in parameters.items()},
    )


async def h1_ok(exchange) -> None:
    await exchange.send_response(200, ((b"content-length", b"2"),))
    await exchange.send_data(b"h1", end_stream=True)


async def h2_ok(stream) -> None:
    await stream.send_response(((b":status", b"200"),))
    await stream.send_data(b"h2", end_stream=True)


async def h1_get(port: int, tls: dict[str, str] | None = None):
    async with (
        AioH1Client(url(port, host="localhost", tls=tls)) as client,
        await client.request(b"GET", b"/", ((b"host", b"a"),), end_stream=True) as exchange,
    ):
        response = await exchange.receive_response()
        body = await exchange.receive_data_exactly(2)
        return response, body


async def h2_get(port: int, tls: dict[str, str] | None = None):
    request = ((b":method", b"GET"), (b":scheme", b"https" if tls else b"http"), *_H2_REQUEST[2:])
    async with AioH2Client(url(port, host="localhost", tls=tls)) as client:
        stream = await client.open_stream(request, end_stream=True)
        async with stream:
            response = await stream.receive_response()
            body = await stream.receive_data_exactly(2)
            return response, body


async def raw(port: int, data: bytes, *, tls: ssl.SSLContext | None = None):
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port, ssl=tls, server_hostname="localhost" if tls else None
    )
    writer.write(data)
    await writer.drain()
    return reader, writer


def test_aio_is_the_default_server() -> None:
    assert extensions.get(HttpServer, None) is AioHttpServer


def test_a_server_needs_a_handler() -> None:
    with pytest.raises(ValueError, match="at least one HTTP version"):
        AioHttpServer(url())


async def test_http1_and_prior_knowledge_http2_share_a_cleartext_port() -> None:
    async with AioHttpServer(url(), h1=h1_ok, h2=h2_ok) as server:
        port = server.address[1]
        response, body = await h1_get(port)
        assert (response.status, body) == (200, b"h1")
        response, body = await h2_get(port)
        assert ((b":status", b"200") in response, body) == (True, b"h2")


async def test_alpn_chooses_the_version_over_tls() -> None:
    async with AioHttpServer(url(tls=_SERVER_TLS), h1=h1_ok, h2=h2_ok) as server:
        port = server.address[1]
        response, body = await h1_get(port, _CLIENT_TLS)
        assert (response.status, body) == (200, b"h1")
        response, body = await h2_get(port, _CLIENT_TLS)
        assert ((b":status", b"200") in response, body) == (True, b"h2")


@pytest.mark.parametrize(("data", "answer"), [(_PREFACE, b"HTTP/1.1 400 "), (_H1_REQUEST, b"HTTP/1.1 200 ")])
async def test_a_tls_connection_without_alpn_gets_http1_whatever_its_first_bytes(data, answer) -> None:
    context = ssl.create_default_context(cafile=_CERTIFICATES / "localhost-cert.pem")
    async with AioHttpServer(url(tls=_SERVER_TLS), h1=h1_ok, h2=h2_ok) as server:
        reader, writer = await raw(server.address[1], data, tls=context)
        assert writer.get_extra_info("ssl_object").selected_alpn_protocol() is None
        assert await reader.readexactly(len(answer)) == answer
        writer.close()


async def test_the_bytes_read_to_choose_reach_the_chosen_version() -> None:
    received = []

    async def handler(exchange) -> None:
        received.append(await exchange.receive_data_exactly(5))
        await h1_ok(exchange)

    async with AioHttpServer(url(), h1=handler, h2=h2_ok) as server:
        # "POST" shares its first byte with the preface; the request arrives a byte at a time.
        request = b"POST / HTTP/1.1\r\nHost: a\r\nContent-Length: 5\r\n\r\nhello"
        reader, writer = await raw(server.address[1], request[:1])
        for byte in request[1:]:
            await asyncio.sleep(0)
            writer.write(bytes((byte,)))
        assert (await reader.readuntil(b"h1")).startswith(b"HTTP/1.1 200 ")
        assert received == [b"hello"]
        writer.close()


async def test_a_peer_that_stops_partway_through_the_preface_is_closed_without_an_answer() -> None:
    async with AioHttpServer(url(), h1=h1_ok, h2=h2_ok) as server:
        reader, writer = await raw(server.address[1], _PREFACE[:10])
        writer.write_eof()
        assert await reader.read() == b""
        writer.close()


async def test_a_connection_slow_to_choose_closes_after_the_timeouts() -> None:
    async with AioHttpServer(url(idle_timeout="1000", request_header_timeout="100"), h1=h1_ok, h2=h2_ok) as server:
        port = server.address[1]
        silent_reader, silent_writer = await raw(port, b"")
        slow_reader, slow_writer = await raw(port, _PREFACE[:10])
        with anyio.fail_after(0.5):
            # The preface has begun, so it gets the time of a request head.
            assert await slow_reader.read() == b""
        assert not silent_reader.at_eof()
        with anyio.fail_after(1):
            assert await silent_reader.read() == b""
        silent_writer.close()
        slow_writer.close()


async def test_closing_the_server_closes_the_connections_still_choosing() -> None:
    server = AioHttpServer(url(), h1=h1_ok, h2=h2_ok)
    async with server:
        reader, writer = await raw(server.address[1], _PREFACE[:10])
        await asyncio.sleep(0.01)
        assert server._detecting
    with anyio.fail_after(1):
        assert await reader.read() == b""
    writer.close()


async def test_a_server_serving_only_http2_over_tls_still_requires_alpn() -> None:
    context = ssl.create_default_context(cafile=_CERTIFICATES / "localhost-cert.pem")
    async with AioHttpServer(url(tls=_SERVER_TLS), h2=h2_ok) as server:
        reader, writer = await raw(server.address[1], _PREFACE, tls=context)
        # Closing with the preface unread may reset the connection rather than end it.
        with anyio.fail_after(1), contextlib.suppress(ConnectionResetError):
            assert await reader.read() == b""
        writer.close()


async def test_an_http1_connection_made_after_a_shutdown_announcement_is_served() -> None:
    started = anyio.Event()
    release = anyio.Event()

    async def handler(exchange) -> None:
        if exchange.request.target == b"/slow":
            started.set()
            await release.wait()
        await h1_ok(exchange)

    async with AioHttpServer(url(), h1=handler) as server:
        reader, writer = await raw(server.address[1], b"GET /slow HTTP/1.1\r\nHost: a\r\n\r\n")
        await started.wait()
        server.announce_shutdown()
        # A connection made while the one it found still answers is served.
        response, body = await h1_get(server.address[1])
        assert (response.status, body) == (200, b"h1")
        release.set()
        answer = await reader.read()
        assert b"connection: close" in answer.lower()
        writer.close()


async def test_http3_shares_the_port_and_http1_and_http2_responses_announce_it() -> None:
    pytest.importorskip("ngquic")
    from dubbo.transport.http.h3.aio import AioH3Client

    async def h3_ok(stream) -> None:
        await stream.send_response(((b":status", b"200"),))
        await stream.send_data(b"h3", end_stream=True)

    async def h1_own(exchange) -> None:
        await exchange.send_response(200, ((b"content-length", b"0"), (b"Alt-Svc", b"clear")), end_stream=True)

    async with AioHttpServer(url(tls=_SERVER_TLS), h1=h1_ok, h2=h2_ok, h3=h3_ok) as server:
        port = server.address[1]
        alt_svc = (b"alt-svc", b'h3=":%d"' % port)

        h1_response, _ = await h1_get(port, _CLIENT_TLS)
        assert alt_svc in h1_response.headers
        h2_response, _ = await h2_get(port, _CLIENT_TLS)
        assert alt_svc in h2_response

        request = ((b":method", b"GET"), (b":scheme", b"https"), (b":authority", b"localhost"), (b":path", b"/"))
        async with AioH3Client(url(port, host="localhost", tls=_CLIENT_TLS)) as client:
            stream = await client.open_stream(request, end_stream=True)
            async with stream:
                response = await stream.receive_response()
                assert (b":status", b"200") in response
                assert all(name != b"alt-svc" for name, _ in response)
                assert await stream.receive_data_exactly(2) == b"h3"

    async with (
        AioHttpServer(url(tls=_SERVER_TLS), h1=h1_own, h3=h3_ok) as server,
        AioH1Client(url(server.address[1], host="localhost", tls=_CLIENT_TLS)) as client,
        await client.request(b"GET", b"/", ((b"host", b"a"),), end_stream=True) as exchange,
    ):
        response = await exchange.receive_response()
        assert [value for name, value in response.headers if name.lower() == b"alt-svc"] == [b"clear"]


async def test_http3_reads_a_parameter_of_its_own_before_the_shared_one() -> None:
    pytest.importorskip("ngquic")
    from dubbo.transport.http.h3.aio import AioH3Client

    async def h3_ok(stream) -> None:
        await stream.send_response(((b":status", b"200"),), end_stream=True)

    tls = _SERVER_TLS | {"http3.max-header-list-size": "20000"}
    async with AioHttpServer(url(tls=tls, max_header_list_size="10000"), h2=h2_ok, h3=h3_ok) as server:
        port = server.address[1]
        async with AioH2Client(url(port, host="localhost", tls=_CLIENT_TLS)) as client:
            stream = await client.open_stream(_H2_REQUEST, end_stream=True)
            async with stream:
                await stream.receive_response()
                assert stream.remote_settings.max_header_list_size == 10000

        request = ((b":method", b"GET"), (b":scheme", b"https"), (b":authority", b"localhost"), (b":path", b"/"))
        async with AioH3Client(url(port, host="localhost", tls=_CLIENT_TLS)) as client:
            stream = await client.open_stream(request, end_stream=True)
            async with stream:
                await stream.receive_response()
                assert stream.remote_settings is not None
                assert stream.remote_settings.max_field_section_size == 20000


@pytest.mark.parametrize(
    "refusal",
    [OSError(errno.EADDRINUSE, "address already in use"), PermissionError(errno.EACCES, "forbidden")],
    ids=["taken", "reserved"],
)
async def test_an_ephemeral_port_taken_for_udp_is_tried_again(monkeypatch, refusal) -> None:
    pytest.importorskip("ngquic")
    from dubbo.transport.http.h3.aio.server import AioH3Connections

    async def h3_ok(stream) -> None:
        await stream.send_response(((b":status", b"200"),), end_stream=True)

    bind = AioH3Connections.bind
    taken = []

    async def bind_once_taken(self, host, port):
        if not taken:
            taken.append(port)
            raise refusal
        return await bind(self, host, port)

    monkeypatch.setattr(AioH3Connections, "bind", bind_once_taken)
    async with AioHttpServer(url(tls=_SERVER_TLS), h2=h2_ok, h3=h3_ok) as server:
        assert server.address[1] != taken[0]
        response, _ = await h2_get(server.address[1], _CLIENT_TLS)
        assert (b"alt-svc", b'h3=":%d"' % server.address[1]) in response


async def test_http3_needs_tls() -> None:
    pytest.importorskip("ngquic")
    with pytest.raises(ValueError, match="HTTP/3 runs over TLS"):
        AioHttpServer(url(), h3=h2_ok)
