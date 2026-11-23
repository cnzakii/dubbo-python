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

import abc
import json
import typing
from typing import Annotated

import anyio
import pytest
from google.protobuf.descriptor_pb2 import FieldDescriptorProto, FieldOptions
from google.protobuf.wrappers_pb2 import StringValue

import dubbo
from dubbo import RpcError, Status
from dubbo.protocol.triple import TripleProtocol
from dubbo.rpc.client import proxy
from dubbo.rpc.server import ServiceInvoker
from dubbo.transport.http.h1.aio import AioH1Client
from dubbo.transport.http.h2.aio import AioH2Client
from dubbo.url import URL

pytestmark = pytest.mark.anyio


@dubbo.service("test.Users")
class UsersServicer(abc.ABC):
    @dubbo.method("greet")
    @abc.abstractmethod
    async def greet(self, name: str, times: int = 1) -> str: ...

    @dubbo.method("tally")
    @abc.abstractmethod
    async def tally(self, scores: dict[str, int], bonus: int = 0) -> int: ...

    @dubbo.method("fail")
    @abc.abstractmethod
    async def fail(self, status: int) -> None: ...

    @dubbo.method("crash")
    @abc.abstractmethod
    async def crash(self) -> None: ...


class UsersImpl(UsersServicer):
    async def greet(self, name: str, times: int = 1) -> str:
        call = dubbo.incoming()
        call.response_attachments["served-by"] = "users"
        return " ".join([f"hi {name}{call.attachments.get('suffix', '')}"] * times)

    async def tally(self, scores: dict[str, int], bonus: int = 0) -> int:
        return sum(scores.values()) + bonus

    async def fail(self, status: int) -> None:
        dubbo.incoming().response_attachments["served-by"] = "users"
        raise RpcError(Status(status), "it failed")

    async def crash(self) -> None:
        raise ValueError("a secret of the server")


class GroupUsersImpl(UsersImpl):
    async def greet(self, name: str, times: int = 1) -> str:
        return "g/2: " + await super().greet(name, times)


@pytest.fixture
async def port(protocol: TripleProtocol, impl, export) -> int:
    exporter = await export(protocol, impl, "?triple.rest.enabled=true&triple.max-receive-message-size=64")
    for query, implementation in (
        ("serialization=hessian2", UsersImpl()),
        ("serialization=hessian2&group=g&version=2", GroupUsersImpl()),
    ):
        url = URL.parse(
            f"tri://127.0.0.1:{exporter.url.port}/test.Users?triple.rest.enabled=true&triple.max-receive-message-size=64&{query}"
        )
        invoker = ServiceInvoker.from_implementation(implementation, url)
        await protocol.export(url, dubbo.get_definition(UsersServicer), invoker)
    return exporter.url.port


async def _h1(port: int, method: bytes, target: bytes, body: bytes = b"", headers=()) -> tuple[int, dict, bytes]:
    async with AioH1Client(URL(protocol="http", host="127.0.0.1", port=port)) as client:
        request = ((b"host", b"127.0.0.1"), (b"content-length", b"%d" % len(body)), *headers)
        async with await client.request(method, target, request, end_stream=not body) as exchange:
            if body:
                await exchange.send_data(body, end_stream=True)
            response = await exchange.receive_response()
            return response.status, dict(response.headers), await exchange.receive_data_exactly(1 << 16)


async def _h2(port: int, method: bytes, target: bytes, body: bytes = b"", headers=()) -> tuple[int, dict, bytes]:
    async with AioH2Client(URL(protocol="tri", host="127.0.0.1", port=port)) as client:
        request = (
            (b":method", method),
            (b":scheme", b"http"),
            (b":authority", b"127.0.0.1"),
            (b":path", target),
            *headers,
        )
        async with await client.open_stream(request, end_stream=not body) as stream:
            if body:
                await stream.send_data(body, end_stream=True)
            fields = dict(await stream.receive_response())
            return int(fields.pop(b":status")), fields, await stream.receive_data_exactly(1 << 16)


