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

import sys
import types
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2, wrappers_pb2
from google.protobuf.compiler.plugin_pb2 import CodeGeneratorRequest

from dubbo.protoc import generate
from dubbo.rpc.definition import RpcType, get_contract_methods, get_definition
from dubbo.rpc.rest import HttpRoute

STRING = ".google.protobuf.StringValue"


GOLDEN = Path(__file__).with_name("data").joinpath("greeter_v1_dubbo.py.txt").read_text()


def _request(*methods: dict[str, object]) -> CodeGeneratorRequest:
    wrappers = descriptor_pb2.FileDescriptorProto()
    wrappers_pb2.DESCRIPTOR.CopyToProto(wrappers)
    greeter = descriptor_pb2.FileDescriptorProto(
        name="demo/greeter-v1.proto", package="demo", dependency=["google/protobuf/wrappers.proto"]
    )
    greeter.message_type.add(name="Outer").nested_type.add(name="Inner")
    service = greeter.service.add(name="Greeter")
    for method in methods:
        service.method.add(**method)
    greeter.service.add(name="Empty")
    greeter.source_code_info.location.add(path=[6, 0], leading_comments=" Greets people.\n")
    greeter.source_code_info.location.add(
        path=[6, 0, 2, 0], leading_comments=' Says hello.\n\n Says it "twice": \\ """ .\n'
    )
    return CodeGeneratorRequest(file_to_generate=[greeter.name], proto_file=[wrappers, greeter])


METHODS = (
    {"name": "SayHello", "input_type": STRING, "output_type": ".demo.Outer.Inner"},
    {"name": "LotsOfReplies", "input_type": STRING, "output_type": STRING, "server_streaming": True},
    {"name": "LotsOfGreetings", "input_type": STRING, "output_type": STRING, "client_streaming": True},
    {
        "name": "BidiHello",
        "input_type": STRING,
        "output_type": STRING,
        "client_streaming": True,
        "server_streaming": True,
    },
    {"name": "GetHTTPStatus", "input_type": STRING, "output_type": STRING},
    {"name": "Import", "input_type": STRING, "output_type": STRING},
)


def test_a_service_becomes_a_contract_with_python_names_and_the_pb2_messages():
    response = generate(_request(*METHODS))

    assert not response.error
    assert [(file.name, file.content) for file in response.file] == [("demo/greeter_v1_dubbo.py", GOLDEN)]


def test_the_generated_contract_defines_the_service(monkeypatch):
    module = types.ModuleType("demo.greeter_v1_pb2")
    module.Outer = type("Outer", (), {"Inner": wrappers_pb2.StringValue})
    package = types.ModuleType("demo")
    package.greeter_v1_pb2 = module
    monkeypatch.setitem(sys.modules, "demo", package)
    monkeypatch.setitem(sys.modules, "demo.greeter_v1_pb2", module)
    namespace: dict[str, object] = {}

    exec(generate(_request(*METHODS)).file[0].content, namespace)

    definition = get_definition(namespace["Greeter"])
    assert definition.interface == "demo.Greeter"
    assert [(method.name, method.rpc_type) for method in definition.methods] == [
        ("SayHello", RpcType.UNARY),
        ("LotsOfReplies", RpcType.SERVER_STREAM),
        ("LotsOfGreetings", RpcType.CLIENT_STREAM),
        ("BidiHello", RpcType.BIDI_STREAM),
        ("GetHTTPStatus", RpcType.UNARY),
        ("Import", RpcType.UNARY),
    ]
    assert list(get_contract_methods(namespace["Greeter"]))[-1] == "import_"
    assert get_definition(namespace["GreeterServicer"]) == definition
    assert definition.method("SayHello").response is wrappers_pb2.StringValue


