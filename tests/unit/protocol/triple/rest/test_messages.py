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

import dataclasses
import datetime
import enum
import inspect
import uuid
from typing import Annotated, Any

import msgspec
import pydantic
import pytest
from google.protobuf.wrappers_pb2 import StringValue

import dubbo
from dubbo.protocol.triple.rest.exceptions import HttpError
from dubbo.protocol.triple.rest.messages import BodyKind, RestMessages
from dubbo.rpc.definition import Method, Parameter
from dubbo.rpc.params import ParamLocation
from dubbo.serialization.json import JsonSerialization
from dubbo.url import URL


@dataclasses.dataclass
class User:
    name: str


class Color(enum.Enum):
    RED = "red"


def _messages(*parameters: tuple, variables: tuple[str, ...] = ()) -> RestMessages:
    request = tuple(
        Parameter(name=name, type=annotation, default=default[0] if default else inspect.Parameter.empty)
        for name, annotation, *default in parameters
    )
    return RestMessages.of(
        Method(name="m", request=request, response=None), JsonSerialization(URL(protocol="tri")), variables
    )


def _arguments(messages: RestMessages, *, path=None, query=b"", headers=(), body=b"") -> tuple:
    return messages.arguments(path or {}, query, headers, body)


def _errors(error: HttpError) -> set[tuple[tuple, str]]:
    return {(tuple(item["loc"]), item["type"]) for item in error.detail}


@pytest.mark.parametrize(
    ("annotation", "location"),
    [
        (int, ParamLocation.QUERY),
        (str, ParamLocation.QUERY),
        (int | None, ParamLocation.QUERY),
        (Annotated[float, "unit"], ParamLocation.QUERY),
        (bytes, ParamLocation.QUERY),
        (User, ParamLocation.BODY),
        (User | None, ParamLocation.BODY),
        (StringValue, ParamLocation.BODY),
        (list[int], ParamLocation.BODY),
        (dict[str, int], ParamLocation.BODY),
        (tuple[int, int], ParamLocation.BODY),
        (Annotated[list[int], dubbo.Query], ParamLocation.QUERY),
        (Annotated[str, dubbo.Body], ParamLocation.BODY),
        (Annotated[str, dubbo.Header], ParamLocation.HEADER),
        (Annotated[str, dubbo.Form()], ParamLocation.FORM),
    ],
    ids=lambda value: str(value).replace("typing.", "").replace("dubbo.rpc.params.", "").replace(" ", ""),
)
def test_a_parameter_comes_from_its_param_else_from_its_type(annotation, location) -> None:
    (parameter,) = _messages(("value", annotation)).parameters

    assert parameter.location is location


def test_arguments_keep_the_parameter_order_whatever_their_source() -> None:
    messages = _messages(
        ("id", int),
        ("a", int),
        ("user", User),
        ("trace_id", Annotated[str, dubbo.Header]),
        ("tags", list[str]),
        variables=("id",),
    )

    body = b'{"user":{"name":"x"},"tags":["t"],"other":1}'
    arguments = _arguments(messages, path={"id": "7"}, query=b"a=1", headers=((b"trace-id", b"t1"),), body=body)
    assert arguments == (7, 1, User("x"), "t1", ["t"])


def test_aliases_name_the_values_in_the_request() -> None:
    messages = _messages(
        ("user_id", Annotated[int, dubbo.Path(alias="id")]),
        ("page", Annotated[int, dubbo.Query(alias="p")]),
        ("token", Annotated[str, dubbo.Header(alias="X-Token")]),
        variables=("id",),
    )

    arguments = _arguments(messages, path={"id": "1"}, query=b"p=2", headers=((b"x-token", b"s"),))
    assert arguments == (1, 2, "s")


