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
import socket
import threading

import pytest

pytestmark = pytest.mark.anyio


async def test_the_clock_waits_for_a_connect_to_this_host(monkeypatch) -> None:
    # A connect that takes 50 ms of real time, as on a busy host.
    async def slow_connect(self, sock, address):
        done = asyncio.get_running_loop().create_future()
        threading.Timer(0.05, self.call_soon_threadsafe, (done.set_result, None)).start()
        await done

    loop = asyncio.get_running_loop()
    # The platform's loop, under the virtual clock.
    monkeypatch.setattr(type(loop).__bases__[0], "sock_connect", slow_connect)
    timer = loop.create_task(asyncio.sleep(3))
    with socket.socket() as sock:
        await loop.sock_connect(sock, ("127.0.0.1", 1))

    assert not timer.done()
    timer.cancel()