def test_a_message_named_with_a_keyword_and_a_comment_ending_in_a_quote_generate_valid_python():
    request = _request({"name": "Echo", "input_type": ".demo.Outer.class", "output_type": ".demo.class"})
    greeter = request.proto_file[1]
    greeter.message_type[0].nested_type.add(name="class")
    greeter.message_type.add(name="class")
    greeter.source_code_info.location.add(path=[6, 0, 2, 0], leading_comments=' Returns "ready"\n')

    content = generate(request).file[0].content

    compile(content, "demo/greeter_v1_dubbo.py", "exec")
    assert 'getattr(getattr(greeter_v1_pb2, "Outer"), "class")' not in content
    assert 'getattr(greeter_v1_pb2.Outer, "class")' in content
    assert 'getattr(greeter_v1_pb2, "class")' in content
    assert '"""Returns "ready\\""""' in content


def test_pb2_modules_with_the_same_basename_keep_their_message_types_distinct(monkeypatch):
    generated = _request({"name": "Copy", "input_type": STRING, "output_type": ".other.Value"})
    other = descriptor_pb2.FileDescriptorProto(name="other/wrappers.proto", package="other")
    other.message_type.add(name="Value")
    generated.proto_file.insert(0, other)
    module = types.ModuleType("other.wrappers_pb2")
    module.Value = wrappers_pb2.Int64Value
    package = types.ModuleType("other")
    package.wrappers_pb2 = module
    monkeypatch.setitem(sys.modules, "other", package)
    monkeypatch.setitem(sys.modules, "other.wrappers_pb2", module)
    namespace = {}

    exec(generate(generated).file[0].content, namespace)
    method = get_definition(namespace["Greeter"]).method("Copy")

    assert method.request[0].type is wrappers_pb2.StringValue
    assert method.response is wrappers_pb2.Int64Value


@pytest.mark.parametrize(
    ("names", "error"),
    [
        (["GetUser", "get_user"], "methods of Greeter share the Python name 'get_user'"),
        (["_Hidden"], "method Greeter._Hidden would be private in Python"),
        (["Dubbo"], "method Greeter.Dubbo would hide the imported name 'dubbo'"),
        (["Wrappers_pb2"], "method Greeter.Wrappers_pb2 would hide the imported name 'wrappers_pb2'"),
    ],
)
def test_a_service_that_cannot_be_a_contract_sets_the_error(names, error):
    response = generate(_request(*({"name": name, "input_type": STRING, "output_type": STRING} for name in names)))

    assert response.error == f"demo/greeter-v1.proto: {error}"
    assert not response.file


def test_a_service_named_as_an_import_sets_the_error():
    request = _request({"name": "SayHello", "input_type": STRING, "output_type": STRING})
    request.proto_file[1].service[0].name = "typing"

    assert generate(request).error == "demo/greeter-v1.proto: a contract would hide the imported name 'typing'"


def test_a_file_named_with_dots_generates_beside_its_pb2_module():
    request = _request({"name": "SayHello", "input_type": STRING, "output_type": STRING})
    request.proto_file[1].name = request.file_to_generate[0] = "demo/greeter.v1.proto"

    assert [file.name for file in generate(request).file] == ["demo/greeter/v1_dubbo.py"]


def _annotated(*methods: tuple[str, dict[str, object], dict[str, object]]) -> CodeGeneratorRequest:
    """Return a request whose Greeter has ``methods``, each its name, its fields and its google.api.http rule."""
    http_pb2 = pytest.importorskip("google.api.http_pb2")
    annotations_pb2 = pytest.importorskip("google.api.annotations_pb2")
    base = _request()
    files = []
    for module in (descriptor_pb2, http_pb2, annotations_pb2):
        file = descriptor_pb2.FileDescriptorProto()
        module.DESCRIPTOR.CopyToProto(file)
        files.append(file)
    request = CodeGeneratorRequest(file_to_generate=base.file_to_generate, proto_file=[*files, *base.proto_file])
    greeter = request.proto_file[-1]
    greeter.dependency.append("google/api/annotations.proto")
    for name, fields, rule in methods:
        method = greeter.service[0].method.add(name=name, input_type=STRING, output_type=STRING, **fields)
        if rule:
            method.options.Extensions[annotations_pb2.http].CopyFrom(http_pb2.HttpRule(**rule))
    return request


def _exec(request: CodeGeneratorRequest) -> dict[str, object]:
    response = generate(request)
    assert not response.error
    namespace: dict[str, object] = {}
    exec(response.file[0].content, namespace)
    return namespace


