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

# The provider: it serves the Greeter over HTTP/2 on TCP port 50051 and over
# HTTP/3 on UDP port 50051, until Ctrl+C. It needs the certificates of
# ../16_tls: python ../16_tls/make_certificates.py

import asyncio
from pathlib import Path

from dubbo import Dubbo
from dubbo.config import ApplicationConfig, Http3Config, ProtocolConfig, ProviderConfig, TLSConfig, TripleConfig

from greeter import Greeter

CERTS = Path(__file__).parents[1] / "16_tls" / "certs"


class GreeterImpl(Greeter):
    async def greet(self, name: str) -> str:
        return f"Hello, {name}!"


async def main() -> None:
    # HTTP/3 runs over TLS alone. With it enabled, the HTTP/2 responses
    # announce it in an alt-svc field.
    app = Dubbo(
        application=ApplicationConfig(name="http3-server"),
        protocols={"tri": ProtocolConfig(port=50051, triple=TripleConfig(http3=Http3Config(enabled=True)))},
        provider=ProviderConfig(serialization="json"),
        tls=TLSConfig(certfile=CERTS / "server.pem"),
    )
    app.export(GreeterImpl())

    async with app:
        print("Serving HTTP/2 and HTTP/3 on port 50051; press Ctrl+C to stop")
        await app.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