@pytest.fixture(params=[_h1, _h2], ids=["h1", "h2"])
def call(request):
    return request.param


async def test_scalar_arguments_come_from_the_query(port, call) -> None:
    status, headers, body = await call(
        port, b"GET", b"/test.Users/greet?name=ann&times=2", headers=((b"suffix", b"!"),)
    )

    assert (status, json.loads(body)) == (200, "hi ann! hi ann!")
    assert headers[b"content-type"] == b"application/json"
    assert headers[b"served-by"] == b"users"


async def test_structured_arguments_come_from_the_json_body(port, call) -> None:
    status, _, body = await call(port, b"POST", b"/test.Users/tally?bonus=1", b'{"a": 1, "b": 2}')

    assert (status, json.loads(body)) == (200, 4)


async def test_a_protobuf_message_is_the_body_in_its_json_mapping(port, call) -> None:
    headers = ((b"content-type", b"application/json"),)
    status, _, body = await call(port, b"POST", b"/test.Echo/Say", b'"hi"', headers)

    assert (status, json.loads(body)) == (200, "hi|-")


async def test_the_service_group_and_version_come_from_triple_or_rest_fields(port, call) -> None:
    for names in ((b"tri-service-group", b"tri-service-version"), (b"rest-service-group", b"rest-service-version")):
        headers = ((names[0], b"g"), (names[1], b"2"))
        status, _, body = await call(port, b"GET", b"/test.Users/greet?name=g", headers=headers)
        assert (status, json.loads(body)) == (200, "g/2: hi g")
    status, _, body = await call(port, b"GET", b"/test.Users/greet?name=g")
    assert (status, json.loads(body)) == (200, "hi g")


@pytest.mark.parametrize(
    ("status", "http_status"),
    [(Status.NOT_FOUND, 404), (Status.INVALID_ARGUMENT, 400), (Status.UNAVAILABLE, 503), (Status.UNKNOWN, 500)],
)
async def test_an_error_status_becomes_its_http_status(port, call, status, http_status) -> None:
    answer, headers, body = await call(port, b"GET", b"/test.Users/fail?status=%d" % status)

    assert (answer, json.loads(body)) == (http_status, {"detail": "it failed"})
    assert headers[b"served-by"] == b"users"


async def test_an_exception_the_method_did_not_choose_is_not_told_to_the_caller(port, call) -> None:
    status, _, body = await call(port, b"GET", b"/test.Users/crash")

    assert (status, json.loads(body)) == (500, {"detail": "Internal Server Error"})


async def test_a_service_group_that_is_not_utf8_is_a_bad_request(port) -> None:
    status, _, body = await _h2(port, b"GET", b"/test.Users/greet?name=a", headers=((b"tri-service-group", b"\xff"),))

    assert status == 400
    assert "UTF-8" in json.loads(body)["detail"]


async def test_grpc_over_http1_is_an_unsupported_media_type(port) -> None:
    status, _, _ = await _h1(port, b"POST", b"/test.Echo/Say", b"x", ((b"content-type", b"application/grpc"),))

    assert status == 415


async def test_invalid_arguments_are_listed_under_422(port, call) -> None:
    status, _, body = await call(port, b"POST", b"/test.Users/tally?bonus=x", b'{"a": "y"}')

    assert status == 422
    # msgspec names no key of a dict, which the message leaves at the end.
    assert {(tuple(error["loc"]), error["msg"]) for error in json.loads(body)["detail"]} == {
        (("query", "bonus"), "Expected `int`, got `str`"),
        (("body",), "Expected `int`, got `str` - at `$[...]`"),
    }


