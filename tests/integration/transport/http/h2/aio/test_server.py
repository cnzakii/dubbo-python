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
import logging
import ssl
from contextlib import ExitStack

import anyio
import ngh2
import pytest

from dubbo.transport.http.aio import AioHttpServer
from dubbo.transport.http.h2 import (
    H2ConnectionError,
    H2Error,
    H2ErrorCode,
    H2StreamError,
    H2StreamResetError,
    H2UsageError,
)
from dubbo.transport.http.h2.aio import AioH2Client

pytestmark = pytest.mark.anyio


async def test_closing_the_server_writes_the_responses_its_handlers_queued(
    request_headers, response_headers, h2_url
) -> None:
    response = b"r" * (100 * 1024)
    handled = anyio.Event()

    async def handler(stream) -> None:
        await stream.send_response(response_headers)
        # Part of the response stays queued behind the client's window.
        await stream.send_data(response)
        await stream.send_trailers(((b"grpc-status", b"0"),))
        handled.set()

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port, initial_window_size="65535")) as client:
            stream = await client.open_stream(request_headers, end_stream=True)
            async with stream, anyio.create_task_group() as task_group:
                await handled.wait()
                task_group.start_soon(server.aclose)

                with anyio.fail_after(5):
                    assert await stream.receive_response() == response_headers
                    assert await stream.receive_data_exactly(len(response)) == response
                    assert await stream.receive_trailers() == ((b"grpc-status", b"0"),)


async def test_a_handler_is_cancelled_when_the_peer_resets_its_stream(request_headers, h2_url) -> None:
    started = anyio.Event()
    cancelled = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await anyio.sleep_forever()

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            stream = await client.open_stream(request_headers)
            await started.wait()
            await stream.reset()

            await cancelled.wait()


async def test_a_handler_is_cancelled_when_its_connection_fails(request_headers, h2_url) -> None:
    started = anyio.Event()
    cancelled = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await anyio.sleep_forever()

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        engine = ngh2.Connection(ngh2.Role.CLIENT)
        engine.initiate_connection()
        engine.send_request(request_headers, end_stream=False)
        async with await anyio.connect_tcp(host, port) as peer:
            await peer.send(engine.data_to_send())
            await started.wait()

        await cancelled.wait()


async def test_a_handler_is_cancelled_when_its_stream_was_reset_before_it_started(request_headers, h2_url) -> None:
    cancelled = anyio.Event()

    async def handler(stream) -> None:
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await anyio.sleep_forever()

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        engine = ngh2.Connection(ngh2.Role.CLIENT)
        engine.initiate_connection()
        async with await anyio.connect_tcp(host, port) as peer:
            await peer.send(engine.data_to_send())
            engine.receive_data(await peer.receive())
            stream_id = engine.send_request(request_headers, end_stream=True)
            request = engine.data_to_send()
            engine.reset_stream(stream_id, ngh2.ErrorCode.CANCEL)
            # The reset arrives with the request, before the handler runs.
            await peer.send(request + engine.data_to_send())

            with anyio.fail_after(5):
                await cancelled.wait()


async def test_a_handler_is_cancelled_when_its_request_breaks_the_protocol(request_headers, h2_url) -> None:
    started = anyio.Event()
    cancelled = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await anyio.sleep_forever()

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        engine = ngh2.Connection(ngh2.Role.CLIENT)
        engine.initiate_connection()
        stream_id = engine.send_request((*request_headers, (b"content-length", b"10")), end_stream=False)
        async with await anyio.connect_tcp(host, port) as peer:
            await peer.send(engine.data_to_send())
            await started.wait()
            # One DATA byte with END_STREAM, short of the ten the request declared.
            await peer.send(b"\x00\x00\x01\x00\x01" + stream_id.to_bytes(4, "big") + b"x")

            with anyio.fail_after(5):
                await cancelled.wait()


async def test_handler_failure_resets_only_its_stream(response_headers, h2_url, path_headers) -> None:
    async def handler(stream) -> None:
        headers = stream.request_headers
        if (b":path", b"/fail") in headers:
            raise RuntimeError("handler failed")
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            failed = await client.open_stream(path_headers(b"/fail"), end_stream=True)
            with pytest.raises(H2StreamResetError) as raised:
                await failed.receive_response()
            assert raised.value.error_code == H2ErrorCode.INTERNAL_ERROR

            healthy = await client.open_stream(path_headers(b"/healthy"), end_stream=True)
            assert await healthy.receive_response() == response_headers
            assert healthy.stream_id == failed.stream_id + 2


async def test_a_handler_that_uses_its_stream_out_of_order_is_logged(request_headers, h2_url, caplog) -> None:
    async def handler(stream) -> None:
        await stream.send_data(b"before any response")

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            stream = await client.open_stream(request_headers, end_stream=True)
            with pytest.raises(H2StreamResetError):
                await stream.receive_response()

    [record] = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert record.getMessage() == "The HTTP/2 stream handler used its stream out of order"
    assert isinstance(record.exc_info[1], H2UsageError)