def test_a_google_api_http_rule_becomes_the_routes_of_its_method():
    namespace = _exec(
        _annotated(
            (
                "Get",
                {},
                {
                    "get": "/v1/{value}",
                    "additional_bindings": [
                        {"post": "/v1/values", "body": "*", "response_body": "value"},
                        {"custom": {"kind": "head", "path": "/v1/{value=*}/check"}},
                        {"patch": "/v1/{value}/x", "body": "value"},
                    ],
                },
            ),
            ("Plain", {}, {}),
            ("Watch", {"server_streaming": True}, {"get": "/v1/watch"}),
        )
    )

    definition = get_definition(namespace["GreeterServicer"])
    assert definition == get_definition(namespace["Greeter"])
    assert definition.method("Get").http_routes == (
        HttpRoute(path="/v1/{value}", methods=["GET"], body=None),
        HttpRoute(path="/v1/values", methods=["POST"], response_body="value"),
        HttpRoute(path="/v1/{value}/check", methods=["HEAD"], body=None),
        HttpRoute(path="/v1/{value}/x", methods=["PATCH"], body="value"),
    )
    assert definition.method("Plain").http_routes == ()
    assert definition.method("Watch").http_routes == ()


@pytest.mark.parametrize(
    ("rule", "error"),
    [
        ({"get": "/v1/{name=shelves/*}"}, "a route takes one segment alone"),
        ({"get": "/v1/{name=**}"}, "a route takes one segment alone"),
        ({"get": "/v1/*/x"}, "a wildcard outside a variable"),
        ({"post": "/v1/{name}:cancel"}, "ends with a :verb"),
        ({"get": "/v1/{a-b}"}, "is no field path"),
        ({"get": "v1"}, "must start with '/'"),
        ({"get": "/v1", "additional_bindings": [{"get": "/a", "additional_bindings": [{"get": "/b"}]}]}, "of its own"),
        ({"body": "*"}, "names no HTTP method and path"),
    ],
)
def test_a_rule_a_route_cannot_take_sets_the_error(rule, error):
    response = generate(_annotated(("Get", {}, rule)))

    assert response.error.startswith("demo/greeter-v1.proto: the google.api.http rule of Greeter.Get: ")
    assert error in response.error


def test_a_deprecated_method_is_marked_as_pep_702_tells():
    request = _annotated(("Old", {"options": descriptor_pb2.MethodOptions(deprecated=True)}, {}), ("New", {}, {}))
    content = generate(request).file[0].content

    namespace = _exec(request)

    assert "\nfrom typing_extensions import deprecated\n" in content
    assert namespace["Greeter"].old.__deprecated__ == "demo.Greeter.Old is deprecated"
    assert namespace["GreeterServicer"].old.__isabstractmethod__
    assert not hasattr(namespace["Greeter"].new, "__deprecated__")
    assert "import sys" not in GOLDEN


def test_a_package_or_service_named_with_a_keyword_generates_valid_python(monkeypatch):
    catalog = descriptor_pb2.FileDescriptorProto(name="from/catalog.proto", package="shop")
    catalog.message_type.add(name="Item")
    service = catalog.service.add(name="class")
    service.method.add(name="Get", input_type=".shop.Item", output_type=".shop.Item")
    request = CodeGeneratorRequest(file_to_generate=[catalog.name], proto_file=[catalog])
    module = types.ModuleType("from.catalog_pb2")
    module.Item = wrappers_pb2.StringValue
    monkeypatch.setitem(sys.modules, "from", types.ModuleType("from"))
    monkeypatch.setitem(sys.modules, "from.catalog_pb2", module)

    content = generate(request).file[0].content
    namespace: dict[str, object] = {}
    exec(compile(content, "from/catalog_dubbo.py", "exec"), namespace)

    assert 'catalog_pb2 = importlib.import_module("from.catalog_pb2")' in content
    assert get_definition(namespace["class_"]).interface == "shop.class"
    assert get_definition(namespace["classServicer"]) == get_definition(namespace["class_"])