@pytest.mark.parametrize(
    ("method", "target", "body", "headers", "status"),
    [
        (b"POST", b"/test.Users/tally", b"{", (), 422),
        (b"POST", b"/test.Users/tally", b"a=1", ((b"content-type", b"application/x-www-form-urlencoded"),), 415),
        (b"POST", b"/test.Users/tally", b'{"a": 1, "b": "%s"}' % (b"x" * 64), (), 413),
        (b"GET", b"/test.Users/missing", b"", (), 404),
        (b"DELETE", b"/test.Users/greet?name=a", b"", (), 405),
        (b"POST", b"/test.Echo/Count", b'"1"', (), 501),
    ],
    ids=["malformed", "form", "too-large", "unknown", "method", "streaming"],
)
async def test_a_request_that_cannot_be_served_is_answered_with_its_status(
    port, call, method, target, body, headers, status
) -> None:
    answer, fields, data = await call(port, method, target, body, headers)

    assert answer == status
    assert "detail" in json.loads(data)
    if status == 405:
        assert fields[b"allow"] == b"GET, POST"


@dubbo.service("test.Accounts")
@dubbo.route("/accounts")
class AccountsServicer(abc.ABC):
    @dubbo.get("/{id}")
    @abc.abstractmethod
    async def get(self, id: int, token: Annotated[str, dubbo.Header(alias="x-token")] = "") -> dict[str, str]: ...

    @dubbo.post("/", status_code=201)
    @abc.abstractmethod
    async def create(self, name: Annotated[str, dubbo.Form], tags: Annotated[list[str], dubbo.Form] = []) -> str: ...  # noqa: B006

    @dubbo.route("/{id}", methods=["PUT", "PATCH"])
    @abc.abstractmethod
    async def rename(self, id: int, names: dict[str, str]) -> str: ...

    @dubbo.delete("/{id}", status_code=204)
    @abc.abstractmethod
    async def remove(self, id: int) -> None: ...

    @dubbo.get("/files/{name:path}")
    @abc.abstractmethod
    async def file(self, name: str) -> str: ...

    @dubbo.route("/{id}/exists", methods=["GET", "HEAD"])
    @abc.abstractmethod
    async def exists(self, id: int) -> bool: ...

    @dubbo.post("/{id}/reset", status_code=205)
    @abc.abstractmethod
    async def reset(self, id: int) -> str: ...

    @abc.abstractmethod
    async def count(self) -> int: ...


class AccountsImpl(AccountsServicer):
    async def get(self, id: int, token: str = "") -> dict[str, str]:
        return {"id": str(id), "token": token}

    async def create(self, name: str, tags: list[str] = []) -> str:  # noqa: B006
        return f"{name}:{','.join(tags)}"

    async def rename(self, id: int, names: dict[str, str]) -> str:
        return f"{id}={names['new']}"

    async def remove(self, id: int) -> None:
        pass

    async def file(self, name: str) -> str:
        return name

    async def exists(self, id: int) -> bool:
        return True

    async def reset(self, id: int) -> str:
        return "a body a 205 response does not carry"

    async def count(self) -> int:
        return 3


@pytest.fixture
async def accounts(protocol: TripleProtocol) -> int:
    url = URL.parse("tri://127.0.0.1:0/test.Accounts?triple.rest.enabled=true&serialization=hessian2")
    invoker = ServiceInvoker.from_implementation(AccountsImpl(), url)
    exporter = await protocol.export(url, dubbo.get_definition(AccountsServicer), invoker)
    return exporter.url.port


@pytest.mark.parametrize(
    ("method", "target", "body", "headers", "status", "answer"),
    [
        (b"GET", b"/accounts/7", b"", ((b"x-token", b"s"),), 200, {"id": "7", "token": "s"}),
        (b"GET", b"/accounts/7/", b"", (), 200, {"id": "7", "token": ""}),
        (
            b"POST",
            b"/accounts",
            b"name=ann&tags=a&tags=b",
            ((b"content-type", b"application/x-www-form-urlencoded"),),
            201,
            "ann:a,b",
        ),
        (b"PATCH", b"/accounts/7", b'{"new": "bo"}', (), 200, "7=bo"),
        (b"PUT", b"/accounts/7", b'{"new": "cy"}', (), 200, "7=cy"),
        (b"GET", b"/accounts/files/a/b%20c.txt", b"", (), 200, "a/b c.txt"),
        (b"GET", b"/accounts/count", b"", (), 200, 3),
    ],
    ids=["path-and-header", "trailing-slash", "form", "patch", "put", "rest-of-path", "prefixed-default"],
)
async def test_a_routed_method_takes_its_arguments_from_the_request(
    accounts, call, method, target, body, headers, status, answer
) -> None:
    answered, _, data = await call(accounts, method, target, body, headers)

    assert (answered, json.loads(data)) == (status, answer)


