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

"""The OpenAPI 3.1 document of the methods served over plain HTTP, and the pages that show it."""

import re
from collections.abc import Iterable, Mapping
from http import HTTPStatus
from typing import Any

import attrs

from dubbo.constants import (
    OPENAPI_ENABLED_KEY,
    OPENAPI_INFO_DESCRIPTION_KEY,
    OPENAPI_INFO_TITLE_KEY,
    OPENAPI_INFO_VERSION_KEY,
    OPENAPI_KEY,
    OPENAPI_PATH_KEY,
    OPENAPI_REDOC_CDN_KEY,
    OPENAPI_SECURITY_KEY,
    OPENAPI_SECURITY_SCHEMES_KEY,
    OPENAPI_SERVERS_KEY,
    OPENAPI_SETTINGS_KEY,
    OPENAPI_SWAGGER_UI_CDN_KEY,
    OPENAPI_SWAGGER_UI_KEY,
    REST_KEY,
)
from dubbo.serialization.models import Models
from dubbo.transport.http.headers import HttpHeader
from dubbo.url import URL
from dubbo.utils.converters import boolean
from dubbo.utils.json import dump_json, load_json

from ...routes import ServedMethod, ServedRoute
from ...streams import Headers
from . import docs
from .operation import merge, operation
from .schemas import REF, Schemas

__all__ = ["OpenAPI"]

_PREFIX = f"{REST_KEY}.{OPENAPI_KEY}."
_SETTINGS = f"{_PREFIX}{OPENAPI_SETTINGS_KEY}."
_DEFAULT_PATH = "/dubbo/openapi"
_DEFAULT_TITLE = "Dubbo OpenAPI"
_DEFAULT_VERSION = "1.0.0"
# The one group of operations, which holds them all.
_DOCUMENT_GROUP = "default"
# The names the schemas of a 422 take, unless a model of the document took them.
_VALIDATION_ERROR = "ValidationError"
_HTTP_VALIDATION_ERROR = "HTTPValidationError"
# {name:path} takes the rest of the path, which an OpenAPI path template writes as {name}.
_CATCH_ALL = re.compile(r"\{([^{}:]+):path\}")
_JSON = (HttpHeader.CONTENT_TYPE, b"application/json")
_HTML = (HttpHeader.CONTENT_TYPE, b"text/html; charset=utf-8")


