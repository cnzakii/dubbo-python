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

# The provider of a contract generated from greeter.proto, whose routes the
# IDL declares. greeter_pb2 imports google/api/annotations.proto, which
# googleapis-common-protos holds: pip install googleapis-common-protos

import asyncio

from dubbo import Dubbo
from dubbo.config import ApplicationConfig, OpenAPIConfig, ProtocolConfig, RestConfig, TripleConfig

from greeter_dubbo import GreeterServicer
from greeter_pb2 import HelloReply, HelloRequest


class GreeterImpl(GreeterServicer):
    async def say_hello(self, request: HelloRequest) -> HelloReply:
        return HelloReply(message=f"{request.greeting or 'Hello'}, {request.name}!")

    # Deprecated in the proto file: still served, and marked so in the
    # generated contract and the OpenAPI document.
    async def hello(self, request: HelloRequest) -> HelloReply:
        return await self.say_hello(request)


async def main() -> None:
    # The messages are protobuf over Triple, and their JSON mapping over
    # plain HTTP.
    rest = RestConfig(enabled=True, openapi=OpenAPIConfig(enabled=True, info_title="Greeter"))
    app = Dubbo(
        application=ApplicationConfig(name="rest-greeter-server"),
        protocols={"tri": ProtocolConfig(port=50052, triple=TripleConfig(rest=rest))},
    )
    app.export(GreeterImpl())

    async with app:
        print("Serving on port 50052; press Ctrl+C to stop")
        await app.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