async def test_a_route_with_no_content_answers_with_an_empty_body(accounts, call) -> None:
    status, headers, body = await call(accounts, b"DELETE", b"/accounts/7")

    assert (status, body) == (204, b"")
    assert b"content-type" not in headers


async def test_a_head_request_gets_the_headers_of_its_response_without_its_body(accounts, call) -> None:
    status, headers, body = await call(accounts, b"HEAD", b"/accounts/7/exists")

    assert (status, headers[b"content-length"], body) == (200, b"4", b"")
    assert (await call(accounts, b"GET", b"/accounts/7/exists"))[2] == b"true"


async def test_a_reset_content_response_has_no_body(accounts, call) -> None:
    status, _, body = await call(accounts, b"POST", b"/accounts/7/reset")

    assert (status, body) == (205, b"")


async def test_an_encoded_slash_stays_within_its_path_segment(accounts, call) -> None:
    status, _, body = await call(accounts, b"GET", b"/accounts/files%2Fx")

    assert status == 422
    assert json.loads(body)["detail"][0]["loc"] == ["path", "id"]


async def test_a_routed_service_no_longer_served_is_unavailable_on_its_routes_alone(
    protocol: TripleProtocol, call
) -> None:
    url = URL.parse("tri://127.0.0.1:0/test.Accounts?triple.rest.enabled=true&serialization=hessian2")
    invoker = ServiceInvoker.from_implementation(AccountsImpl(), url)
    exporter = await protocol.export(url, dubbo.get_definition(AccountsServicer), invoker)
    stay = URL.parse(f"tri://127.0.0.1:{exporter.url.port}/test.Users?triple.rest.enabled=true&serialization=hessian2")
    await protocol.export(
        stay, dubbo.get_definition(UsersServicer), ServiceInvoker.from_implementation(UsersImpl(), stay)
    )
    await exporter.aclose()

    preflight = ((b"origin", b"https://a.example"), (b"access-control-request-method", b"GET"))

    assert (await call(exporter.url.port, b"GET", b"/accounts/7"))[0] == 503
    assert (await call(exporter.url.port, b"OPTIONS", b"/accounts/7", headers=preflight))[0] == 503
    assert (await call(exporter.url.port, b"GET", b"/test.Accounts/get?id=1"))[0] == 404


async def test_a_route_its_parameters_do_not_fit_fails_the_export(protocol: TripleProtocol) -> None:
    @dubbo.service("test.Unfit")
    class Unfit(typing.Protocol):
        @dubbo.get("/unfit/{id}")
        async def get(self, other: int) -> str: ...

    class UnfitImpl(Unfit):
        async def get(self, other: int) -> str:
            return ""

    url = URL.parse("tri://127.0.0.1:0/test.Unfit?triple.rest.enabled=true&serialization=hessian2")
    with pytest.raises(TypeError, match=r"test\.Unfit\.get cannot be served on /unfit/\{id\}"):
        await protocol.export(url, dubbo.get_definition(Unfit), ServiceInvoker.from_implementation(UnfitImpl(), url))

    # Without plain HTTP, the route is not built, and the method is served over gRPC.
    url = URL.parse("tri://127.0.0.1:0/test.Unfit?serialization=hessian2")
    exporter = await protocol.export(
        url, dubbo.get_definition(Unfit), ServiceInvoker.from_implementation(UnfitImpl(), url)
    )
    await exporter.aclose()