def test_msgspec_meta_beside_a_param_constrains_the_value() -> None:
    messages = _messages(("page", Annotated[int, dubbo.Query(alias="n"), msgspec.Meta(ge=1)]))

    assert _arguments(messages, query=b"n=3") == (3,)
    with pytest.raises(HttpError) as caught:
        _arguments(messages, query=b"n=0")
    assert caught.value.status == 422
    assert caught.value.detail == [{"loc": ["query", "n"], "msg": "Expected `int` >= 1", "type": "value_invalid"}]


def test_the_constraints_of_a_param_of_an_optional_type_apply_to_a_value_given() -> None:
    messages = _messages(("limit", Annotated[int | None, dubbo.Query(gt=0, description="limit")], None))

    assert (_arguments(messages, query=b"limit=5"), _arguments(messages)) == ((5,), (None,))
    with pytest.raises(HttpError) as caught:
        _arguments(messages, query=b"limit=0")
    assert caught.value.detail == [{"loc": ["query", "limit"], "msg": "Expected `int` >= 1", "type": "value_invalid"}]


def test_the_constraints_of_a_param_apply_as_those_of_msgspec_meta() -> None:
    messages = _messages(
        ("page", Annotated[int, dubbo.Query(ge=1, description="page")]),
        ("a", Annotated[str, dubbo.Body(max_length=1)]),
        ("b", Annotated[str, dubbo.Body(pattern="^x$")]),
    )

    assert _arguments(messages, query=b"page=1", body=b'{"a":"y","b":"x"}') == (1, "y", "x")
    with pytest.raises(HttpError) as caught:
        _arguments(messages, query=b"page=0", body=b'["yy","z"]')
    # A body reports its first problem alone.
    assert _errors(caught.value) == {(("query", "page"), "value_invalid"), (("body", 0), "value_invalid")}


def test_a_message_takes_no_constraints() -> None:
    with pytest.raises(TypeError, match="takes no constraints"):
        _messages(("request", Annotated[StringValue, dubbo.Body(description="x")]))


def test_a_repeated_value_gives_its_last_or_every_value_to_a_list() -> None:
    messages = _messages(("one", int), ("many", Annotated[list[int], dubbo.Query]))

    assert _arguments(messages, query=b"one=1&one=2&many=3&many=4") == (2, [3, 4])


def test_the_query_and_headers_are_read_only_when_a_parameter_takes_them() -> None:
    messages = _messages(("user", User))

    assert not messages.takes_query
    assert not messages.takes_headers
    assert _arguments(messages, query=b"%zz", headers=((b"\xff", b"\xff"),), body=b'{"name":"x"}') == (User("x"),)


def test_the_only_body_parameter_is_the_whole_body() -> None:
    assert _messages(("user", User)).body is BodyKind.WHOLE
    assert _arguments(_messages(("user", User)), body=b'{"name":"x"}') == (User("x"),)
    assert _arguments(_messages(("request", StringValue)), body=b'"hi"') == (StringValue(value="hi"),)
    assert _arguments(_messages(("name", Annotated[str, dubbo.Body])), body=b'"hi"') == ("hi",)


def test_several_body_parameters_are_a_json_object_by_name_or_a_json_array_in_order() -> None:
    messages = _messages(("a", User), ("b", list[int]), ("c", User | None, None))

    assert messages.body is BodyKind.MEMBERS
    assert _arguments(messages, body=b'{"a":{"name":"x"},"b":[1]}') == (User("x"), [1], None)
    assert _arguments(messages, body=b' [{"name":"x"},[1],{"name":"y"}]') == (User("x"), [1], User("y"))


@pytest.mark.parametrize("body", [b'{"name": 1}', b'[{"name": "x"}, "2"]'])
def test_a_body_is_read_strictly_as_json(body: bytes) -> None:
    messages = _messages(("user", User), ("count", Annotated[int, dubbo.Body]))

    with pytest.raises(HttpError) as caught:
        _arguments(messages, body=body)

    assert caught.value.status == 422


