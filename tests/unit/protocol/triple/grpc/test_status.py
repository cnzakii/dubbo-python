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

import pytest
from google.protobuf import descriptor_pool
from google.protobuf.wrappers_pb2 import Int32Value, StringValue

from dubbo import Status
from dubbo._proto.google.rpc import status_pb2
from dubbo._proto.pool import pool
from dubbo.protocol.triple import ErrorDetail, TripleError
from dubbo.protocol.triple.grpc.status import response_error, status_headers, trailer_error, transport_error
from dubbo.transport.http.h2 import H2ConnectionError, H2ErrorCode, H2StreamError, H2StreamResetError, H2UsageError
from dubbo.transport.http.h3 import H3ConnectionError, H3ErrorCode, H3StreamError, H3StreamResetError, H3UsageError


@pytest.mark.parametrize(
    ("http_status", "status"),
    [
        (b"400", Status.INTERNAL),
        (b"401", Status.UNAUTHENTICATED),
        (b"403", Status.PERMISSION_DENIED),
        (b"404", Status.UNIMPLEMENTED),
        (b"429", Status.UNAVAILABLE),
        (b"502", Status.UNAVAILABLE),
        (b"503", Status.UNAVAILABLE),
        (b"504", Status.UNAVAILABLE),
        (b"500", Status.UNKNOWN),
    ],
)
def test_response_error_maps_http_statuses(http_status: bytes, status: Status) -> None:
    error = response_error({b":status": http_status, b"content-type": b"application/grpc"})

    assert error is not None
    assert error.status is status


def test_response_error_requires_a_grpc_content_type() -> None:
    assert response_error({b":status": b"200", b"content-type": b"application/grpc+proto"}) is None

    error = response_error({b":status": b"200", b"content-type": b"text/html"})
    assert error is not None
    assert error.status is Status.UNKNOWN


def test_trailer_error_reads_grpc_status_and_message() -> None:
    assert trailer_error({b"grpc-status": b"0"}) is None

    error = trailer_error({b"grpc-status": b"7", b"grpc-message": b"no%20way"})
    assert error is not None
    assert (error.status, error.message) == (Status.PERMISSION_DENIED, "no way")


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({}, Status.UNKNOWN),
        ({b"grpc-status": b"x"}, Status.INTERNAL),
        ({b"grpc-status": b"9" * 5000}, Status.INTERNAL),
        ({b"grpc-status": b"99"}, Status.UNKNOWN),
    ],
    ids=["missing", "malformed", "too-long", "unknown"],
)
def test_trailer_error_handles_missing_and_unknown_statuses(fields: dict[bytes, bytes], status: Status) -> None:
    error = trailer_error(fields)

    assert error is not None
    assert error.status is status


def test_an_error_detail_holds_a_protobuf_message() -> None:
    detail = ErrorDetail.from_message(StringValue(value="name"))

    assert detail.type_url == "type.googleapis.com/google.protobuf.StringValue"
    assert detail.unpack(StringValue) == StringValue(value="name")
    assert detail.unpack(Int32Value) is None


@pytest.mark.parametrize("padded", [False, True])
def test_error_details_travel_in_the_trailers(padded: bool) -> None:
    detail = ErrorDetail.from_message(StringValue(value="name"))
    fields = dict(status_headers(TripleError(Status.INVALID_ARGUMENT, "bad", details=[detail])))
    if padded:
        value = fields[b"grpc-status-details-bin"]
        fields[b"grpc-status-details-bin"] = value + b"=" * (-len(value) % 4)

    error = trailer_error(fields)

    assert isinstance(error, TripleError)
    assert (error.status, error.message, error.details) == (Status.INVALID_ARGUMENT, "bad", (detail,))


@pytest.mark.parametrize("value", [b"!!", b"AQID"])
def test_malformed_error_details_leave_the_status(value: bytes) -> None:
    error = trailer_error({b"grpc-status": b"5", b"grpc-status-details-bin": value})

    assert error is not None
    assert error.status is Status.NOT_FOUND
    assert not isinstance(error, TripleError)