@pytest.mark.parametrize(
    ("query", "target", "status"),
    [
        ("triple.rest.enabled=true&triple.rest.enable-default-mapping=false", b"/test.Accounts/get?id=1", 404),
        ("triple.rest.enabled=true&triple.rest.enable-default-mapping=false", b"/accounts/7", 200),
        ("triple.rest.enabled=false", b"/accounts/7", None),
        ("", b"/accounts/7", None),
    ],
    ids=["default-mapping-off", "route-stays", "rest-off", "rest-off-by-default"],
)
async def test_plain_http_is_off_unless_enabled_and_its_default_mapping_can_be_turned_off(
    protocol: TripleProtocol, call, query, target, status
):
    url = URL.parse(f"tri://127.0.0.1:0/test.Accounts?serialization=hessian2&{query}")
    invoker = ServiceInvoker.from_implementation(AccountsImpl(), url)
    exporter = await protocol.export(url, dubbo.get_definition(AccountsServicer), invoker)

    answered, _, _ = await call(exporter.url.port, b"GET", target)

    # Without plain HTTP, HTTP/1.1 finds nothing and HTTP/2 takes the request for gRPC, which refuses its media type.
    assert answered == (status if status is not None else 404 if call is _h1 else 415)


@pytest.mark.parametrize(
    ("method", "target", "body", "headers", "status"),
    [
        (b"POST", b"/accounts/7", b"", (), 405),
        (b"GET", b"/test.Accounts/get?id=1", b"", (), 404),
        (b"GET", b"/accounts/x", b"", (), 422),
        (b"POST", b"/accounts", b'{"name": "a"}', ((b"content-type", b"application/json"),), 415),
    ],
    ids=["method", "default-path-replaced", "path-type", "form-needs-form"],
)
async def test_a_routed_request_that_cannot_be_served_is_answered_with_its_status(
    accounts, call, method, target, body, headers, status
) -> None:
    answered, fields, data = await call(accounts, method, target, body, headers)

    assert answered == status
    assert "detail" in json.loads(data)
    if status == 405:
        assert fields[b"allow"] == b"DELETE, GET, PATCH, PUT"


async def test_a_service_whose_route_another_takes_is_not_exported(protocol: TripleProtocol, accounts) -> None:
    @dubbo.service("test.Other")
    class Other(typing.Protocol):
        @dubbo.get("/accounts/{name}")
        async def other(self, name: str) -> str: ...

    class OtherImpl(Other):
        async def other(self, name: str) -> str:
            return name

    url = URL.parse(f"tri://127.0.0.1:{accounts}/test.Other?triple.rest.enabled=true&serialization=hessian2")
    with pytest.raises(ValueError, match="routed twice"):
        await protocol.export(url, dubbo.get_definition(Other), ServiceInvoker.from_implementation(OtherImpl(), url))


async def test_unexporting_a_service_leaves_the_call_of_another_on_the_same_http1_connection(
    protocol: TripleProtocol, impl, export
) -> None:
    port = (await export(protocol, impl, "?triple.rest.enabled=true")).url.port
    url = URL.parse(f"tri://127.0.0.1:{port}/test.Users?triple.rest.enabled=true&serialization=hessian2")
    users = await protocol.export(
        url, dubbo.get_definition(UsersServicer), ServiceInvoker.from_implementation(UsersImpl(), url)
    )
    results = []

    async with AioH1Client(URL(protocol="http", host="127.0.0.1", port=port)) as client:

        async def request(method: bytes, target: bytes, body: bytes = b"") -> tuple[int, bytes]:
            headers = ((b"host", b"127.0.0.1"), (b"content-length", b"%d" % len(body)))
            async with await client.request(method, target, headers, end_stream=not body) as exchange:
                if body:
                    await exchange.send_data(body, end_stream=True)
                response = await exchange.receive_response()
                return response.status, await exchange.receive_data_exactly(1 << 16)

        assert (await request(b"GET", b"/test.Users/greet?name=a"))[0] == 200
        async with anyio.create_task_group() as tasks:

            async def wait() -> None:
                results.append(await request(b"POST", b"/test.Echo/Say", b'"wait"'))

            tasks.start_soon(wait)
            await impl.started.wait()
            await users.aclose()
            impl.release.set()

    assert results == [(200, b'"wait|-"')]


