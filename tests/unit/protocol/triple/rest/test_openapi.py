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
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorProto
from pydantic import BaseModel, Field, create_model

import dubbo
from dubbo._proto.org.apache.dubbo.metadata.metadata_service_v2_pb2 import ServiceInfoV2
from dubbo.protocol.served import ServedService
from dubbo.protocol.triple.rest.openapi import OpenAPI
from dubbo.protocol.triple.routes import served_methods
from dubbo.rpc.definition import Service
from dubbo.serialization.json import JsonSerialization
from dubbo.url import URL


def _openapi(query: str) -> OpenAPI | None:
    return OpenAPI.from_url(URL.parse(f"tri://127.0.0.1:1/x?{query}"))


def test_openapi_is_off_by_default() -> None:
    assert _openapi("rest.openapi.path=/docs") is None


def test_the_path_info_and_cdns_come_from_the_url() -> None:
    openapi = _openapi(
        "rest.openapi.enabled=true&rest.openapi.path=/docs/&rest.openapi.info-version=2"
        "&rest.openapi.settings.swagger-ui.cdn=https://cdn.internal/swagger/"
        "&rest.openapi.settings.redoc.cdn=https://cdn.internal/redoc"
    )

    swagger = openapi.respond(b"GET", b"/docs/swagger-ui/")[2]
    redoc = openapi.respond(b"GET", b"/docs/redoc/")[2]

    assert (openapi.path, openapi.info) == ("/docs", {"title": "Dubbo OpenAPI", "version": "2"})
    assert b'href="https://cdn.internal/swagger/swagger-ui.css"' in swagger
    assert b'src="https://cdn.internal/redoc/redoc.standalone.js"' in redoc
    assert openapi.respond(b"GET", b"/elsewhere") is None
    assert json.loads(openapi.respond(b"GET", b"/docs/openapi.json")[2])["paths"] == {}


def test_a_path_must_start_with_a_slash() -> None:
    with pytest.raises(ValueError, match="must start with '/'"):
        _openapi("rest.openapi.enabled=true&rest.openapi.path=docs")


def test_the_default_viewers_load_their_major_versions_that_work_outside_a_module_script() -> None:
    # ReDoc 3 bundles use `import.meta`, which a plain <script> cannot run.
    openapi = _openapi("rest.openapi.enabled=true&rest.openapi.path=/docs")

    swagger_ui = openapi.respond(b"GET", b"/docs/swagger-ui/")[2]
    redoc = openapi.respond(b"GET", b"/docs/redoc/")[2]

    assert b'src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"' in swagger_ui
    assert b'src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"' in redoc


class _Child(BaseModel):
    value: int = Field(validation_alias="inValue", serialization_alias="outValue")


class _Envelope(BaseModel):
    child: _Child


@dubbo.service("test.Documented")
class _Documented:
    @dubbo.post("/echo")
    async def echo(self, envelope: _Envelope) -> _Envelope:
        return envelope

    @dubbo.get("/odd", status_code=299)
    async def odd(self) -> str:
        return ""

    @dubbo.get("/services", body=None)
    async def find(self, service: ServiceInfoV2) -> ServiceInfoV2:
        return service


def _document() -> dict[str, Any]:
    return _document_of(_Documented())


def _document_of(instance: Any) -> dict[str, Any]:
    url = URL.parse("tri://127.0.0.1:1")
    definition = Service.from_implementation(instance)
    service = ServedService(interface=definition.interface, group="", version="", invoker=SimpleNamespace(invoke=None))
    json_serialization = JsonSerialization(url)
    openapi = OpenAPI(models=json_serialization.models, path="/docs", info={"title": "t", "version": "1"})
    served = served_methods(service, definition, json_serialization, json_serialization)
    openapi.update(served.values(), default_mapping=False)
    return json.loads(openapi.document())


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]] if "$ref" in schema else schema


def test_a_model_whose_request_and_response_differ_has_a_schema_for_each() -> None:
    document = _document()
    operation = document["paths"]["/echo"]["post"]

    request = _resolve(document, operation["requestBody"]["content"]["application/json"]["schema"])
    response = _resolve(document, operation["responses"]["200"]["content"]["application/json"]["schema"])

    assert _resolve(document, request["properties"]["child"])["required"] == ["inValue"]
    assert _resolve(document, response["properties"]["child"])["required"] == ["outValue"]


