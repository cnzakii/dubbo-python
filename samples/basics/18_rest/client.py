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

# The consumer: it calls the same methods over Triple, as any Dubbo caller.

import asyncio

from dubbo import Dubbo
from dubbo.config import ApplicationConfig, ReferenceConfig

from todos import NewTodo, Todos


async def main() -> None:
    app = Dubbo(application=ApplicationConfig(name="rest-client"))
    todos = app.refer(Todos, url="tri://127.0.0.1:50051", config=ReferenceConfig(serialization="json"))

    async with app:
        added = await todos.add(NewTodo(title="Call over Triple"))
        print("Added:", added)
        print("All:", await todos.find())


if __name__ == "__main__":
    asyncio.run(main())