@attrs.define(kw_only=True)
class OpenAPI:
    """Serves the OpenAPI 3.1 document of the methods served over plain HTTP, under ``path``.

    ``{path}/api-docs/default``, also with ``.json``, ``{path}/api-docs``
    and ``{path}/openapi.json`` are the document; ``{path}/swagger-ui/`` and
    ``{path}/redoc/`` show it with Swagger UI and ReDoc, loaded from their
    CDNs; ``{path}`` itself redirects to Swagger UI. Each takes ``GET`` and
    ``HEAD`` alone.

    The document lists each route, and each method without one on
    ``/{interface}/{method}`` with ``POST``, but none that plain HTTP cannot
    carry or that a route leaves out of the schema. An operation takes its
    summary and description from the route, else from the docstring of the
    contract method; its tags from the route, else the interface; and is
    deprecated when the route or the method is. Python types have the JSON
    Schema of msgspec, with the classes of ``models`` as those describe
    them, and protobuf messages that of the proto3 JSON mapping; their
    definitions are named once for the whole document.

    Attributes:
        models: The installed models, which describe their classes.
        path: The path the document and its viewers are served under.
        info: The ``info`` object of the document.
        servers: The URLs the API is reached at; none for the URL the
            document is read from.
        security_schemes: The security schemes of the document by name.
        security: The security requirements of every operation.
        swagger_ui_cdn: Where Swagger UI is loaded from.
        swagger_ui_parameters: The parameters of Swagger UI by name.
        redoc_cdn: Where ReDoc is loaded from.
    """

    models: Models
    path: str
    info: dict[str, str]
    servers: tuple[str, ...] = ()
    security_schemes: dict[str, Any] = attrs.field(factory=dict)
    security: tuple[Any, ...] = ()
    swagger_ui_cdn: str = docs.SWAGGER_UI_CDN
    swagger_ui_parameters: dict[str, Any] = attrs.field(factory=dict)
    redoc_cdn: str = docs.REDOC_CDN
    _served: tuple[tuple[ServedMethod, ServedRoute], ...] = ()
    _document: bytes | None = None

    @classmethod
    def from_url(cls, url: URL) -> "OpenAPI | None":
        """Return the OpenAPI settings of the server ``url``, ``rest.openapi.*``; ``None`` when it is off.

        Raises:
            ValueError: The path does not start with ``/``, or the security
                schemes are no JSON object or the security requirements no
                JSON array.
        """
        if not url.get_parameter(_PREFIX + OPENAPI_ENABLED_KEY, boolean, False):
            return None

        parameters = url.parameters
        path = url.parameters.get(_PREFIX + OPENAPI_PATH_KEY, _DEFAULT_PATH).rstrip("/")
        if not path.startswith("/"):
            raise ValueError(f"URL parameter {_PREFIX + OPENAPI_PATH_KEY!r} must start with '/'")
        info = {
            "title": url.parameters.get(_PREFIX + OPENAPI_INFO_TITLE_KEY, _DEFAULT_TITLE),
            "version": url.parameters.get(_PREFIX + OPENAPI_INFO_VERSION_KEY, _DEFAULT_VERSION),
        }
        if description := parameters.get(_PREFIX + OPENAPI_INFO_DESCRIPTION_KEY):
            info["description"] = description
        servers = parameters.get(_PREFIX + OPENAPI_SERVERS_KEY, "")
        swagger_ui = _SETTINGS + OPENAPI_SWAGGER_UI_KEY
        return cls(
            models=Models(url),
            path=path,
            info=info,
            servers=tuple(server.strip() for server in servers.split(",") if server.strip()),
            security_schemes=_json_parameter(parameters, OPENAPI_SECURITY_SCHEMES_KEY, dict, {}),
            security=tuple(_json_parameter(parameters, OPENAPI_SECURITY_KEY, list, [])),
            swagger_ui_cdn=parameters.get(_SETTINGS + OPENAPI_SWAGGER_UI_CDN_KEY, docs.SWAGGER_UI_CDN).rstrip("/"),
            swagger_ui_parameters={
                key.removeprefix(swagger_ui): _json_or_text(value)
                for key, value in parameters.items()
                if key.startswith(swagger_ui) and key != _SETTINGS + OPENAPI_SWAGGER_UI_CDN_KEY
            },
            redoc_cdn=parameters.get(_SETTINGS + OPENAPI_REDOC_CDN_KEY, docs.REDOC_CDN).rstrip("/"),
        )

    def update(self, served: Iterable[ServedMethod], *, default_mapping: bool) -> None:
        """Describe the routes of ``served``, the routes of methods without their own too while ``default_mapping``."""
        self._served = tuple(
            (method, rest)
            for method in sorted(served, key=lambda method: (method.service.interface, method.method.name))
            for rest in method.rest_routes
            if rest.messages is not None and rest.route.include_in_schema and (default_mapping or not rest.default)
        )
        # The document is made again when it is next asked for.
        self._document = None

    def respond(self, method: bytes, target: bytes) -> tuple[int, Headers, bytes] | None:
        """Return the response to a request for the document or a viewer; ``None`` for a target outside ``path``."""
        path = target.partition(b"?")[0].decode("utf-8", "replace")
        if path != self.path and not path.startswith(self.path + "/"):
            return None
        rest = path[len(self.path) :]
        if method not in (b"GET", b"HEAD"):
            return HTTPStatus.METHOD_NOT_ALLOWED, ((HttpHeader.ALLOW, b"GET, HEAD"),), b""

        # A viewer sits one level below the path, so the document is one level up.
        document = f"../api-docs/{_DOCUMENT_GROUP}"
        match rest:
            case "" | "/":
                return _redirect(f"{self.path}/swagger-ui/")
            case "/openapi.json" | "/api-docs" | "/api-docs/" | "/api-docs/default" | "/api-docs/default.json":
                return HTTPStatus.OK, (_JSON,), self.document()
            case "/swagger-ui" | "/redoc":
                return _redirect(f"{self.path}{rest}/")
            case "/swagger-ui/" | "/swagger-ui/index.html":
                page = docs.swagger_ui(
                    title=self.info["title"],
                    cdn=self.swagger_ui_cdn,
                    url=document,
                    parameters=self.swagger_ui_parameters,
                )
                return HTTPStatus.OK, (_HTML,), page
            case "/redoc/" | "/redoc/index.html":
                return HTTPStatus.OK, (_HTML,), docs.redoc(title=self.info["title"], cdn=self.redoc_cdn, url=document)
            case _:
                return HTTPStatus.NOT_FOUND, (_JSON,), dump_json({"detail": "Not Found"}).encode()

    def document(self) -> bytes:
        """Return the OpenAPI document as JSON, made once for each `update`."""
        if self._document is None:
            self._document = dump_json(self._build()).encode()
        return self._document

    def _build(self) -> dict[str, Any]:
        schemas = Schemas(self.models)
        paths: dict[str, dict[str, Any]] = {}
        taken: set[str] = set()
        validation_errors: list[dict[str, Any]] = []
        extras: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        for served, rest in self._served:
            route = rest.route
            # The default mapping takes GET too, which documentation leaves to POST, where a body fits.
            methods = ("POST",) if rest.default else sorted(route.methods)
            item = paths.setdefault(_CATCH_ALL.sub(r"{\1}", route.path), {})
            for http_method in methods:
                # Another service of the same path, of another group or version, is described once.
                if http_method.lower() not in item:
                    item[http_method.lower()] = described = operation(
                        served, rest, http_method, schemas, taken, validation_errors
                    )
                    if route.openapi_extra:
                        extras.append((described, route.openapi_extra))

        definitions = schemas.resolve()
        if validation_errors:
            _add_validation_errors(definitions, validation_errors)
        # Merged once every schema is in place, so that the members it names override them.
        for described, extra in extras:
            merge(described, extra)
        document: dict[str, Any] = {"openapi": "3.1.0", "info": self.info}
        if self.servers:
            document["servers"] = [{"url": server} for server in self.servers]
        document["paths"] = paths
        components: dict[str, Any] = {}
        if definitions:
            components["schemas"] = dict(sorted(definitions.items()))
        if self.security_schemes:
            components["securitySchemes"] = self.security_schemes
        if components:
            document["components"] = components
        if self.security:
            document["security"] = list(self.security)
        return document