@dubbo.service("test.Marked")
class MarkedServicer(abc.ABC):
    @dubbo.post("/marked")
    @abc.abstractmethod
    async def say(self, request: Annotated[StringValue, dubbo.Body]) -> StringValue: ...


class MarkedImpl(MarkedServicer):
    async def say(self, request: StringValue) -> StringValue:
        return StringValue(value=request.value + "!")


@pytest.mark.parametrize("serialization", ["protobuf", "json"])
async def test_a_marked_protobuf_parameter_is_still_a_protobuf_message(
    protocol: TripleProtocol, call, serialization
) -> None:
    url = URL.parse(f"tri://127.0.0.1:0/test.Marked?triple.rest.enabled=true&serialization={serialization}")
    exporter = await protocol.export(
        url, dubbo.get_definition(MarkedServicer), ServiceInvoker.from_implementation(MarkedImpl(), url)
    )
    marked = proxy(MarkedServicer, (await protocol.refer(exporter.url, dubbo.get_definition(MarkedServicer))).invoke)
    status, _, body = await call(exporter.url.port, b"POST", b"/marked", b'"ho"')

    assert await marked.say(StringValue(value="hi")) == StringValue(value="hi!")
    assert (status, json.loads(body)) == (200, "ho!")


@dubbo.service("test.Fields")
class FieldsServicer(abc.ABC):
    @dubbo.method("Describe")
    @dubbo.get("/fields/{name}", body=None, response_body="options")
    @dubbo.post("/fields/{name}/options", body="options")
    @abc.abstractmethod
    async def describe(self, request: FieldDescriptorProto) -> FieldDescriptorProto: ...


class FieldsImpl(FieldsServicer):
    async def describe(self, request: FieldDescriptorProto) -> FieldDescriptorProto:
        return FieldDescriptorProto(
            name=request.name, number=request.number, options=FieldOptions(deprecated=request.options.lazy)
        )


async def test_a_protobuf_request_takes_its_fields_from_the_path_query_and_body_on_each_route(
    protocol: TripleProtocol, call
) -> None:
    url = URL.parse("tri://127.0.0.1:0/test.Fields?triple.rest.enabled=true")
    exporter = await protocol.export(
        url, dubbo.get_definition(FieldsServicer), ServiceInvoker.from_implementation(FieldsImpl(), url)
    )
    port = exporter.url.port

    got = await call(port, b"GET", b"/fields/f?options.lazy=true&number=3")
    posted = await call(port, b"POST", b"/fields/f/options?number=4", b'{"lazy": false}')
    invalid = await call(port, b"GET", b"/fields/f?number=x")

    assert (got[0], json.loads(got[2])) == (200, {"deprecated": True})
    assert (posted[0], json.loads(posted[2])) == (200, {"name": "f", "number": 4, "options": {"deprecated": False}})
    assert (invalid[0], json.loads(invalid[2])["detail"][0]["loc"]) == (422, ["query", "number"])


async def test_cors_answers_preflight_requests_and_marks_every_response(protocol: TripleProtocol, call) -> None:
    query = (
        "serialization=hessian2&triple.rest.cors.allowed-origins=https://a.example&triple.rest.cors.allowed-methods=GET"
    )
    url = URL.parse(f"tri://127.0.0.1:0/test.Accounts?triple.rest.enabled=true&{query}")
    exporter = await protocol.export(
        url, dubbo.get_definition(AccountsServicer), ServiceInvoker.from_implementation(AccountsImpl(), url)
    )
    port = exporter.url.port
    origin = (b"origin", b"https://a.example")

    def preflight(method: bytes, target: bytes = b"/accounts/7"):
        return call(port, b"OPTIONS", target, headers=(origin, (b"access-control-request-method", method)))

    allowed, refused, wrong, missing = (
        await preflight(b"GET"),
        await preflight(b"PUT"),
        await preflight(b"DELETE"),
        await preflight(b"GET", b"/nowhere"),
    )
    served = await call(port, b"GET", b"/accounts/7", headers=(origin,))
    failed = await call(port, b"GET", b"/accounts/x", headers=(origin,))

    assert (allowed[0], allowed[1][b"access-control-allow-origin"], allowed[2]) == (204, b"https://a.example", b"")
    assert (refused[0], wrong[0], missing[0]) == (403, 403, 404)
    assert (served[0], served[1][b"access-control-allow-origin"], served[1][b"vary"]) == (
        200,
        b"https://a.example",
        b"origin",
    )
    assert (failed[0], failed[1][b"access-control-allow-origin"]) == (422, b"https://a.example")


