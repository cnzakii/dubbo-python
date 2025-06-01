#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import enum
from http import HTTPStatus

__all__ = ["MetadataName", "StatusCode", "HTTP_CODE_TO_GRPC_STATUS", "H2_ERROR_TO_GRPC_STATUS"]

from typing import Optional, Union

from dubbo.remoting.h2 import Http2ErrorCode


class MetadataName(enum.StrEnum):
    """
    Standard gRPC metadata keys used in HTTP/2 requests and responses.

    Reference:
    - https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md#requests
    - https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md#responses
    """

    TIMEOUT = "grpc-timeout"  # Deadline timeout for the RPC (e.g. 1S, 500m)
    ENCODING = "grpc-encoding"  # Compression algorithm used by the client (e.g. gzip)
    ACCEPT_ENCODING = "grpc-accept-encoding"  # Supported compression algorithms from the client

    STATUS = "grpc-status"  # Response status code (e.g. 0 = OK, 1 = CANCELLED)
    MESSAGE = "grpc-message"  # Human-readable status message (URL-escaped UTF-8)
    STATUS_DETAILS_BIN = "grpc-status-details-bin"  # Binary-encoded google.rpc.Status (base64-encoded)


class StatusCode(enum.IntEnum):
    """
    gRPC status codes.

    See : https://github.com/grpc/grpc/blob/master/doc/statuscodes.md
    """

    # Not an error; returned on success.
    OK = 0

    # The operation was cancelled, typically by the caller.
    CANCELLED = 1

    # Unknown error.
    UNKNOWN = 2

    # The client specified an invalid argument.
    INVALID_ARGUMENT = 3

    # The deadline expired before the operation could complete.
    DEADLINE_EXCEEDED = 4

    # Some requested entity (e.g., file or directory) was not found
    NOT_FOUND = 5

    # The entity that a client attempted to create (e.g., file or directory) already exists.
    ALREADY_EXISTS = 6

    # The caller does not have permission to execute the specified operation.
    PERMISSION_DENIED = 7

    # Some resource has been exhausted, perhaps a per-user quota, or perhaps the entire file system is out of space.
    RESOURCE_EXHAUSTED = 8

    # The operation was rejected because the system is not in a state required for the operation's execution.
    FAILED_PRECONDITION = 9

    # The operation was aborted, typically due to a concurrency issue
    # such as a sequencer check failure or transaction abort.
    ABORTED = 10

    # The operation was attempted past the valid range.
    OUT_OF_RANGE = 11

    # The operation is not implemented or is not supported/enabled in this service.
    UNIMPLEMENTED = 12

    # Internal errors.
    INTERNAL = 13

    # The service is currently unavailable.
    UNAVAILABLE = 14

    # Unrecoverable data loss or corruption.
    DATA_LOSS = 15

    # The request does not have valid authentication credentials for the operation.
    UNAUTHENTICATED = 16


# This table is to be used only for clients that received a response that did not include grpc-status.
# If grpc-status was provided, it must be used.
# Servers must not use this table to determine an HTTP status code to use;
# the mappings are neither symmetric nor 1-to-1.
# See: https://github.com/grpc/grpc/blob/master/doc/http-grpc-status-mapping.md
HTTP_CODE_TO_GRPC_STATUS: dict[Union[HTTPStatus, int], StatusCode] = {
    # 400
    HTTPStatus.BAD_REQUEST: StatusCode.INTERNAL,
    # 401
    HTTPStatus.UNAUTHORIZED: StatusCode.UNAUTHENTICATED,
    # 403
    HTTPStatus.FORBIDDEN: StatusCode.PERMISSION_DENIED,
    # 404
    HTTPStatus.NOT_FOUND: StatusCode.UNIMPLEMENTED,
    # 502
    HTTPStatus.BAD_GATEWAY: StatusCode.UNAVAILABLE,
    # 503
    HTTPStatus.SERVICE_UNAVAILABLE: StatusCode.UNAVAILABLE,
    # 504
    HTTPStatus.GATEWAY_TIMEOUT: StatusCode.UNAVAILABLE,
    # 429
    HTTPStatus.TOO_MANY_REQUESTS: StatusCode.UNAVAILABLE,
}


# The following mapping from RST_STREAM error codes to GRPC error codes is applied.
# Reference: https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md
H2_ERROR_TO_GRPC_STATUS: dict[Union[Http2ErrorCode, int], Optional[StatusCode]] = {
    Http2ErrorCode.NO_ERROR: StatusCode.INTERNAL,
    Http2ErrorCode.PROTOCOL_ERROR: StatusCode.INTERNAL,
    Http2ErrorCode.INTERNAL_ERROR: StatusCode.INTERNAL,
    Http2ErrorCode.FLOW_CONTROL_ERROR: StatusCode.INTERNAL,
    Http2ErrorCode.SETTINGS_TIMEOUT: StatusCode.INTERNAL,
    Http2ErrorCode.STREAM_CLOSED: None,
    Http2ErrorCode.FRAME_SIZE_ERROR: StatusCode.INTERNAL,
    Http2ErrorCode.REFUSED_STREAM: StatusCode.UNAVAILABLE,
    Http2ErrorCode.CANCEL: StatusCode.CANCELLED,
    Http2ErrorCode.COMPRESSION_ERROR: StatusCode.INTERNAL,
    Http2ErrorCode.CONNECT_ERROR: StatusCode.INTERNAL,
    Http2ErrorCode.ENHANCE_YOUR_CALM: StatusCode.RESOURCE_EXHAUSTED,
    Http2ErrorCode.INADEQUATE_SECURITY: StatusCode.PERMISSION_DENIED,
}