def test_a_status_without_a_registered_phrase_is_described_by_its_number() -> None:
    assert _document()["paths"]["/odd"]["get"]["responses"] == {
        "299": {"description": "Status 299", "content": {"application/json": {"schema": {"type": "string"}}}}
    }


def test_a_map_of_the_request_message_is_a_deep_object_in_the_query() -> None:
    parameters = {each["name"]: each for each in _document()["paths"]["/services"]["get"]["parameters"]}

    assert parameters["params"]["style"] == "deepObject"
    assert parameters["params"]["explode"] is True
    assert "style" not in parameters["name"]


def test_an_operation_that_takes_input_also_answers_422_for_an_invalid_one() -> None:
    document = _document()

    responses = document["paths"]["/echo"]["post"]["responses"]
    error = _resolve(document, responses["422"]["content"]["application/json"]["schema"])
    detail = _resolve(document, error["properties"]["detail"]["items"])

    assert responses["422"]["description"] == "Validation Error"
    assert detail["required"] == ["loc", "msg", "type"]


def test_an_operation_without_input_answers_no_422() -> None:
    assert "422" not in _document()["paths"]["/odd"]["get"]["responses"]


class ValidationError(BaseModel):
    reason: str


class HTTPValidationError(BaseModel):
    code: int


@dubbo.service("test.Clashing")
class _Clashing:
    @dubbo.post("/report")
    async def report(self, error: ValidationError) -> HTTPValidationError:
        return HTTPValidationError(code=1)


def test_a_business_model_named_like_a_422_schema_is_kept_and_the_422_schema_takes_another_name() -> None:
    document = _document_of(_Clashing())
    schemas = document["components"]["schemas"]
    operation = document["paths"]["/report"]["post"]

    business_request = _resolve(document, operation["requestBody"]["content"]["application/json"]["schema"])
    business_response = _resolve(document, operation["responses"]["200"]["content"]["application/json"]["schema"])
    error = _resolve(document, operation["responses"]["422"]["content"]["application/json"]["schema"])
    detail = _resolve(document, error["properties"]["detail"]["items"])

    # The business models keep their names and their own shape, undisturbed by the 422 schemas.
    assert business_request["properties"] == {"reason": {"type": "string", "title": "Reason"}}
    assert business_response["properties"] == {"code": {"type": "integer", "title": "Code"}}
    # The 422 schemas took other, free names, consistently at their component key, title and every $ref to them.
    assert detail["required"] == ["loc", "msg", "type"]
    detail_name = operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
    assert detail_name not in ("ValidationError", "HTTPValidationError")
    assert schemas[detail_name]["title"] == detail_name
    inner_name = error["properties"]["detail"]["items"]["$ref"].rsplit("/", 1)[1]
    assert schemas[inner_name]["title"] == inner_name


@dubbo.service("test.Declared")
class _Declared:
    @dubbo.post("/validated", openapi_extra={"responses": {"422": {"description": "Already answered"}}})
    async def validated(self, envelope: _Envelope) -> _Envelope:
        return envelope


def test_an_openapi_extra_422_extends_the_automatic_one_rather_than_replacing_it() -> None:
    # openapi_extra is merged in after the automatic 422 is decided, so it can override or add fields to it, but an
    # operation that takes input still answers 422 with the shared error schema even where openapi_extra mentions it.
    document = _document_of(_Declared())
    response = document["paths"]["/validated"]["post"]["responses"]["422"]

    assert response["description"] == "Already answered"
    assert response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1] == "HTTPValidationError"


CatalogItem = create_model("Item", __module__="catalog", sku=(str, ...))
InventoryItem = create_model("Item", __module__="inventory", quantity=(int, ...))


@dubbo.service("test.Stock")
class _Stock:
    @dubbo.post("/catalog")
    async def catalog(self, item: CatalogItem) -> CatalogItem:
        return item

    @dubbo.post("/inventory")
    async def inventory(self, item: InventoryItem) -> InventoryItem:
        return item

    @dubbo.post("/files")
    async def files(self, file: FileDescriptorProto) -> FileDescriptorProto:
        return file


