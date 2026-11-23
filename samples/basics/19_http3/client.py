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

# The consumer: it starts on HTTP/2 and moves to HTTP/3 once the server
# announces it. The log tells when its calls change version.

import asyncio
import logging
from pathlib import Path

from dubbo import Dubbo
from dubbo.config import ConsumerConfig, Http3Config, ReferenceConfig, TLSConfig, TripleConfig

from greeter import Greeter

CERTS = Path(__file__).parents[1] / "16_tls" / "certs"


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Its DEBUG lines also show HTTP/3 connecting again after the idle timeout.
    logging.getLogger("dubbo.protocol.triple.connection").setLevel(logging.DEBUG)
    # An idle timeout of 1 second, so that the connection closes between
    # the last two calls.
    http3 = Http3Config(enabled=True, idle_timeout=1)
    app = Dubbo(
        tls=TLSConfig(cafile=CERTS / "ca.pem"),
        consumer=ConsumerConfig(triple=TripleConfig(http3=http3)),
    )
    greeter = app.refer(Greeter, url="tri://localhost:50051", config=ReferenceConfig(serialization="json"))

    async with app:
        # The first call goes over HTTP/2, and its response announces
        # HTTP/3, which connects in the background.
        print(await greeter.greet("Alice"))
        await asyncio.sleep(0.5)
        # Over HTTP/3.
        print(await greeter.greet("Bob"))
        await asyncio.sleep(2)
        # The HTTP/3 connection closed on its idle timeout: the call goes
        # over HTTP/2 rather than wait for a handshake, while HTTP/3
        # connects again for the calls after it.
        print(await greeter.greet("Carol"))
        await asyncio.sleep(0.5)
        # Over HTTP/3 again.
        print(await greeter.greet("Dave"))


if __name__ == "__main__":
    asyncio.run(main())
