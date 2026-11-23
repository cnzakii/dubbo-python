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

"""The serving side of a unary call made over plain HTTP, with its arguments in the path, query, headers and body."""

import logging
import re
from collections.abc import Iterable, Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote

from multidict import MultiDict

from dubbo.exceptions import RpcError, Status
from dubbo.transport.http.h1 import H1Error, H1ServerExchange
from dubbo.transport.http.headers import BODILESS_STATUSES, HttpHeader, media_type
from dubbo.utils.json import dump_json

from ..attachments import attachment_headers, split_headers
from ..grpc.headers import is_grpc
from ..grpc.status import to_rpc_error
from ..headers import TripleHeader
from ..routes import MethodKey, ServedMethod, ServedRoute, invoke
from ..settings import TripleSettings
from ..streams import TRANSPORT_ERRORS, Headers, HttpServerStream
from .cors import ORIGIN, REQUEST_METHOD, Cors
from .exceptions import HttpError
from .messages import BodyKind
from .openapi import OpenAPI
from .router import Found, RestRouter, RouteEntry, WrongMethod
from .transcoding import RouteMessages

__all__ = ["RestHandler"]

# The scheme and authority of an absolute-form request target.
_ABSOLUTE_FORM = re.compile(rb"https?://[^/?]*", re.IGNORECASE)

_logger = logging.getLogger(__name__)

# The HTTP status of each gRPC status, as google.rpc.Code documents them.
_HTTP_STATUS_BY_GRPC = {
    Status.OK: HTTPStatus.OK,
    Status.CANCELLED: 499,
    Status.UNKNOWN: HTTPStatus.INTERNAL_SERVER_ERROR,
    Status.INVALID_ARGUMENT: HTTPStatus.BAD_REQUEST,
    Status.DEADLINE_EXCEEDED: HTTPStatus.GATEWAY_TIMEOUT,
    Status.NOT_FOUND: HTTPStatus.NOT_FOUND,
    Status.ALREADY_EXISTS: HTTPStatus.CONFLICT,
    Status.PERMISSION_DENIED: HTTPStatus.FORBIDDEN,
    Status.RESOURCE_EXHAUSTED: HTTPStatus.TOO_MANY_REQUESTS,
    Status.FAILED_PRECONDITION: HTTPStatus.BAD_REQUEST,
    Status.ABORTED: HTTPStatus.CONFLICT,
    Status.OUT_OF_RANGE: HTTPStatus.BAD_REQUEST,
    Status.UNIMPLEMENTED: HTTPStatus.NOT_IMPLEMENTED,
    Status.INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
    Status.UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    Status.DATA_LOSS: HTTPStatus.INTERNAL_SERVER_ERROR,
    Status.UNAUTHENTICATED: HTTPStatus.UNAUTHORIZED,
}
_JSON = (HttpHeader.CONTENT_TYPE, b"application/json")
_FORM = b"application/x-www-form-urlencoded"
# The fields of a CORS preflight request.
_PREFLIGHT_FIELDS = frozenset((ORIGIN, REQUEST_METHOD, b"access-control-request-headers"))
# Name the service group and version when Triple's own fields do not.
_REST_SERVICE_GROUP = "rest-service-group"
_REST_SERVICE_VERSION = "rest-service-version"


class _Exchange:
    """One request and its response, over any HTTP version."""

    method: bytes
    target: bytes
    headers: Headers
    fields: Mapping[bytes, bytes]
    attachments: MultiDict[str | bytes]
    peer: str | None
    responded = False

    async def read(self, nbytes: int) -> bytes:
        """Return the request body, or its first ``nbytes`` bytes when it is longer."""
        raise NotImplementedError

    async def respond(self, status: int, headers: Headers, body: bytes | None) -> None:
        """Send the response; ``None`` sends none of a body, as to ``HEAD``, keeping its headers."""
        raise NotImplementedError


class _H1(_Exchange):
    def __init__(self, exchange: H1ServerExchange) -> None:
        request = exchange.request
        self._exchange = exchange
        self.method = request.method
        self.target = _origin_form(request.target)
        self.headers = tuple((name.lower(), value) for name, value in request.headers)
        self.fields, self.attachments = split_headers(self.headers)
        self.peer = exchange.peer

    async def read(self, nbytes: int) -> bytes:
        return await self._exchange.receive_data_exactly(nbytes)

    async def respond(self, status: int, headers: Headers, body: bytes | None) -> None:
        self.responded = True
        await self._exchange.send_response(status, headers, end_stream=not body)
        if body:
            await self._exchange.send_data(body, end_stream=True)


