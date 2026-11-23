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

"""The OpenAPI operation object of one route of a method and one HTTP method."""

import inspect
from collections.abc import Iterable, Mapping
from http import HTTPStatus
from typing import Any

from google.protobuf.descriptor import FieldDescriptor

from dubbo.rpc.params import ParamLocation
from dubbo.serialization.models.protobuf import is_well_known
from dubbo.transport.http.headers import BODILESS_STATUSES
from dubbo.utils.annotations import unannotated

from ...routes import ServedMethod, ServedRoute
from ..messages import BodyKind, RestMessages
from ..transcoding import TranscodedMessages
from .schemas import Schemas

__all__ = ["merge", "operation"]

_EMPTY = inspect.Parameter.empty
# The responses that, once an operation describes one, take the place of the automatic 422.
_CLIENT_ERRORS = frozenset(("422", "4XX", "default"))

Parameters = list[dict[str, Any]]


def operation(
    served: ServedMethod,
    rest: ServedRoute,
    http_method: str,
    schemas: Schemas,
    taken: set[str],
    validation_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the operation object of ``rest`` with ``http_method``, its schemas asked of ``schemas``.

    ``taken`` holds the operation IDs of the document so far. An operation
    that takes input answers 422 for an invalid one, unless it describes a
    422, 4XX or default response of its own, as FastAPI does; that response
    goes into ``validation_errors``, whose schema the document names last.
    """
    method, route, interface = served.method, rest.route, served.service.interface
    summary, _, description = (method.doc or "").partition("\n")
    result: dict[str, Any] = {"tags": list(route.tags) or [interface]}
    if summary := route.summary if route.summary is not None else summary.strip():
        result["summary"] = summary
    if description := route.description if route.description is not None else description.strip():
        result["description"] = description
    result["operationId"] = _operation_id(route.operation_id or f"{interface}.{method.name}", http_method, taken)

    messages = rest.messages
    if isinstance(messages, TranscodedMessages):
        parameters, body = _message_request(messages, schemas)
    else:
        assert isinstance(messages, RestMessages)
        parameters, body = _request(messages, schemas)
    if parameters:
        result["parameters"] = parameters
    if body is not None:
        result["requestBody"] = body

    status = route.status_code
    response: dict[str, Any] = {"description": _phrase(status)}
    if status not in BODILESS_STATUSES and method.response is not None:
        response["content"] = {"application/json": {"schema": _response_schema(method.response, rest, schemas)}}
    responses = {str(status): response}
    for code, declared in (route.responses or {}).items():
        responses[code] = _declared_response(code, declared, schemas)
    if (parameters or body is not None) and not _CLIENT_ERRORS & responses.keys():
        error: dict[str, Any] = {"description": "Validation Error"}
        responses["422"] = error
        validation_errors.append(error)
    result["responses"] = responses
    if route.deprecated or method.deprecated:
        result["deprecated"] = True
    return result


def merge(target: dict[str, Any], extra: Mapping[str, Any]) -> None:
    """Merge ``extra`` into ``target``, object by object, its values replacing the others."""
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = value


def _operation_id(base: str, http_method: str, taken: set[str]) -> str:
    """Return ``base``, or with a suffix once another route or method of the document took it."""
    operation_id, number = base, 1
    while operation_id in taken:
        number += 1
        operation_id = f"{base}_{http_method.lower()}" if number == 2 else f"{base}_{http_method.lower()}_{number}"
    taken.add(operation_id)
    return operation_id


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    # A status without a registered phrase, such as 299, is described by its number.
    except ValueError:
        return f"Status {status}"


def _request(messages: RestMessages, schemas: Schemas) -> tuple[Parameters, dict[str, Any] | None]:
    """Return the parameters and the request body of an operation whose arguments travel as ``messages`` tells."""
    parameters: Parameters = []
    form: dict[str, Any] = {}
    form_required: list[str] = []
    body = None
    for parameter in messages.parameters:
        param = parameter.param
        if param is not None and not param.include_in_schema:
            continue
        required = parameter.location is ParamLocation.PATH or parameter.default is _EMPTY
        if parameter.fields is not None:
            # A model takes a value of its place for each of its fields, which are described one by one.
            for gathered in parameter.fields:
                field = gathered.field
                schema = schemas.of(field.type, "input")
                if parameter.location is ParamLocation.FORM:
                    form[gathered.key] = schema
                    if field.required and required:
                        form_required.append(gathered.key)
                    continue
                entry = {
                    "name": gathered.key,
                    "in": parameter.location.value,
                    "required": field.required and required,
                    "schema": schema,
                }
                if field.description is not None:
                    entry["description"] = field.description
                parameters.append(entry)
            continue
        schema = schemas.of(parameter.type, "input")
        match parameter.location:
            case ParamLocation.PATH | ParamLocation.QUERY | ParamLocation.HEADER | ParamLocation.COOKIE:
                entry = {
                    "name": parameter.key,
                    "in": parameter.location.value,
                    "required": required,
                    "schema": schema,
                }
                if param is not None and param.description is not None:
                    entry["description"] = param.description
                if param is not None and param.deprecated:
                    entry["deprecated"] = True
                parameters.append(entry)
            case ParamLocation.FORM:
                form[parameter.key] = schema
                if required:
                    form_required.append(parameter.key)
            case _ if messages.body is BodyKind.WHOLE:
                body = {"required": required, "content": {"application/json": {"schema": schema}}}
    if messages.members is not None:
        # A body of several parameters is an object of their names; an array of them in order is read too.
        schema = schemas.of(messages.members.object, "input")
        body = {"required": True, "content": {"application/json": {"schema": schema}}}
    if form:
        schema = {"type": "object", "properties": form}
        if form_required:
            schema["required"] = form_required
        body = {"required": bool(form_required), "content": {"application/x-www-form-urlencoded": {"schema": schema}}}
    return parameters, body


def _message_request(messages: TranscodedMessages, schemas: Schemas) -> tuple[Parameters, dict[str, Any] | None]:
    """Return the parameters and the request body of an operation whose request message travels as ``messages`` tells."""
    parameters: Parameters = [
        {"name": name, "in": "path", "required": True, "schema": schemas.of(fields[-1], "input")}
        for name, fields in messages.variables
    ]
    body = None
    if messages.body is BodyKind.WHOLE:
        field = messages.body_field
        schema = schemas.of(messages.message if field is None else field, "input")
        body = {"content": {"application/json": {"schema": schema}}}
    # A body of the whole message leaves no field to the query.
    if messages.body is BodyKind.NONE or messages.body_field is not None:
        bound = {".".join(field.name for field in fields) for _, fields in messages.variables}
        skipped = messages.body_field.name if messages.body_field is not None else None
        for name, field in _query_fields(messages.message.DESCRIPTOR, "", set()):
            if name not in bound and name.split(".")[0] != skipped:
                parameter = {
                    "name": name,
                    "in": "query",
                    "required": False,
                    "schema": schemas.of(field, "input"),
                }
                # A map takes one parameter `name[key]` for each entry.
                if field.message_type is not None and field.message_type.GetOptions().map_entry:
                    parameter.update(style="deepObject", explode=True)
                parameters.append(parameter)
    return parameters, body


def _declared_response(code: str, declared: Mapping[str, Any], schemas: Schemas) -> dict[str, Any]:
    """Return the response object of a further response a route declares; its ``model`` gives the JSON's schema."""
    response = {key: value for key, value in declared.items() if key != "model"}
    if "model" in declared:
        content = response.setdefault("content", {}).setdefault("application/json", {})
        content["schema"] = schemas.of(declared["model"], "output")
    if "description" not in response:
        response["description"] = _range_phrase(code)
    return response


def _range_phrase(code: str) -> str:
    """Return the description of a response for ``code``, such as ``404``, ``4XX`` or ``default``."""
    if code == "default":
        return "Default Response"
    if code.endswith("XX"):
        return {"1": "Information", "2": "Success", "3": "Redirection", "4": "Client Error", "5": "Server Error"}[
            code[0]
        ]
    return _phrase(int(code))


def _query_fields(descriptor: Any, prefix: str, seen: set[str]) -> Iterable[tuple[str, FieldDescriptor]]:
    """Yield the fields a query parameter can set, by their dotted proto names, through singular messages."""
    seen = seen | {descriptor.full_name}
    for field in descriptor.fields:
        name = prefix + field.name
        message = field.message_type
        # A singular message of fields of its own is named field by field, once on each path.
        if message is not None and not field.is_repeated and not is_well_known(message):
            if message.full_name not in seen:
                yield from _query_fields(message, name + ".", seen)
        else:
            yield name, field


def _response_schema(response: Any, rest: ServedRoute, schemas: Schemas) -> dict[str, Any]:
    """Return the schema of the JSON a method returning ``response`` answers with on ``rest``."""
    response_body = rest.route.response_body
    if response_body is not None:
        return schemas.of(unannotated(response).DESCRIPTOR.fields_by_name[response_body], "output")
    return schemas.of(response, "output")