def test_form_fields_come_from_a_urlencoded_body() -> None:
    messages = _messages(("name", Annotated[str, dubbo.Form]), ("tags", Annotated[list[str], dubbo.Form]))

    assert messages.body is BodyKind.FORM
    assert _arguments(messages, body=b"name=a+b&tags=x&tags=y") == ("a b", ["x", "y"])


def test_arguments_left_out_take_their_defaults() -> None:
    messages = _messages(("a", int, 3), ("user", User | None, None))

    assert _arguments(messages) == (3, None)


def test_every_wrong_argument_is_reported_with_where_it_came_from() -> None:
    messages = _messages(("a", int), ("b", int), ("user", User), ("tags", list[str]))

    with pytest.raises(HttpError) as caught:
        _arguments(messages, query=b"a=x", body=b'{"user":{}}')

    assert caught.value.status == 422
    assert _errors(caught.value) == {
        (("query", "a"), "value_invalid"),
        (("query", "b"), "missing"),
        (("body", "user"), "value_invalid"),
    }


@pytest.mark.parametrize("body", [b"{", b"[1"])
@pytest.mark.parametrize("parameters", [(("user", User),), (("user", User), ("other", User))], ids=["one", "two"])
def test_a_body_that_is_no_json_is_reported_as_such(parameters, body) -> None:
    with pytest.raises(HttpError) as caught:
        _arguments(_messages(*parameters), body=body)

    # msgspec checks the shape while it parses, so a body cut short may first be of the wrong shape.
    assert caught.value.status == 422
    assert {loc[0] for loc, _ in _errors(caught.value)} == {"body"}


def test_an_array_of_another_length_than_the_body_parameters_is_invalid() -> None:
    with pytest.raises(HttpError) as caught:
        _arguments(_messages(("a", User), ("b", User)), body=b'[{"name":"x"}]')

    assert caught.value.status == 422
    assert {loc[0] for loc, _ in _errors(caught.value)} == {"body"}


@pytest.mark.parametrize(
    ("parameters", "variables", "message"),
    [
        ((("id", Annotated[int, dubbo.Path]),), (), "from path segments"),
        ((("a", int),), ("id",), "which no parameter takes"),
        ((("user", User), ("name", Annotated[str, dubbo.Form])), (), "both a JSON body and a form"),
        ((("request", Annotated[StringValue, dubbo.Query]),), (), "from the body alone"),
        ((("request", StringValue), ("user", User)), (), "from the body alone"),
    ],
    ids=["path-not-in-route", "route-variable-unused", "body-and-form", "message-in-query", "message-member"],
)
def test_a_method_whose_parameters_do_not_fit_its_route_is_refused(parameters, variables, message) -> None:
    with pytest.raises(TypeError, match=message):
        _messages(*parameters, variables=variables)


def test_a_message_and_scalars_from_the_path_and_query_are_bound() -> None:
    messages = _messages(("id", int), ("verbose", bool, False), ("value", StringValue), variables=("id",))

    assert _arguments(messages, path={"id": "3"}, body=b'"x"') == (3, False, StringValue(value="x"))


def test_cookies_come_from_every_cookie_header_field_by_name_or_alias() -> None:
    messages = _messages(
        ("session", Annotated[str, dubbo.Cookie]),
        ("theme", Annotated[str, dubbo.Cookie(alias="ui-theme")]),
        ("lang", Annotated[str, dubbo.Cookie], "en"),
    )

    headers = ((b"cookie", b'session=s1; ui-theme="dark"'), (b"cookie", b"other=1"))
    assert _arguments(messages, headers=headers) == ("s1", "dark", "en")
    headers = ((b"cookie", "session=会话; ui-theme=暗".encode()),)
    assert _arguments(messages, headers=headers) == ("会话", "暗", "en")
    with pytest.raises(HttpError) as caught:
        _arguments(messages)
    assert _errors(caught.value) == {(("cookie", "session"), "missing"), (("cookie", "ui-theme"), "missing")}


