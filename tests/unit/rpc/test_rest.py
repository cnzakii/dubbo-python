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

import typing
from typing import Annotated

import pytest

import dubbo
from dubbo import HttpRoute, get_definition
from dubbo.rpc.params import param_of


@dubbo.service("test.Users")
@dubbo.route("/users")
class Users(typing.Protocol):
    @dubbo.get("/{id}")
    async def get_user(self, id: int) -> str: ...

    @dubbo.route("/{id}", methods=["put", "PATCH"], status_code=202)
    async def update(self, id: int, name: str) -> None: ...

    @dubbo.post("/", status_code=201)
    async def create(self, name: str) -> int: ...

    @dubbo.method("countAll")
    async def count(self) -> int: ...


@dubbo.service("test.Plain")
class Plain(typing.Protocol):
    @dubbo.delete("/plain/{id}")
    async def remove(self, id: int) -> None: ...

    async def ping(self) -> None: ...


def test_routes_join_the_prefix_of_their_contract() -> None:
    definition = get_definition(Users)

    assert definition.method("get_user").http_routes == (HttpRoute(path="/users/{id}", methods=["GET"]),)
    assert definition.method("update").http_routes == (
        HttpRoute(path="/users/{id}", methods=["PUT", "PATCH"], status_code=202),
    )
    assert definition.method("create").http_routes == (HttpRoute(path="/users", methods=["POST"], status_code=201),)
    assert definition.method("countAll").http_routes == (HttpRoute(path="/users/countAll", methods=["GET", "POST"]),)


def test_without_a_prefix_only_routed_methods_have_a_route() -> None:
    definition = get_definition(Plain)

    assert definition.method("remove").http_routes == (HttpRoute(path="/plain/{id}", methods=["DELETE"]),)
    assert definition.method("ping").http_routes == ()
    assert definition.method("remove").http_routes[0].variables == ("id",)


def test_an_implementation_keeps_the_routes_of_its_contract() -> None:
    class UsersImpl(Users):
        async def get_user(self, id: int) -> str:
            return ""

        async def update(self, id: int, name: str) -> None:
            pass

        async def create(self, name: str) -> int:
            return 0

        async def count(self) -> int:
            return 0

    served = dubbo.Service.from_implementation(UsersImpl())
    assert served.method("get_user").http_routes == get_definition(Users).method("get_user").http_routes


def test_a_streaming_method_cannot_have_a_route() -> None:
    @dubbo.service("test.Streams")
    class Streams(typing.Protocol):
        @dubbo.get("/watch")
        def watch(self, query: str) -> dubbo.ServerStream[str]: ...

    with pytest.raises(TypeError, match="cannot have an HTTP route"):
        get_definition(Streams)


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("users", "must start with '/'"),
        ("/users/", "must not end with '/'"),
        ("/users/id{id}", "not text or one"),
        ("/{rest:path}/more", "only as its last segment"),
        ("/{id}/{id}", "more than once"),
        ("/a//b", "not text or one"),
    ],
)
def test_an_invalid_path_template_is_refused(path, error) -> None:
    with pytest.raises(ValueError, match=error):
        dubbo.get(path)


def test_a_route_needs_methods_on_a_method_and_none_on_a_class() -> None:
    with pytest.raises(TypeError, match="needs its HTTP methods"):
        dubbo.route("/a")(lambda self: None)
    with pytest.raises(TypeError, match="only prefixes"):
        dubbo.route("/a", methods=["GET"])(type("Contract", (), {}))
    with pytest.raises(ValueError, match="at least one"):
        dubbo.route("/a", methods=[])
    with pytest.raises(TypeError, match="not the string"):
        dubbo.route("/a", methods="GET")
    with pytest.raises(ValueError, match="not HTTP method names"):
        dubbo.route("/a", methods=["GET POST"])


@pytest.mark.parametrize("status_code", [199, 600])
def test_a_status_outside_200_to_599_is_refused(status_code) -> None:
    with pytest.raises(ValueError, match="from 200 to 599"):
        dubbo.get("/a", status_code=status_code)


def test_a_path_template_is_checked_when_a_route_is_made_by_hand() -> None:
    with pytest.raises(ValueError, match="must not end with '/'"):
        HttpRoute(path="/a/", methods=["GET"])


def test_a_method_takes_several_routes_in_order_and_a_class_one() -> None:
    @dubbo.get("/a")
    @dubbo.post("/b", body=None)
    def handler(self) -> None: ...

    assert vars(handler)["__dubbo_http__"] == (
        HttpRoute(path="/a", methods=["GET"]),
        HttpRoute(path="/b", methods=["POST"], body=None),
    )

    contract = dubbo.route("/a")(type("Contract", (), {}))
    with pytest.raises(TypeError, match="already has a route"):
        dubbo.route("/b")(contract)


def test_documentation_belongs_to_the_route_of_a_method() -> None:
    def handler(self) -> None: ...

    dubbo.get("/a", summary="s", tags=["t"], deprecated=True, include_in_schema=False)(handler)
    (route,) = vars(handler)["__dubbo_http__"]

    assert (route.summary, route.tags, route.deprecated, route.include_in_schema) == ("s", ("t",), True, False)
    with pytest.raises(TypeError, match="only prefixes"):
        dubbo.route("/a", summary="s")(type("Contract", (), {}))
    with pytest.raises(TypeError, match="only prefixes"):
        dubbo.route("/a", body=None)(type("Contract", (), {}))


@pytest.mark.parametrize(("body", "response_body"), [("a.b", None), ("", None), ("*", "*"), ("*", "a b")])
def test_body_and_response_body_name_a_field(body, response_body) -> None:
    with pytest.raises(ValueError, match="body must be"):
        HttpRoute(path="/a", methods=["POST"], body=body, response_body=response_body)


def test_a_path_variable_may_name_a_nested_field() -> None:
    assert HttpRoute(path="/books/{book.id}/{name:path}", methods=["GET"]).variables == ("book.id", "name")


def test_a_param_is_found_in_the_metadata_of_its_annotation() -> None:
    assert param_of(Annotated[int, dubbo.Query]) == dubbo.Query()
    assert param_of(Annotated[int, "unit", dubbo.Header(alias="x")]) == dubbo.Header(alias="x")
    assert param_of(int) is None
    assert dubbo.Query.location is dubbo.ParamLocation.QUERY
    with pytest.raises(TypeError, match="more than one place"):
        param_of(Annotated[int, dubbo.Query, dubbo.Body])
    with pytest.raises(TypeError, match="base of the places"):
        param_of(Annotated[int, dubbo.Param])