def test_models_of_one_name_from_two_modules_each_keep_their_own_schema() -> None:
    document = _document_of(_Stock())

    def fields(path: str) -> set[str]:
        operation = document["paths"][path]["post"]
        request = _resolve(document, operation["requestBody"]["content"]["application/json"]["schema"])
        response = _resolve(document, operation["responses"]["200"]["content"]["application/json"]["schema"])
        assert request == response
        return set(request["properties"])

    assert (fields("/catalog"), fields("/inventory")) == ({"sku"}, {"quantity"})
    assert "name" in fields("/files")


@dubbo.service("test.Rejecting")
class _Rejecting:
    @dubbo.post("/rejected", status_code=422)
    async def rejected(self, envelope: _Envelope) -> _Child:
        return envelope.child


def test_an_operation_that_answers_422_itself_is_described_by_its_own_response() -> None:
    document = _document_of(_Rejecting())
    responses = document["paths"]["/rejected"]["post"]["responses"]

    assert list(responses) == ["422"]
    assert set(_resolve(document, responses["422"]["content"]["application/json"]["schema"])["properties"]) == {
        "outValue"
    }


class _Page(BaseModel):
    limit: int = Field(20, description="items at most")
    cursor: str


class _NotFound(BaseModel):
    missing: str


@dubbo.service("test.Listing")
class _Listing:
    @dubbo.get("/items", responses={404: {"model": _NotFound}, "4xx": {"description": "Refused"}})
    async def items(
        self,
        page: Annotated[_Page, dubbo.Query],
        session: Annotated[str, dubbo.Cookie],
        theme: Annotated[str, dubbo.Cookie] = "light",
    ) -> list[str]:
        return []


def test_a_model_of_query_parameters_and_cookies_are_described_parameter_by_parameter() -> None:
    document = _document_of(_Listing())
    parameters = document["paths"]["/items"]["get"]["parameters"]

    described = {(each["in"], each["name"]): (each["required"], each.get("description")) for each in parameters}
    assert described == {
        ("query", "limit"): (False, "items at most"),
        ("query", "cursor"): (True, None),
        ("cookie", "session"): (True, None),
        ("cookie", "theme"): (False, None),
    }
    assert next(each for each in parameters if each["name"] == "limit")["schema"]["type"] == "integer"


def test_declared_responses_are_described_and_a_4xx_takes_the_place_of_the_automatic_422() -> None:
    document = _document_of(_Listing())
    responses = document["paths"]["/items"]["get"]["responses"]

    assert set(responses) == {"200", "404", "4XX"}
    assert responses["4XX"] == {"description": "Refused"}
    assert responses["404"]["description"] == "Not Found"
    assert set(_resolve(document, responses["404"]["content"]["application/json"]["schema"])["properties"]) == {
        "missing"
    }


def test_a_response_for_no_status_is_refused() -> None:
    with pytest.raises(ValueError, match="for a status"):
        dubbo.get("/x", responses={"6XX": {}})


def test_servers_security_and_swagger_ui_parameters_come_from_the_url() -> None:
    openapi = _openapi(
        "rest.openapi.enabled=true&rest.openapi.servers=https://api.example/v1, https://backup.example"
        '&rest.openapi.security-schemes={"token": {"type": "apiKey", "in": "header", "name": "token"}}'
        '&rest.openapi.security=[{"token": []}]'
        "&rest.openapi.settings.swagger-ui.persistAuthorization=true"
        "&rest.openapi.settings.swagger-ui.docExpansion=none"
    )

    document = json.loads(openapi.respond(b"GET", b"/dubbo/openapi/openapi.json")[2])
    page = openapi.respond(b"GET", b"/dubbo/openapi/swagger-ui/")[2]

    assert document["servers"] == [{"url": "https://api.example/v1"}, {"url": "https://backup.example"}]
    assert document["components"]["securitySchemes"] == {"token": {"type": "apiKey", "in": "header", "name": "token"}}
    assert document["security"] == [{"token": []}]
    assert b'"persistAuthorization":true' in page
    assert b'"docExpansion":"none"' in page
    assert b'"url":"../api-docs/default"' in page


@pytest.mark.parametrize("setting", ["security-schemes=[1]", "security={}", "security=not-json"])
def test_security_settings_that_are_not_json_of_their_shape_are_refused(setting) -> None:
    with pytest.raises(ValueError, match="must be"):
        _openapi(f"rest.openapi.enabled=true&rest.openapi.{setting}")