async def test_a_preflight_request_reaches_a_service_of_a_group_and_version(protocol: TripleProtocol, call) -> None:
    query = "serialization=hessian2&group=g&version=2&triple.rest.cors.allowed-origins=https://a.example"
    url = URL.parse(f"tri://127.0.0.1:0/test.Accounts?triple.rest.enabled=true&{query}")
    exporter = await protocol.export(
        url, dubbo.get_definition(AccountsServicer), ServiceInvoker.from_implementation(AccountsImpl(), url)
    )
    origin = (b"origin", b"https://a.example")
    names = (b"access-control-request-headers", b"tri-service-group,tri-service-version")

    # A browser asks first without the values of the fields that name the group and version.
    allowed = await call(
        exporter.url.port,
        b"OPTIONS",
        b"/accounts/7",
        headers=(origin, (b"access-control-request-method", b"GET"), names),
    )
    served = await call(
        exporter.url.port,
        b"GET",
        b"/accounts/7",
        headers=(origin, (b"tri-service-group", b"g"), (b"tri-service-version", b"2")),
    )

    assert (allowed[0], served[0]) == (204, 200)


async def test_an_absolute_form_request_target_is_served_as_its_path(accounts) -> None:
    status, _, body = await _h1(accounts, b"GET", b"http://127.0.0.1/accounts/7?x=1")

    assert (status, json.loads(body)["id"]) == (200, "7")


async def test_without_cors_a_preflight_request_is_refused(accounts, call) -> None:
    status, headers, _ = await call(
        accounts,
        b"OPTIONS",
        b"/accounts/7",
        headers=((b"origin", b"https://a.example"), (b"access-control-request-method", b"GET")),
    )

    assert status == 403
    assert b"access-control-allow-origin" not in headers


@pytest.fixture
async def documented(protocol: TripleProtocol) -> int:
    query = (
        "triple.rest.openapi.enabled=true&triple.rest.openapi.info-title=Shop%20%26%20Co"
        "&triple.health-enabled=true&triple.reflection-enabled=true"
    )
    port = 0
    for interface, contract, implementation, serialization in (
        ("test.Accounts", AccountsServicer, AccountsImpl(), "hessian2"),
        ("test.Fields", FieldsServicer, FieldsImpl(), "protobuf"),
        ("test.Users", UsersServicer, UsersImpl(), "hessian2"),
    ):
        url = URL.parse(
            f"tri://127.0.0.1:{port}/{interface}?triple.rest.enabled=true&serialization={serialization}&{query}"
        )
        exporter = await protocol.export(
            url, dubbo.get_definition(contract), ServiceInvoker.from_implementation(implementation, url)
        )
        port = exporter.url.port
    return port


async def test_the_built_in_services_are_not_served_over_plain_http(documented, call) -> None:
    status, _, _ = await call(documented, b"POST", b"/grpc.health.v1.Health/Check", b"{}")

    assert status == 404