class _Stream(_Exchange):
    def __init__(
        self, stream: HttpServerStream, fields: Mapping[bytes, bytes], attachments: MultiDict[str | bytes]
    ) -> None:
        self._stream = stream
        self.method = fields.get(HttpHeader.METHOD, b"")
        self.target = fields.get(HttpHeader.PATH, b"")
        self.headers = stream.request_headers
        self.fields, self.attachments = fields, attachments
        self.peer = stream.peer

    async def read(self, nbytes: int) -> bytes:
        return await self._stream.receive_data_exactly(nbytes)

    async def respond(self, status: int, headers: Headers, body: bytes | None) -> None:
        self.responded = True
        await self._stream.send_response(((HttpHeader.STATUS, b"%d" % status), *headers), end_stream=not body)
        if body:
            await self._stream.send_data(body, end_stream=True)


class RestHandler:
    """Serves the unary methods of a server over plain HTTP, any version, with JSON.

    A request reaches the method on its route, or a method without one on
    ``/{interface}/{method}`` with ``GET`` or ``POST`` while
    ``default_mapping`` is true, of the service whose group and version
    ``tri-service-group`` and ``tri-service-version`` name, or else
    ``rest-service-group`` and ``rest-service-version``, the empty ones
    when they name none; `RestMessages` tells where its arguments come from. A body is
    JSON, with ``content-type`` absent or a JSON media type, or for a method
    that takes form fields ``application/x-www-form-urlencoded``, and at
    most ``max-receive-message-size`` bytes. Header fields other than those
    of HTTP and Triple are the attachments of the call, as in gRPC, and the
    response attachments are fields of the response.

    The returned value is the JSON body of a response with the status of the
    route, 200 by default; a response of 204, 205 or 304, or to ``HEAD``,
    has no body. A failure is answered with a JSON object whose ``detail``
    tells what went wrong: the message of an `RpcError` the method raises,
    under the HTTP status google.rpc.Code gives its status, or ``Internal
    Server Error`` under 500 for another exception; a list of what is wrong
    with the arguments, a body that is no JSON included, under 422; 404 for
    a path no method is on, 405 for a method the path does not take, 501 for
    a method plain HTTP cannot carry, 503 for one of a service no longer
    served here, 413 for a body too large and 415 for one of another media
    type. A gRPC request over HTTP/1.1 is answered 415, as
    gRPC needs HTTP/2 or HTTP/3.

    With ``openapi``, the OpenAPI document and its viewers are served as
    `OpenAPI` describes.

    With ``cors``, responses carry the CORS fields it gives, and a CORS
    preflight request is answered 204 for a method its path takes, 404 for
    a path no method is on, or 403 when ``cors`` or the path refuses it;
    without, every preflight request is answered 403.
    """

    def __init__(
        self,
        settings: TripleSettings,
        *,
        default_mapping: bool = True,
        cors: Cors | None = None,
        openapi: OpenAPI | None = None,
    ) -> None:
        self._settings = settings
        self._default_mapping = default_mapping
        self._cors = cors
        self._openapi = openapi
        self._router = RestRouter(())
        self._retired = RestRouter(())

    def update(self, served: Mapping[MethodKey, ServedMethod], retired: Iterable[ServedMethod]) -> None:
        """Route requests to ``served``, and answer those of ``retired``, whose services are no longer served, with 503.

        Raises:
            ValueError: Two methods take the same HTTP method on paths of the
                same shape, for the same group and version.
        """
        router = RestRouter(self._endpoints(served.values()))
        self._retired = RestRouter(self._endpoints(retired), unique=False)
        self._router = router
        if self._openapi is not None:
            self._openapi.update(served.values(), default_mapping=self._default_mapping)

    async def serve_exchange(self, exchange: H1ServerExchange) -> None:
        """Serve the HTTP/1.1 request of ``exchange``."""
        await self._serve(_H1(exchange))

    async def serve_stream(
        self, stream: HttpServerStream, fields: Mapping[bytes, bytes], attachments: MultiDict[str | bytes]
    ) -> None:
        """Serve the plain HTTP request on ``stream``, whose request headers split into ``fields`` and ``attachments``."""
        await self._serve(_Stream(stream, fields, attachments))

    def _endpoints(self, served: Iterable[ServedMethod]) -> list[RouteEntry]:
        endpoints = []
        for method in served:
            for rest in method.rest_routes:
                # A method without its own route is on /{interface}/{method} while the default mapping is on.
                if rest.default and not self._default_mapping:
                    continue
                endpoints.append(
                    RouteEntry(
                        path=rest.route.path,
                        methods=rest.route.methods,
                        group=method.service.group,
                        version=method.service.version,
                        target=(method, rest),
                    )
                )
        return endpoints

    async def _serve(self, exchange: _Exchange) -> None:
        head = exchange.method == b"HEAD"
        # The OpenAPI document and its viewers are answered as they are, before any route.
        answer = None if self._openapi is None else self._openapi.respond(exchange.method, exchange.target)
        if answer is not None:
            status, headers, body = answer
            headers = (*headers, (HttpHeader.CONTENT_LENGTH, b"%d" % len(body)))
            await exchange.respond(status, headers, None if head or not body else body)
            return
        origin = _field(exchange.headers, ORIGIN)
        preflight = exchange.method == b"OPTIONS" and origin is not None and _field(exchange.headers, REQUEST_METHOD)
        # Every outcome becomes a status, response headers and a JSON body.
        try:
            status, headers, body = self._preflight(exchange) if preflight else await self._call(exchange)
        except HttpError as error:
            status, headers, body = error.status, error.headers, _detail(error.detail)
        except (*TRANSPORT_ERRORS, H1Error):
            # The request or its connection failed; nothing more can be sent on it.
            raise
        except Exception:
            _logger.exception("Serving a plain HTTP request to %r failed", exchange.target)
            # A response already begun cannot be replaced by a 500.
            if exchange.responded:
                raise
            status, headers, body = HTTPStatus.INTERNAL_SERVER_ERROR, (), _detail("Internal Server Error")

        # Every response to a cross-origin call, an error too, carries CORS fields, so the page can read it.
        if self._cors is not None and not preflight:
            headers = (*headers, *self._cors.response_headers(origin))
        if status in BODILESS_STATUSES:
            await exchange.respond(status, headers, None)
            return
        # A response to HEAD has the headers of the response to GET, without its body.
        headers = (_JSON, (HttpHeader.CONTENT_LENGTH, b"%d" % len(body)), *headers)
        await exchange.respond(status, headers, None if head else body)

    async def _call(self, exchange: _Exchange) -> tuple[int, Headers, bytes]:
        """Return the status, headers and body of the response to a call; raise `HttpError` for any failure."""
        fields, attachments = exchange.fields, exchange.attachments
        if isinstance(exchange, _H1) and is_grpc(fields.get(HttpHeader.CONTENT_TYPE)):
            raise HttpError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "gRPC is served over HTTP/2 and HTTP/3")

        # 1. Find the method on the path of the target.
        query, endpoint, variables = self._find(exchange, exchange.method.decode("latin-1"))

        # 2. Check the body against the method before reading it.
        served: ServedMethod
        rest: ServedRoute
        served, rest = endpoint.target
        messages = rest.messages
        if messages is None:
            raise HttpError(HTTPStatus.NOT_IMPLEMENTED, f"cannot be called over plain HTTP: {rest.unbound}")
        self._check_body(messages, fields)
        # 3. Read the body, bind the arguments and call the method, counted as running
        # so that a graceful shutdown waits for it.
        with served.service.running():
            body = await self._read(exchange)
            arguments = messages.arguments(variables, query, exchange.headers, body)
            result = await invoke(served, arguments, attachments, exchange.peer)

            # 4. Answer with the returned value, or the error; both carry the response attachments.
            headers = tuple(attachment_headers(result.attachments)) if result.attachments else ()
            if result.error is not None:
                raise _failure(result.error, headers)
            status = rest.route.status_code
            if status in BODILESS_STATUSES:
                return status, headers, b""
            try:
                return status, headers, messages.dump_response(result.value)
            except (TypeError, ValueError) as error:
                _logger.exception(
                    "The value %s.%s returned cannot be written as JSON", served.service.interface, served.method.name
                )
                raise HttpError(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal Server Error", headers) from error

    def _preflight(self, exchange: _Exchange) -> tuple[int, Headers, bytes]:
        """Return the 204 response to a CORS preflight request for a method on its path; raise 403 to refuse it."""
        requested = (_field(exchange.headers, REQUEST_METHOD) or b"").decode("latin-1").strip()
        # The request that follows names its group and version, which the preflight request does not carry.
        segments = _segments(exchange)[0]
        match self._router.find(requested, segments, None, None, any_service=True):
            case Found():
                pass
            case WrongMethod():
                # The path is served, but not with the method the page asks for.
                raise HttpError(HTTPStatus.FORBIDDEN, "the CORS request is not allowed")
            case _:
                if isinstance(self._retired.find(requested, segments, None, None, any_service=True), Found):
                    raise HttpError(HTTPStatus.SERVICE_UNAVAILABLE, "the service is no longer served here")
                raise HttpError(HTTPStatus.NOT_FOUND, "Not Found")
        fields = {name: value for name, value in exchange.headers if name in _PREFLIGHT_FIELDS}
        headers = None if self._cors is None else self._cors.preflight(fields)
        if headers is None:
            raise HttpError(HTTPStatus.FORBIDDEN, "the CORS request is not allowed")
        return HTTPStatus.NO_CONTENT, headers, b""

    def _find(self, exchange: _Exchange, method: str) -> tuple[bytes, RouteEntry, dict[str, str]]:
        """Return the query of the target of ``exchange``, the endpoint of ``method`` on its path, and its variables.

        Raises:
            HttpError: 400 for a target that is no path or a group or version
                that is no UTF-8, 405 when the path takes other HTTP methods,
                503 when its service was unexported, else 404.
        """
        fields, attachments = exchange.fields, exchange.attachments
        segments, query = _segments(exchange)
        # The rest-service-* attachments are taken out, so the method does not see them.
        group = _name(fields.get(TripleHeader.SERVICE_GROUP), attachments.popone(_REST_SERVICE_GROUP, None))
        version = _name(fields.get(TripleHeader.SERVICE_VERSION), attachments.popone(_REST_SERVICE_VERSION, None))

        match self._router.find(method, segments, group, version):
            case Found(endpoint, variables):
                return query, endpoint, variables
            case WrongMethod(allowed):
                allow = (HttpHeader.ALLOW, ", ".join(sorted(allowed)).encode())
                raise HttpError(HTTPStatus.METHOD_NOT_ALLOWED, "Method Not Allowed", (allow,))
            case _:
                if isinstance(self._retired.find(method, segments, group, version), Found):
                    raise HttpError(HTTPStatus.SERVICE_UNAVAILABLE, "the service is no longer served here")
                raise HttpError(HTTPStatus.NOT_FOUND, "Not Found")

    def _check_body(self, messages: RouteMessages, fields: Mapping[bytes, bytes]) -> None:
        """Refuse a body of the wrong media type, or one its declared length shows too large, before reading it."""
        max_size = self._settings.max_message_size
        length = fields.get(HttpHeader.CONTENT_LENGTH)
        # A content-length that is no number is skipped here; reading the body checks the size.
        if length is not None and length.isdigit() and int(length) > max_size:
            raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"the body is larger than {max_size} bytes")
        # A method that takes no body, or an empty body, accepts any media type.
        if messages.body is BodyKind.NONE or length == b"0":
            return
        essence = media_type(fields.get(HttpHeader.CONTENT_TYPE))
        if messages.body is BodyKind.FORM:
            if essence != _FORM:
                raise HttpError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "the body must be application/x-www-form-urlencoded")
        elif not _is_json(essence):
            raise HttpError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "the body must be JSON")

    async def _read(self, exchange: _Exchange) -> bytes:
        max_size = self._settings.max_message_size
        # One byte more than the limit tells a body over it, as one without content-length can be.
        body = await exchange.read(max_size + 1)
        if len(body) > max_size:
            raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"the body is larger than {max_size} bytes")
        return body


