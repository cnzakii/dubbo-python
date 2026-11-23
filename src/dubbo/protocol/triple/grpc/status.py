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

"""The statuses that end a gRPC call, and the errors of responses and transports that stand for one."""

from collections.abc import Mapping
from http import HTTPStatus

from google.protobuf.message import DecodeError

from dubbo._proto.google.rpc.status_pb2 import Status as StatusMessage
from dubbo.exceptions import RemoteError, RpcError, Status
from dubbo.transport.http.h2 import H2ConnectionError, H2ErrorCode, H2StreamResetError
from dubbo.transport.http.h3 import H3ConnectionError, H3ErrorCode, H3StreamResetError
from dubbo.transport.http.headers import HttpHeader

from ..exceptions import ErrorDetail, TripleError
from ..headers import HTTP_OK, decode_binary, encode_binary, percent_decode, percent_encode
from ..streams import TransportError
from .headers import GrpcHeader, content_subtype, quoted

__all__ = ["response_error", "status_headers", "to_rpc_error", "trailer_error", "transport_error"]

_GRPC_OK = b"%d" % Status.OK
_GRPC_STATUS_BY_HTTP = {
    HTTPStatus.BAD_REQUEST: Status.INTERNAL,
    HTTPStatus.UNAUTHORIZED: Status.UNAUTHENTICATED,
    HTTPStatus.FORBIDDEN: Status.PERMISSION_DENIED,
    HTTPStatus.NOT_FOUND: Status.UNIMPLEMENTED,
    HTTPStatus.TOO_MANY_REQUESTS: Status.UNAVAILABLE,
    HTTPStatus.BAD_GATEWAY: Status.UNAVAILABLE,
    HTTPStatus.SERVICE_UNAVAILABLE: Status.UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT: Status.UNAVAILABLE,
}
# PROTOCOL-HTTP2.md maps the HTTP/2 codes; each HTTP/3 code takes the status of the HTTP/2 code of the same meaning.
_H2_RESET_STATUSES = {
    H2ErrorCode.REFUSED_STREAM: Status.UNAVAILABLE,
    H2ErrorCode.CANCEL: Status.CANCELLED,
    H2ErrorCode.ENHANCE_YOUR_CALM: Status.RESOURCE_EXHAUSTED,
    H2ErrorCode.INADEQUATE_SECURITY: Status.PERMISSION_DENIED,
}
_H3_RESET_STATUSES = {
    H3ErrorCode.H3_REQUEST_REJECTED: Status.UNAVAILABLE,
    H3ErrorCode.H3_REQUEST_CANCELLED: Status.CANCELLED,
    H3ErrorCode.H3_EXCESSIVE_LOAD: Status.RESOURCE_EXHAUSTED,
}


def status_headers(error: RpcError | None) -> list[tuple[bytes, bytes]]:
    """Return the grpc-status, grpc-message and grpc-status-details-bin fields that end a call."""
    if error is None:
        return [(GrpcHeader.STATUS, _GRPC_OK)]

    headers = [(GrpcHeader.STATUS, b"%d" % error.status)]
    if error.message:
        headers.append((GrpcHeader.MESSAGE, percent_encode(error.message)))
    if isinstance(error, TripleError) and error.details:
        headers.append((GrpcHeader.STATUS_DETAILS, _encode_details(error)))
    return headers


def response_error(fields: Mapping[bytes, bytes]) -> RpcError | None:
    """Return why response headers are not those of a gRPC response, if they are not."""
    value = fields.get(HttpHeader.STATUS)
    if value != HTTP_OK:
        code = int(value) if value is not None and value.isdigit() else 0
        return RpcError(_GRPC_STATUS_BY_HTTP.get(code, Status.UNKNOWN), f"unexpected HTTP status {code}")

    value = fields.get(HttpHeader.CONTENT_TYPE)
    if content_subtype(value) is None:
        return RpcError(Status.UNKNOWN, f"unexpected content-type {quoted(value)}")

    return None


def trailer_error(fields: Mapping[bytes, bytes]) -> RpcError | None:
    """Return the error that ending fields report; ``None`` for OK."""
    # OK without details comes first: almost every call ends with it.
    value = fields.get(GrpcHeader.STATUS)
    encoded = fields.get(GrpcHeader.STATUS_DETAILS)
    if value == _GRPC_OK and encoded is None:
        return None
    if value is None:
        return RpcError(Status.UNKNOWN, "the server sent no grpc-status")
    try:
        # int() also refuses digits past the limit of its conversion.
        code = int(value) if value.isdigit() else None
    except ValueError:
        code = None
    if code is None:
        return RpcError(Status.INTERNAL, f"malformed grpc-status {quoted(value)}")
    try:
        status = Status(code)
    except ValueError:
        status = Status.UNKNOWN

    message = percent_decode(fields.get(GrpcHeader.MESSAGE, b""))
    decoded = _decode_details(encoded) if encoded is not None else None
    # PROTOCOL-HTTP2.md: the details must not contradict grpc-status, OK included, and the consumer verifies it.
    if decoded is not None and decoded.code != code:
        return RpcError(
            Status.INTERNAL, f"grpc-status-details-bin has code {decoded.code} but grpc-status is {code}: {message}"
        )
    if status is Status.OK:
        return None
    if decoded is None:
        return RpcError(status, message)
    return TripleError(status, message, details=_details(decoded))


def to_rpc_error(error: Exception) -> RpcError:
    """Return a fresh `RpcError` with the status, message and details of ``error``, without its traceback.

    A `RemoteError` keeps its class in the message.
    """
    if isinstance(error, TripleError):
        return TripleError(error.status, error.message, details=error.details)
    if isinstance(error, RemoteError):
        return RpcError(error.status, str(error))
    if isinstance(error, RpcError):
        return RpcError(error.status, error.message)

    return RpcError(Status.UNKNOWN, f"{type(error).__name__}: {error}")


def transport_error(error: TransportError) -> RpcError:
    """Return the error a call ends with when its stream or connection fails."""
    if isinstance(error, H2StreamResetError):
        return RpcError(_H2_RESET_STATUSES.get(error.error_code, Status.INTERNAL), str(error))
    if isinstance(error, H3StreamResetError):
        return RpcError(_H3_RESET_STATUSES.get(error.error_code, Status.INTERNAL), str(error))
    if isinstance(error, H2ConnectionError | H3ConnectionError):
        return RpcError(Status.UNAVAILABLE, str(error))
    return RpcError(Status.INTERNAL, str(error))


def _encode_details(error: TripleError) -> bytes:
    """Encode the google.rpc.Status of ``error`` as a binary field value: base64 without padding."""
    status = StatusMessage(code=error.status, message=error.message)
    for detail in error.details:
        status.details.add(type_url=detail.type_url, value=detail.value)

    return encode_binary(status.SerializeToString())


def _decode_details(value: bytes) -> StatusMessage | None:
    """Return the google.rpc.Status a binary field value encodes; ``None`` when it is malformed."""
    try:
        return StatusMessage.FromString(decode_binary(value))
    except (ValueError, DecodeError):
        return None


def _details(status: "StatusMessage") -> tuple[ErrorDetail, ...]:
    return tuple(ErrorDetail(type_url=detail.type_url, value=detail.value) for detail in status.details)