class Page(pydantic.BaseModel):
    limit: int = 20
    offset: int = 0
    tags: list[str] = []


class Client(pydantic.BaseModel):
    user_agent: str
    request_id: str = pydantic.Field("", alias="X-Request-Id")


def test_a_model_from_the_query_or_headers_takes_a_value_for_each_of_its_fields() -> None:
    messages = _messages(("page", Annotated[Page, dubbo.Query]), ("client", Annotated[Client, dubbo.Header]))

    headers = ((b"user-agent", b"curl"), (b"x-request-id", b"r1"))
    arguments = _arguments(messages, query=b"limit=10&tags=a&tags=b", headers=headers)

    assert arguments == (Page(limit=10, offset=0, tags=["a", "b"]), Client(user_agent="curl", **{"X-Request-Id": "r1"}))


def test_a_model_reports_what_is_wrong_under_the_place_it_came_from() -> None:
    messages = _messages(("page", Annotated[Page, dubbo.Query]), ("client", Annotated[Client, dubbo.Header]))

    with pytest.raises(HttpError) as caught:
        _arguments(messages, query=b"limit=x")

    (page, client) = caught.value.detail
    assert (page["loc"], client["loc"]) == (["query"], ["header"])
    assert page["msg"].startswith("limit: Input should be a valid integer")
    assert client["msg"] == "user_agent: Field required"


@dataclasses.dataclass
class Window:
    size: Annotated[int, msgspec.Meta(ge=1)]
    tags: list[str] = dataclasses.field(default_factory=list)


def test_a_dataclass_from_the_query_takes_a_value_for_each_of_its_fields() -> None:
    messages = _messages(("window", Annotated[Window, dubbo.Query]))

    assert _arguments(messages, query=b"size=2&tags=a&tags=b") == (Window(2, ["a", "b"]),)
    with pytest.raises(HttpError) as caught:
        _arguments(messages, query=b"size=0")
    assert _errors(caught.value) == {(("query", "size"), "value_invalid")}


@pytest.mark.parametrize(
    ("annotation", "text", "value"),
    [
        (int, "20", 20),
        (float, "2.5", 2.5),
        (bool, "true", True),
        (bool, "0", False),
        (datetime.date, "2026-11-27", datetime.date(2026, 11, 27)),
        (uuid.UUID, "c0ffee00-0000-4000-8000-000000000000", uuid.UUID("c0ffee00-0000-4000-8000-000000000000")),
        (Color, "red", Color.RED),
        (str | None, "null", "null"),
    ],
)
def test_text_becomes_a_value_as_msgspec_converts_it_loosely(annotation: Any, text: str, value: Any) -> None:
    assert _arguments(_messages(("value", annotation)), query=f"value={text}".encode()) == (value,)


@pytest.mark.parametrize(("annotation", "text"), [(bool, "yes"), (int, ""), (int | None, "null")])
def test_text_of_no_value_of_the_type_is_invalid(annotation: Any, text: str) -> None:
    with pytest.raises(HttpError) as caught:
        _arguments(_messages(("value", annotation, None)), query=f"value={text}".encode())

    assert _errors(caught.value) == {(("query", "value"), "value_invalid")}


def test_an_installed_model_converts_its_classes_in_the_body(points) -> None:
    messages = _messages(("points", list[points]))

    assert _arguments(messages, body=b'["1,2"]') == ([points(1, 2)],)
    with pytest.raises(HttpError) as caught:
        _arguments(messages, body=b'["1"]')
    assert caught.value.detail == [
        {"loc": ["body", 0], "msg": "expected two integers apart by a comma", "type": "value_invalid"}
    ]


def test_a_model_without_any_of_its_values_takes_its_default() -> None:
    default = Page(limit=1)
    messages = _messages(("page", Annotated[Page, dubbo.Query], default))

    assert _arguments(messages) == (default,)
