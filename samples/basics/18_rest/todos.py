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

# The contract of the service, with the routes plain HTTP reaches its
# methods on. Triple callers call the same methods by name.

import dataclasses
import typing

import dubbo


@dataclasses.dataclass
class Todo:
    id: int
    title: str
    done: bool = False


@dataclasses.dataclass
class NewTodo:
    title: str


# The class route prefixes the route of each method.
@dubbo.service("org.apache.dubbo.samples.Todos")
@dubbo.route("/todos")
class Todos(typing.Protocol):
    # A parameter named in the path takes that segment; a scalar parameter
    # not in the path comes from the query.
    @dubbo.get("/{id}")
    async def get(self, id: int) -> Todo: ...

    # Annotated constraints are checked on calls over plain HTTP, which
    # answer 422 when one fails.
    @dubbo.get("/", summary="List the todos")
    async def find(
        self, done: bool | None = None, limit: typing.Annotated[int, dubbo.Query(ge=1, le=100)] = 20
    ) -> list[Todo]: ...

    # Any other parameter comes from the JSON body.
    @dubbo.post("/", status_code=201)
    async def add(self, todo: NewTodo) -> Todo: ...
