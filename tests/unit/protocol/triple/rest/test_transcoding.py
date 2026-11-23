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

import json
from typing import Annotated

import pytest
from google.protobuf.descriptor_pb2 import FieldDescriptorProto, FieldOptions, FileDescriptorProto, FileOptions
from google.protobuf.wrappers_pb2 import StringValue

import dubbo
from dubbo._proto.org.apache.dubbo.metadata.metadata_service_v2_pb2 import ServiceInfoV2
from dubbo.protocol.triple.rest.exceptions import HttpError
from dubbo.protocol.triple.rest.messages import BodyKind, RestMessages
from dubbo.protocol.triple.rest.transcoding import TranscodedMessages, route_messages
from dubbo.rpc.definition import Method, Parameter
from dubbo.rpc.rest import HttpRoute
from dubbo.serialization.json import JsonSerialization
from dubbo.url import URL

_JSON = JsonSerialization(URL(protocol="tri"))


def _messages(path: str, request=FieldDescriptorProto, response=FieldDescriptorProto, **options):
    method = Method(name="m", request=(Parameter(name="request", type=request),), response=response)
    return route_messages(method, HttpRoute(path=path, methods=["POST"], **options), _JSON)


def _errors(error: HttpError) -> set[tuple[tuple, str]]:
    return {(tuple(item["loc"]), item["type"]) for item in error.detail}


def test_fields_come_from_the_path_and_the_query_without_a_body() -> None:
    messages = _messages("/fields/{name}/{options.deprecated}", body=None)
    query = b"number=3&label=LABEL_REPEATED&proto3Optional=true&type_name=t&options.lazy=1&unknown=1"

    (message,) = messages.arguments({"name": "f", "options.deprecated": "false"}, query, (), b"")

    assert isinstance(messages, TranscodedMessages)
    assert messages.body is BodyKind.NONE
    assert message == FieldDescriptorProto(
        name="f",
        number=3,
        label=FieldDescriptorProto.LABEL_REPEATED,
        proto3_optional=True,
        type_name="t",
        options=FieldOptions(deprecated=False, lazy=True),
    )


def test_the_body_holds_one_field_and_the_query_the_others() -> None:
    messages = _messages("/files/{name}", request=FileDescriptorProto, body="options")
    query = b"package=p&dependency=a&dependency=b&options.java_package=ignored&name=ignored"

    (message,) = messages.arguments({"name": "f.proto"}, query, (), b'{"javaPackage": "j"}')

    assert message == FileDescriptorProto(
        name="f.proto", package="p", dependency=["a", "b"], options=FileOptions(java_package="j")
    )


def test_a_body_of_the_whole_message_leaves_nothing_to_the_query_and_yields_to_the_path() -> None:
    messages = _messages("/fields/{name}/{oneof_index}")

    (message,) = messages.arguments(
        {"name": "a", "oneof_index": "2"}, b"number=9", (), b'{"name": "b", "oneofIndex": 1, "number": 4}'
    )

    assert message == FieldDescriptorProto(name="a", oneof_index=2, number=4)


def test_a_map_takes_one_query_parameter_for_each_entry() -> None:
    messages = _messages("/services/{name}", request=ServiceInfoV2, response=ServiceInfoV2, body=None)
    query = b"params%5Bside%5D=provider&params%5Bweight%5D=5&name%5Bx%5D=ignored&params%5Bopen=ignored"

    (message,) = messages.arguments({"name": "s"}, query, (), b"")

    assert message == ServiceInfoV2(name="s", params={"side": "provider", "weight": "5"})


def test_a_well_known_type_of_another_json_than_an_object_is_the_whole_body() -> None:
    (message,) = _messages("/say", request=StringValue).arguments({}, b"", (), b'"hi"')

    assert message == StringValue(value="hi")


@pytest.mark.parametrize(
    ("path", "query", "body", "status", "errors"),
    [
        ({"name": "f", "number": "x"}, b"", b"", 422, {(("path", "number"), "value_invalid")}),
        (
            {"name": "f", "number": "1"},
            b"label=NOPE&proto3_optional=maybe",
            b"",
            422,
            {
                (("query", "label"), "value_invalid"),
                (("query", "proto3_optional"), "value_invalid"),
            },
        ),
        ({"name": "f", "number": "1"}, b"", b'{"options": {"deprecated": 1}}', 422, {(("body",), "value_invalid")}),
        ({"name": "f", "number": "1"}, b"", b"[]", 422, {(("body",), "value_invalid")}),
        ({"name": "f", "number": "1"}, b"", b"{", 422, {(("body",), "json_invalid")}),
    ],
)
def test_what_is_wrong_is_reported_where_it_is(path, query, body, status, errors) -> None:
    messages = _messages("/fields/{name}/{number}")
    if not body:
        messages = _messages("/fields/{name}/{number}", body=None)

    with pytest.raises(HttpError) as raised:
        messages.arguments(path, query, (), body)

    assert raised.value.status == status
    assert _errors(raised.value) == errors


@pytest.mark.parametrize(
    ("path", "options", "match"),
    [
        ("/{nope}", {}, "no field of the request"),
        ("/{options.nope}", {}, "no field of the request"),
        ("/{options}", {}, "not a single value of a scalar type"),
        ("/{name}", {"body": "nope"}, "has no field of"),
        ("/{name}", {"response_body": "nope"}, "has no field of"),
    ],
)
def test_the_route_must_name_fields_of_the_messages(path, options, match) -> None:
    with pytest.raises(TypeError, match=match):
        _messages(path, **options)


def test_the_response_body_is_one_field_of_the_returned_message() -> None:
    message = FieldDescriptorProto(options=FieldOptions(deprecated=True))

    picked = _messages("/f", response_body="options").dump_response(message)
    unset = _messages("/f", response_body="options").dump_response(FieldDescriptorProto())
    scalar = _messages("/f", response_body="number").dump_response(FieldDescriptorProto())
    listed = _messages("/f", request=FileDescriptorProto, response=FileDescriptorProto, response_body="dependency")

    assert (json.loads(picked), json.loads(unset), json.loads(scalar)) == ({"deprecated": True}, {}, 0)
    assert json.loads(listed.dump_response(FileDescriptorProto())) == []


def test_a_method_of_other_parameters_takes_the_whole_body_alone() -> None:
    method = Method(name="m", request=(Parameter(name="n", type=int),), response=FieldDescriptorProto)

    messages = route_messages(method, HttpRoute(path="/m", methods=["GET"], response_body="number"), _JSON)

    assert isinstance(messages, RestMessages)
    assert json.loads(messages.dump_response(FieldDescriptorProto(number=5))) == 5
    with pytest.raises(TypeError, match="body applies to"):
        route_messages(method, HttpRoute(path="/m", methods=["GET"], body=None), _JSON)


def test_a_message_marked_for_the_query_is_not_transcoded() -> None:
    method = Method(
        name="m", request=(Parameter(name="r", type=Annotated[StringValue, dubbo.Query]),), response=StringValue
    )

    with pytest.raises(TypeError, match="from the body alone"):
        route_messages(method, HttpRoute(path="/m", methods=["GET"]), _JSON)
