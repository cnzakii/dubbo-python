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

# The provider: it serves the Todos over Triple and over plain HTTP on one
# port, until Ctrl+C.

import asyncio

from dubbo import Dubbo, RpcError, Status
from dubbo.config import (
    ApplicationConfig,
    CorsConfig,
    OpenAPIConfig,
    ProtocolConfig,
    ProviderConfig,
    RestConfig,
    TripleConfig,
)

from todos import NewTodo, Todo, Todos


class TodosImpl(Todos):
    def __init__(self) -> None:
        self._todos = {1: Todo(id=1, title="Read the samples", done=True)}

    async def get(self, id: int) -> Todo:
        try:
            return self._todos[id]
        except KeyError:
            # A status becomes its HTTP status: NOT_FOUND answers 404.
            raise RpcError(Status.NOT_FOUND, f"no todo {id}") from None

    async def find(self, done: bool | None = None, limit: int = 20) -> list[Todo]:
        todos = [todo for todo in self._todos.values() if done is None or todo.done == done]
        return todos[:limit]

    async def add(self, todo: NewTodo) -> Todo:
        added = self._todos[len(self._todos) + 1] = Todo(id=len(self._todos) + 1, title=todo.title)
        return added


async def main() -> None:
    # Plain HTTP is off unless rest.enabled turns it on. CORS lets the pages
    # of one origin call from a browser, and the OpenAPI document describes
    # the routes, with Swagger UI to try them.
    rest = RestConfig(
        enabled=True,
        cors=CorsConfig(allowed_origins=("https://app.example",)),
        openapi=OpenAPIConfig(enabled=True, info_title="Todos"),
    )
    # JSON is what plain HTTP carries; with it on Triple too, the Python
    # types of the contract travel the same way on both.
    app = Dubbo(
        application=ApplicationConfig(name="rest-server"),
        protocols={"tri": ProtocolConfig(port=50051, triple=TripleConfig(rest=rest))},
        provider=ProviderConfig(serialization="json"),
    )
    app.export(TodosImpl())

    async with app:
        print("Serving on port 50051; press Ctrl+C to stop")
        await app.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