def _field(headers: Headers, name: bytes) -> bytes | None:
    """Return the value of the header field ``name``, if the request has it."""
    return next((value for key, value in headers if key == name), None)


def _name(field: bytes | None, attachment: Any) -> str | None:
    """Return the group or version a request names in a Triple field, else in an attachment."""
    if field is None:
        return None if attachment is None else str(attachment)
    try:
        return field.decode()
    except UnicodeDecodeError:
        raise HttpError(HTTPStatus.BAD_REQUEST, "the service group and version must be UTF-8") from None


def _failure(error: Exception, headers: Headers) -> HttpError:
    """Return the response to a call that failed with ``error``, telling its message only when the method chose it."""
    if not isinstance(error, RpcError):
        return HttpError(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal Server Error", headers)
    rpc_error = to_rpc_error(error)
    status = _HTTP_STATUS_BY_GRPC.get(rpc_error.status, HTTPStatus.INTERNAL_SERVER_ERROR)
    return HttpError(status, rpc_error.message, headers)


def _is_json(essence: bytes | None) -> bool:
    """Whether the media type ``essence`` is JSON: absent, ``application/json`` or ``application/*+json``."""
    if essence is None:
        return True
    return essence == b"application/json" or (essence.startswith(b"application/") and essence.endswith(b"+json"))


def _detail(detail: Any) -> bytes:
    return dump_json({"detail": detail}).encode()


def _segments(exchange: _Exchange) -> tuple[list[str], bytes]:
    """Return the path segments of the target of ``exchange``, each percent-decoded alone so that %2F stays in one, and its query.

    Raises:
        HttpError: 400 for a target that is no path.
    """
    raw_path, _, query = exchange.target.partition(b"?")
    if not raw_path.startswith(b"/"):
        raise HttpError(HTTPStatus.BAD_REQUEST, "the request target must be a path")
    return [unquote(part) for part in raw_path[1:].decode("utf-8", "replace").split("/")], query


def _origin_form(target: bytes) -> bytes:
    """Return the path and query of an absolute-form request target, such as a proxy sends, RFC 9112 section 3.2.2."""
    match = _ABSOLUTE_FORM.match(target)
    if match is None:
        return target
    path = target[match.end() :]
    # The empty path is "/".
    return path if path.startswith(b"/") else b"/" + path