def _json_parameter(parameters: Mapping[str, str], key: str, kind: type, default: Any) -> Any:
    """Return the JSON value of the setting ``key``, of ``kind``, or ``default`` when it is absent."""
    text = parameters.get(_PREFIX + key)
    if text is None:
        return default
    try:
        value = load_json(text)
    except ValueError as error:
        raise ValueError(f"URL parameter {_PREFIX + key!r} must be JSON: {error}") from None
    if not isinstance(value, kind):
        raise ValueError(f"URL parameter {_PREFIX + key!r} must be a JSON {'object' if kind is dict else 'array'}")
    return value


def _json_or_text(text: str) -> Any:
    """Return the JSON value of ``text``, or ``text`` itself where it is no JSON."""
    try:
        return load_json(text)
    except ValueError:
        return text


def _redirect(location: str) -> tuple[int, Headers, bytes]:
    return HTTPStatus.FOUND, ((b"location", location.encode()),), b""


def _add_validation_errors(definitions: dict[str, Any], responses: list[dict[str, Any]]) -> None:
    """Add the schemas of a 422 under names the models left free, and point each of ``responses`` to them."""
    validation_error = _free_name(_VALIDATION_ERROR, definitions)
    http_validation_error = _free_name(_HTTP_VALIDATION_ERROR, definitions)
    definitions[validation_error] = {
        "properties": {
            "loc": {
                "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                "type": "array",
                "title": "Location",
            },
            "msg": {"type": "string", "title": "Message"},
            "type": {"type": "string", "title": "Error Type"},
        },
        "type": "object",
        "required": ["loc", "msg", "type"],
        "title": validation_error,
    }
    definitions[http_validation_error] = {
        "properties": {
            "detail": {"items": {"$ref": REF.format(name=validation_error)}, "type": "array", "title": "Detail"}
        },
        "type": "object",
        "title": http_validation_error,
    }
    for response in responses:
        response["content"] = {"application/json": {"schema": {"$ref": REF.format(name=http_validation_error)}}}


def _free_name(base: str, definitions: Mapping[str, Any]) -> str:
    """Return ``base``, or a name of its own that is free in ``definitions``, had a model of the document taken it."""
    if base not in definitions:
        return base

    name, number = f"Dubbo{base}", 1
    while name in definitions:
        number += 1
        name = f"Dubbo{base}{number}"
    return name