def test_the_framework_protos_stay_out_of_the_default_pool() -> None:
    assert status_pb2.DESCRIPTOR.pool is pool
    assert pool is not descriptor_pool.Default()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (H2StreamResetError(1, H2ErrorCode.NO_ERROR), Status.INTERNAL),
        (H2StreamResetError(1, H2ErrorCode.PROTOCOL_ERROR), Status.INTERNAL),
        (H2StreamResetError(1, H2ErrorCode.REFUSED_STREAM), Status.UNAVAILABLE),
        (H2StreamResetError(1, H2ErrorCode.CANCEL), Status.CANCELLED),
        (H2StreamResetError(1, H2ErrorCode.ENHANCE_YOUR_CALM), Status.RESOURCE_EXHAUSTED),
        (H2StreamResetError(1, H2ErrorCode.INADEQUATE_SECURITY), Status.PERMISSION_DENIED),
        (H2StreamResetError(1, 0xFF), Status.INTERNAL),
        (H2ConnectionError("lost"), Status.UNAVAILABLE),
        (H2StreamError("broken", stream_id=1), Status.INTERNAL),
        (H2UsageError("misused", stream_id=1), Status.INTERNAL),
        (H3StreamResetError(0, H3ErrorCode.H3_NO_ERROR), Status.INTERNAL),
        (H3StreamResetError(0, H3ErrorCode.H3_REQUEST_REJECTED), Status.UNAVAILABLE),
        (H3StreamResetError(0, H3ErrorCode.H3_REQUEST_CANCELLED), Status.CANCELLED),
        (H3StreamResetError(0, H3ErrorCode.H3_EXCESSIVE_LOAD), Status.RESOURCE_EXHAUSTED),
        (H3StreamResetError(0, 0x21), Status.INTERNAL),
        (H3ConnectionError("lost"), Status.UNAVAILABLE),
        (H3StreamError("broken", stream_id=0), Status.INTERNAL),
        (H3UsageError("misused", stream_id=0), Status.INTERNAL),
    ],
    ids=[
        "no-error-reset",
        "protocol-reset",
        "refused-reset",
        "cancelled-reset",
        "overload-reset",
        "security-reset",
        "unknown-reset",
        "connection-loss",
        "stream-failure",
        "misuse",
        "h3-no-error-reset",
        "h3-rejected-reset",
        "h3-cancelled-reset",
        "h3-overload-reset",
        "h3-unknown-reset",
        "h3-connection-loss",
        "h3-stream-failure",
        "h3-misuse",
    ],
)
def test_transport_failures_map_to_rpc_statuses_and_keep_their_messages(error, status):
    mapped = transport_error(error)

    assert (mapped.status, mapped.message) == (status, str(error))


def test_error_details_that_contradict_the_status_fail_the_call() -> None:
    detail = ErrorDetail.from_message(StringValue(value="name"))
    fields = dict(status_headers(TripleError(Status.INVALID_ARGUMENT, "bad", details=[detail])))
    fields[b"grpc-status"] = b"%d" % Status.PERMISSION_DENIED

    error = trailer_error(fields)

    # PROTOCOL-HTTP2.md:142-145
    assert error is not None
    assert error.status is Status.INTERNAL
    assert not isinstance(error, TripleError)
    assert "grpc-status-details-bin has code 3 but grpc-status is 7" in error.message


def test_ok_with_error_details_that_contradict_it_fails_the_call() -> None:
    detail = ErrorDetail.from_message(StringValue(value="name"))
    fields = dict(status_headers(TripleError(Status.PERMISSION_DENIED, "denied", details=[detail])))
    fields[b"grpc-status"] = b"0"

    error = trailer_error(fields)

    assert error is not None
    assert error.status is Status.INTERNAL
    assert "grpc-status-details-bin has code 7 but grpc-status is 0" in error.message


@pytest.mark.parametrize("details", [b"", b"!"], ids=["details-of-ok", "malformed"])
def test_ok_with_details_that_do_not_contradict_it_is_ok(details: bytes) -> None:
    assert trailer_error({b"grpc-status": b"0", b"grpc-status-details-bin": details}) is None
