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

import dataclasses
import typing

import pytest

import dubbo
from dubbo.protocol.triple import TripleProtocol
from dubbo.protocol.triple.grpc.framing import frame
from dubbo.rpc.client import proxy
from dubbo.rpc.server import ServiceInvoker
from dubbo.transport.http.h2.aio import AioH2Client
from dubbo.url import URL

pytestmark = pytest.mark.anyio


@dataclasses.dataclass
class Order:
    item: str
    count: int = 1


@dubbo.service("test.Orders")
class Orders(typing.Protocol):
    @dubbo.method("place")
    async def place(self, customer: str, order: Order) -> list[Order]: ...


class OrdersImpl(Orders):
    async def place(self, customer: str, order: Order) -> list[Order]:
        return [order, Order(item=customer, count=order.count * 2)]


async def test_python_types_travel_as_json(protocol: TripleProtocol) -> None:
    url = URL.parse("tri://127.0.0.1:0/test.Orders?serialization=json")
    exporter = await protocol.export(
        url, dubbo.get_definition(Orders), ServiceInvoker.from_implementation(OrdersImpl(), url)
    )
    reference = URL.parse(f"tri://127.0.0.1:{exporter.url.port}/test.Orders?serialization=json")
    orders = proxy(Orders, (await protocol.refer(reference, dubbo.get_definition(Orders))).invoke)

    assert await orders.place("ann", Order(item="tea", count=2)) == [Order("tea", 2), Order("ann", 4)]


async def test_a_protobuf_message_travels_as_protojson_under_grpc_json(protocol: TripleProtocol, impl, export) -> None:
    exporter = await export(protocol, impl, "?serialization=json")
    request = (
        (b":method", b"POST"),
        (b":scheme", b"http"),
        (b":authority", b"127.0.0.1"),
        (b":path", b"/test.Echo/Say"),
        (b"content-type", b"application/grpc+json"),
        (b"te", b"trailers"),
    )
    async with (
        AioH2Client(URL(protocol="tri", host="127.0.0.1", port=exporter.url.port)) as client,
        await client.open_stream(request) as stream,
    ):
        await stream.send_data(frame(b'"hi"'), end_stream=True)
        headers = dict(await stream.receive_response())
        body = await stream.receive_data_exactly(1 << 16)
        trailers = dict(await stream.receive_trailers() or ())

    assert headers[b"content-type"] == b"application/grpc+json"
    assert body == frame(b'"hi|-"')
    assert trailers[b"grpc-status"] == b"0"
