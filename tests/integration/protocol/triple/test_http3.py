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

from pathlib import Path

import anyio
import pytest
from google.protobuf.wrappers_pb2 import StringValue

from dubbo import RpcError, Status
from dubbo.extension import extensions
from dubbo.protocol.triple import TripleProtocol
from dubbo.transport.http import HttpServer
from dubbo.transport.http.h2 import H2Client
from dubbo.transport.http.h2.aio import AioH2Client
from dubbo.transport.http.h3 import H3Client, H3Priority

pytest.importorskip("ngquic")

from dubbo.transport.http.aio import AioHttpServer
from dubbo.transport.http.h3.aio import AioH3Client

pytestmark = pytest.mark.anyio

_CERTIFICATES = Path(__file__).resolve().parents[3] / "certificates"
_SERVER = (
    f"ssl-enabled=true&tls-cert-file={_CERTIFICATES / 'localhost-cert.pem'}"
    f"&tls-key-file={_CERTIFICATES / 'localhost-key.pem'}&triple.http3.enabled=true"
)
_CLIENT = (
    f"ssl-enabled=true&ca-cert-file={_CERTIFICATES / 'localhost-cert.pem'}&tls-server-name=localhost"
    "&triple.http3.enabled=true&triple.client=recording"
)


@pytest.fixture
def opened(isolated_extensions) -> list[str]:
    """The HTTP version of each call the clients named ``recording`` open a stream for, in order."""
    opened: list[str] = []

    class RecordingH2Client(AioH2Client):
        async def open_stream(self, headers, *, end_stream=False):
            opened.append("h2")
            return await super().open_stream(headers, end_stream=end_stream)

    class RecordingH3Client(AioH3Client):
        async def open_stream(self, headers, *, end_stream=False):
            opened.append("h3")
            return await super().open_stream(headers, end_stream=end_stream)

    extensions.register(H2Client, "recording", RecordingH2Client)
    extensions.register(H3Client, "recording", RecordingH3Client)
    return opened


async def test_calls_move_from_http2_to_http3_once_the_server_announces_it(
    opened, protocol: TripleProtocol, impl, export, refer
) -> None:
    echo = await refer(protocol, await export(protocol, impl, f"?{_SERVER}"), f"?{_CLIENT}")

    # HTTP/3 connects in the background, while time passes between calls.
    for attempt in range(100):
        assert await echo.say(StringValue(value=str(attempt))) == StringValue(value=f"{attempt}|-")
        if opened[-1] == "h3":
            break
        await anyio.sleep(0.01)

    assert opened[0] == "h2"
    assert opened[-1] == "h3"
    assert await echo.say(StringValue(value="again")) == StringValue(value="again|-")
    assert opened[-1] == "h3"


async def test_a_call_after_the_http3_idle_timeout_goes_over_http2_while_http3_connects_again(
    opened, protocol: TripleProtocol, impl, export, refer
) -> None:
    echo = await refer(
        protocol, await export(protocol, impl, f"?{_SERVER}"), f"?{_CLIENT}&triple.http3.idle-timeout=200"
    )
    for attempt in range(100):
        await echo.say(StringValue(value=str(attempt)))
        if opened[-1] == "h3":
            break
        await anyio.sleep(0.01)
    assert opened[-1] == "h3"

    await anyio.sleep(1)
    opened.clear()
    await echo.say(StringValue(value="after the idle timeout"))
    for attempt in range(100):
        if opened[-1] == "h3":
            break
        await anyio.sleep(0.01)
        await echo.say(StringValue(value=str(attempt)))

    # The first call did not wait for the handshake.
    assert opened[0] == "h2"
    assert opened[-1] == "h3"


async def test_every_kind_of_call_runs_over_http3_alone_without_negotiation(
    opened, protocol: TripleProtocol, impl, export, refer
) -> None:
    echo = await refer(
        protocol, await export(protocol, impl, f"?{_SERVER}"), f"?{_CLIENT}&triple.http3.negotiation=false"
    )

    assert await echo.say(StringValue(value="hi")) == StringValue(value="hi|-")
    with pytest.raises(RpcError) as raised:
        await echo.say(StringValue(value="rpc"))
    assert raised.value.status is Status.ABORTED
    async with echo.count(StringValue(value="3")) as events:
        assert [event.value async for event in events] == ["0", "1", "2"]
    async with echo.join() as upload:
        for chunk in "abc":
            await upload.send(StringValue(value=chunk))
        await upload.end()
        assert await upload.receive() == StringValue(value="a,b,c")
    async with echo.chat() as chat:
        await chat.send(StringValue(value="q"))
        assert await chat.receive() == StringValue(value="Q")
        await chat.end()
        assert [answer async for answer in chat] == []

    assert set(opened) == {"h3"}


async def test_a_server_serves_http3_only_when_enabled(opened, protocol: TripleProtocol, impl, export, refer) -> None:
    server = _SERVER.removesuffix("&triple.http3.enabled=true")
    echo = await refer(
        protocol, await export(protocol, impl, f"?{server}"), f"?{_CLIENT}&triple.http3.negotiation=false"
    )

    with pytest.raises(RpcError) as raised:
        await echo.say(StringValue(value="hi"))
    assert raised.value.status is Status.UNAVAILABLE


async def test_a_server_schedules_its_http3_responses_to_take_turns_at_the_clients_urgency(
    opened, protocol: TripleProtocol, impl, export, refer
) -> None:
    scheduled: list[H3Priority] = []

    class Observed:
        """A server stream that records the priorities set on it."""

        def __init__(self, stream) -> None:
            self._stream = stream

        def __getattr__(self, name):
            return getattr(self._stream, name)

        def set_priority(self, priority: H3Priority) -> None:
            scheduled.append(priority)
            self._stream.set_priority(priority)

        async def __aenter__(self):
            await self._stream.__aenter__()
            return self

        async def __aexit__(self, *exc_info):
            return await self._stream.__aexit__(*exc_info)

    class ObservingServer(AioHttpServer):
        def __init__(self, url, *, h1=None, h2=None, h3=None) -> None:
            async def observed(stream) -> None:
                await h3(Observed(stream))

            super().__init__(url, h1=h1, h2=h2, h3=observed)

    extensions.register(HttpServer, "observing", ObservingServer)
    exporter = await export(protocol, impl, f"?{_SERVER}&triple.server=observing")
    echo = await refer(protocol, exporter, f"?{_CLIENT}&triple.http3.negotiation=false")

    assert await echo.say(StringValue(value="hi")) == StringValue(value="hi|-")
    assert scheduled == [H3Priority(3, incremental=True)]
