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
import struct
from typing import Any

import pytest

from dubbo.protocol.dubbo.connection import DubboConnection, EventBodies
from dubbo.serialization.hessian2 import Hessian2Serialization
from dubbo.url import URL
from dubbo.utils.net import read_buffer

pytestmark = pytest.mark.anyio


class _Transport(asyncio.Transport):
    def __init__(self) -> None:
        super().__init__()
        self.reading = True

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return default

    def is_closing(self) -> bool:
        return False

    def pause_reading(self) -> None:
        self.reading = False

    def resume_reading(self) -> None:
        self.reading = True

    def write(self, data: Any) -> None:
        pass

    def close(self) -> None:
        pass


def _connection(transport: asyncio.Transport, frames: list[bytes], *, pause_reading: bool = False) -> DubboConnection:
    connection = DubboConnection(
        on_frame=lambda _connection, _flags, _status, _request_id, body: frames.append(body),
        on_closed=lambda _connection: None,
        serialization_id=2,
        events=EventBodies.from_serialization(Hessian2Serialization(URL(protocol="dubbo"))),
        heartbeat=None,
        idle_timeout=60.0,
        max_body=8 * 1024 * 1024,
        pause_reading=pause_reading,
    )
    connection.connection_made(transport)
    return connection


def _receive(connection: DubboConnection, data: bytes) -> None:
    buffer = connection.get_buffer(-1)
    buffer[: len(data)] = data
    connection.buffer_updated(len(data))


async def test_a_frame_takes_memory_as_its_bytes_arrive() -> None:
    frames: list[bytes] = []
    connection = _connection(_Transport(), frames)
    size = 8 * 1024 * 1024

    _receive(connection, struct.pack(">HBBqI", 0xDABB, 0xC2, 0, 1, size))

    assert len(connection.get_buffer(-1)) < 256 * 1024
    for _ in range(size // 65536):
        _receive(connection, b"x" * 65536)
    assert frames == [b"x" * size]
    connection.connection_lost(None)


async def test_a_connection_keeps_no_buffer_of_its_own_once_a_large_frame_is_handled() -> None:
    frames: list[bytes] = []
    connection = _connection(_Transport(), frames)
    size = 3 * 1024 * 1024

    _receive(connection, struct.pack(">HBBqI", 0xDABB, 0xC2, 0, 1, size))
    for _ in range(size // 65536):
        _receive(connection, b"x" * 65536)

    assert len(frames[0]) == size
    assert connection.get_buffer(-1).obj is read_buffer().obj
    connection.connection_lost(None)


async def test_a_partly_received_frame_survives_another_connection_reading_into_the_shared_buffer() -> None:
    first: list[bytes] = []
    second: list[bytes] = []
    one = _connection(_Transport(), first)
    other = _connection(_Transport(), second)
    frame = struct.pack(">HBBqI", 0xDABB, 0xC2, 0, 1, 6) + b"abcdef"

    _receive(one, frame[:15])
    _receive(other, struct.pack(">HBBqI", 0xDABB, 0xC2, 0, 2, 6) + b"zzzzzz")
    _receive(one, frame[15:])

    assert (first, second) == ([b"abcdef"], [b"zzzzzz"])
    one.connection_lost(None)
    other.connection_lost(None)


async def test_a_server_connection_stops_reading_while_it_cannot_write() -> None:
    transport = _Transport()
    connection = _connection(transport, [], pause_reading=True)

    connection.pause_writing()
    assert not transport.reading
    connection.resume_writing()
    assert transport.reading
    connection.connection_lost(None)


async def test_a_response_too_large_fails_alone_and_the_connection_carries_on() -> None:
    frames: list[bytes | None] = []
    closed: list[bool] = []

    class Closing(_Transport):
        def close(self) -> None:
            closed.append(True)

    connection = _connection(Closing(), frames)
    too_large = 8 * 1024 * 1024 + 1
    header = struct.pack(">HBBqI", 0xDABB, 0x02, 20, 1, too_large)

    _receive(connection, header + b"x" * 1000)
    rest = too_large - 1000
    while rest > 65536:
        _receive(connection, b"x" * 65536)
        rest -= 65536
    _receive(connection, b"x" * rest + struct.pack(">HBBqI", 0xDABB, 0x02, 20, 2, 3) + b"abc")

    assert (frames, closed) == ([None, b"abc"], [])
    connection.connection_lost(None)


async def test_a_request_too_large_closes_the_connection() -> None:
    frames: list[bytes | None] = []
    closed: list[bool] = []

    class Closing(_Transport):
        def close(self) -> None:
            closed.append(True)

    connection = _connection(Closing(), frames)

    _receive(connection, struct.pack(">HBBqI", 0xDABB, 0xC2, 0, 1, 8 * 1024 * 1024 + 1))

    assert (frames, closed) == ([], [True])
    connection.connection_lost(None)