async def test_a_stream_its_handler_reset_fails_later_operations_without_a_warning(
    request_headers, h2_url, caplog
) -> None:
    caplog.set_level(logging.DEBUG, "dubbo.transport.http.h2.aio.server")

    async def handler(stream) -> None:
        await stream.reset(H2ErrorCode.CANCEL)
        await stream.send_response(((b":status", b"200"),))

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            stream = await client.open_stream(request_headers, end_stream=True)
            with pytest.raises(H2StreamResetError):
                await stream.receive_response()

    [record] = caplog.records
    assert (record.levelno, record.getMessage()) == (
        logging.DEBUG,
        "The HTTP/2 stream handler ended on an error of its stream",
    )
    assert isinstance(record.exc_info[1], H2StreamError)
    assert not isinstance(record.exc_info[1], H2UsageError)


async def test_receiving_the_trailers_discards_a_body_larger_than_the_window(
    request_headers, response_headers, h2_url
) -> None:
    async def handler(stream) -> None:
        trailers = await stream.receive_trailers()
        await stream.send_response(response_headers)
        await stream.send_trailers(trailers)

    async with AioHttpServer(h2_url("127.0.0.1", 0, initial_window_size="65535"), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            stream = await client.open_stream(request_headers)
            with anyio.fail_after(5):
                await stream.send_data(b"x" * (4 * 65535))
                await stream.send_trailers(((b"checksum", b"1"),))
                assert await stream.receive_response() == response_headers
                assert await stream.receive_trailers() == ((b"checksum", b"1"),)


async def test_server_reports_its_address_only_while_listening(h2_url) -> None:
    async def handler(stream) -> None:
        raise AssertionError("no stream is opened")

    server = AioHttpServer(h2_url("127.0.0.1", 0), h2=handler)
    with pytest.raises(RuntimeError, match="not listening"):
        _ = server.address

    async with server:
        host, port = server.address
        assert host == "127.0.0.1"
        assert port != 0
        with pytest.raises(RuntimeError, match="started once"):
            await server.__aenter__()

    with pytest.raises(RuntimeError, match="not listening"):
        _ = server.address


async def test_server_close_lets_running_handlers_finish_and_accepts_no_new_work(
    request_headers, response_headers, h2_url
) -> None:
    started = anyio.Event()
    release = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        await release.wait()
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(h2_url("127.0.0.1", 0, max_concurrent_streams="1"), h2=handler) as server:
        host, port = server.address
        # The running stream fills the connection, so the next one waits until the GOAWAY frees it.
        async with AioH2Client(h2_url(host, port)) as client:
            running = await client.open_stream(request_headers, end_stream=True)
            await started.wait()

            async with anyio.create_task_group() as task_group:
                task_group.start_soon(server.aclose)
                # The shutdown GOAWAY stops the client opening streams on this connection.
                with pytest.raises(H2ConnectionError, match="failed to connect"):
                    await client.open_stream(request_headers, end_stream=True)
                release.set()

            assert await running.receive_response() == response_headers

    assert not server._h2_connections.connections
    assert not server._h2_connections.handlers


async def test_server_announcing_its_shutdown_sends_goaway_and_keeps_serving(
    response_headers, h2_url, path_headers
) -> None:
    async def handler(stream) -> None:
        if (b":path", b"/announce") in stream.request_headers:
            server.announce_shutdown()
        # After an announcement, the client reads the GOAWAY before these headers, which follow it.
        await stream.send_response(response_headers, end_stream=True)

    async with AioHttpServer(h2_url("127.0.0.1", 0), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            stream = await client.open_stream(path_headers(b"/announce"), end_stream=True)
            assert await stream.receive_response() == response_headers

            # The next streams go to a new connection, which the server serves without a notice.
            stream_ids = []
            for _ in range(3):
                stream = await client.open_stream(path_headers(b"/serve"), end_stream=True)
                assert await stream.receive_response() == response_headers
                stream_ids.append(stream.stream_id)
            assert stream_ids == [1, 3, 5]


async def test_server_close_cancels_handlers_still_running_after_the_grace_period(request_headers, h2_url) -> None:
    started = anyio.Event()
    cancelled = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        with ExitStack() as stack:
            stack.callback(cancelled.set)
            await anyio.sleep_forever()

    async with AioHttpServer(h2_url("127.0.0.1", 0, shutwait="10"), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            stream = await client.open_stream(request_headers)
            await started.wait()
            await server.aclose()
            with pytest.raises(H2Error):
                await stream.receive_response()

    assert cancelled.is_set()
    assert not server._h2_connections.connections


async def test_tls_server_bounds_a_stalled_handshake(server_tls, h2_url) -> None:
    async def handler(stream) -> None:
        raise AssertionError("TLS never reached HTTP/2")

    server = AioHttpServer(h2_url("127.0.0.1", 0, ssl_handshake_timeout="50", **server_tls), h2=handler)
    async with server:
        host, port = server.address
        async with await anyio.connect_tcp(host, port) as raw_stream:
            # The server stays open, so only the handshake timeout ends the connection.
            with anyio.fail_after(5), pytest.raises(anyio.EndOfStream):
                await raw_stream.receive()

        assert not server._h2_connections.connections

    assert not server._h2_connections.connections


async def test_tls_server_discards_an_invalid_handshake_without_registering_h2(server_tls, h2_url) -> None:
    async def handler(stream) -> None:
        raise AssertionError("invalid TLS never reached HTTP/2")

    server = AioHttpServer(h2_url("127.0.0.1", 0, ssl_handshake_timeout="200", **server_tls), h2=handler)
    async with server:
        host, port = server.address
        raw_stream = await anyio.connect_tcp(host, port)
        await raw_stream.send(b"not TLS")

        with anyio.fail_after(5):
            with pytest.raises(anyio.EndOfStream):
                await raw_stream.receive()
        assert not server._h2_connections.connections

        await raw_stream.aclose()


async def test_tls_server_aborts_an_established_peer_that_withholds_close_notify(
    server_tls, h2_url, certificates
) -> None:
    async def handler(stream) -> None:
        raise AssertionError("the peer did not send an HTTP/2 request")

    async with AioHttpServer(h2_url("127.0.0.1", 0, **server_tls), h2=handler) as server:
        host, port = server.address
        context = ssl.create_default_context(cafile=certificates / "localhost-cert.pem")
        context.set_alpn_protocols(["h2"])
        reader, writer = await asyncio.open_connection(
            host,
            port,
            ssl=context,
            server_hostname="localhost",
        )
        # The server writes its SETTINGS once it owns the connection.
        await reader.readexactly(9)

        writer.transport.pause_reading()
        with anyio.fail_after(2):
            await server.aclose()
        writer.transport.abort()

    assert not server._h2_connections.connections


async def test_cancelling_server_start_closes_its_listener(monkeypatch, h2_url):
    listeners = []
    started = anyio.Event()
    start_serving = asyncio.base_events.Server.start_serving

    async def start_and_wait(listener):
        listeners.append(listener)
        await start_serving(listener)
        started.set()
        await anyio.sleep_forever()

    async def handler(stream):
        raise AssertionError("no request is sent")

    monkeypatch.setattr(asyncio.base_events.Server, "start_serving", start_and_wait)
    scopes = []

    async def start():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await AioHttpServer(h2_url("127.0.0.1", 0), h2=handler).__aenter__()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(start)
        await started.wait()
        scopes[0].cancel()

    assert len(listeners) == 1
    assert not listeners[0].is_serving()


async def test_cancelling_a_server_close_finishes_the_close_first(request_headers, h2_url) -> None:
    started = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        await anyio.sleep_forever()

    async with AioHttpServer(h2_url("127.0.0.1", 0, shutwait="10000"), h2=handler) as server:
        host, port = server.address
        async with AioH2Client(h2_url(host, port)) as client:
            await client.open_stream(request_headers)
            await started.wait()

            closing = asyncio.get_running_loop().create_task(server.aclose())
            # While the close waits out the handlers' grace period.
            await anyio.wait_all_tasks_blocked()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing

            assert not server._h2_connections.handlers
            assert not server._h2_connections.connections


async def test_cancelling_the_server_owner_closes_its_connections(request_headers, h2_url) -> None:
    handler_started = anyio.Event()

    async def handler(stream) -> None:
        handler_started.set()
        await stream.receive_data()

    server = AioHttpServer(h2_url("127.0.0.1", 0, shutwait="0"), h2=handler)
    client: AioH2Client | None = None
    with anyio.CancelScope() as scope:
        async with server:
            host, port = server.address
            client = AioH2Client(h2_url(host, port))
            await client.open_stream(request_headers)
            await handler_started.wait()
            scope.cancel()
            await anyio.sleep_forever()

    assert client is not None
    await client.aclose()
    assert not server._h2_connections.connections


async def test_a_task_cancelled_while_it_holds_the_server_stops_it_at_once(request_headers, h2_url) -> None:
    started = anyio.Event()

    async def handler(stream) -> None:
        started.set()
        await anyio.sleep_forever()

    listening = anyio.Event()

    async def serve(server: AioHttpServer) -> None:
        async with server:
            listening.set()
            await anyio.sleep_forever()

    server = AioHttpServer(h2_url("127.0.0.1", 0, shutwait="3000"), h2=handler)
    task = asyncio.get_running_loop().create_task(serve(server))
    await listening.wait()
    # The client stays connected, so that only the server's close can end the handler.
    async with AioH2Client(h2_url(*server.address)) as client:
        await client.open_stream(request_headers)
        await started.wait()
        stopping = anyio.current_time()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # A graceful close would wait three seconds for the handler.
    assert anyio.current_time() == stopping