async def test_the_openapi_document_describes_every_route(documented, call) -> None:
    status, headers, body = await call(documented, b"GET", b"/dubbo/openapi/openapi.json")
    document = json.loads(body)
    paths = document["paths"]

    assert (status, headers[b"content-type"]) == (200, b"application/json")
    assert (document["openapi"], document["info"]["title"]) == ("3.1.0", "Shop & Co")
    get = paths["/accounts/{id}"]["get"]
    assert (get["operationId"], get["tags"]) == ("test.Accounts.get", ["test.Accounts"])
    assert {(p["name"], p["in"], p["required"]) for p in get["parameters"]} == {
        ("id", "path", True),
        ("x-token", "header", False),
    }
    assert set(paths["/accounts/{id}"]) == {"get", "put", "patch", "delete"}
    assert "application/x-www-form-urlencoded" in paths["/accounts"]["post"]["requestBody"]["content"]
    assert set(paths["/test.Users/greet"]) == {"post"}
    assert not [path for path in paths if path.startswith("/grpc.")]
    described = paths["/fields/{name}"]["get"]
    assert described["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/google.protobuf.FieldOptions"
    }
    assert {"name", "number", "options.lazy"} <= {p["name"] for p in described["parameters"]}
    posted = paths["/fields/{name}/options"]["post"]
    assert posted["operationId"] == "test.Fields.Describe_post"
    assert posted["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/google.protobuf.FieldOptions"
    }
    schemas = document["components"]["schemas"]
    assert schemas["google.protobuf.FieldDescriptorProto"]["properties"]["number"] == {
        "type": "integer",
        "format": "int32",
    }


@pytest.mark.parametrize(
    ("method", "target", "status", "kind"),
    [
        (b"GET", b"/dubbo/openapi", 302, b"/dubbo/openapi/swagger-ui/"),
        (b"GET", b"/dubbo/openapi/swagger-ui/", 200, b"text/html; charset=utf-8"),
        (b"GET", b"/dubbo/openapi/redoc/index.html", 200, b"text/html; charset=utf-8"),
        (b"GET", b"/dubbo/openapi/api-docs/default.json", 200, b"application/json"),
        (b"GET", b"/dubbo/openapi/api-docs/other", 404, b"application/json"),
        (b"POST", b"/dubbo/openapi/openapi.json", 405, None),
    ],
)
async def test_the_openapi_viewers_and_documents_are_served_under_their_path(
    documented, call, method, target, status, kind
) -> None:
    answered, headers, body = await call(documented, method, target)

    assert answered == status
    if status == 302:
        assert headers[b"location"] == kind
    elif kind is not None:
        assert headers[b"content-type"] == kind
    if kind == b"text/html; charset=utf-8":
        assert b'"../api-docs/default"' in body


async def test_without_openapi_its_path_is_no_route(accounts, call) -> None:
    status, _, _ = await call(accounts, b"GET", b"/dubbo/openapi/openapi.json")

    assert status == 404


async def test_a_contract_with_a_list_default_can_be_referred(protocol: TripleProtocol, accounts) -> None:
    url = URL.parse(f"tri://127.0.0.1:{accounts}/test.Accounts?triple.rest.enabled=true&serialization=hessian2")
    contract = dubbo.get_definition(AccountsServicer)

    first = await protocol.refer(url, contract)
    second = await protocol.refer(url, contract)
    await first.aclose()
    await second.aclose()

    assert first is not second


async def test_a_value_plain_http_cannot_write_answers_500_with_its_attachments(
    protocol: TripleProtocol, echo_impl_type, call
) -> None:
    class Unencodable(echo_impl_type):
        async def say(self, request: StringValue) -> StringValue:
            if request.value != "unencodable":
                return await super().say(request)
            dubbo.incoming().response_attachments["served-by"] = "echo"
            return "not a message"  # ty: ignore[invalid-return-type]

    url = URL.parse("tri://127.0.0.1:0/test.Echo?triple.rest.enabled=true")
    impl = Unencodable()
    exporter = await protocol.export(url, dubbo.get_definition(impl), ServiceInvoker.from_implementation(impl, url))
    json_body = ((b"content-type", b"application/json"),)

    failed = await call(exporter.url.port, b"POST", b"/test.Echo/Say", b'"unencodable"', headers=json_body)
    served = await call(exporter.url.port, b"POST", b"/test.Echo/Say", b'"hi"', headers=json_body)

    assert (failed[0], failed[1].get(b"served-by"), json.loads(failed[2])) == (
        500,
        b"echo",
        {"detail": "Internal Server Error"},
    )
    assert (served[0], json.loads(served[2])) == (200, "hi|-")
