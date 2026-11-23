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

"""The HTTP header fields the framework reads and writes, the text of their values, the media type of a content type,
and the statuses whose responses carry no body."""

import enum
from http import HTTPStatus

__all__ = ["BODILESS_STATUSES", "HttpHeader", "field_text", "media_type"]


# Responses that carry no body, RFC 9110 sections 15.3.5, 15.3.6 and 15.4.5.
BODILESS_STATUSES = frozenset((HTTPStatus.NO_CONTENT, HTTPStatus.RESET_CONTENT, HTTPStatus.NOT_MODIFIED))


class HttpHeader(bytes, enum.Enum):
    """HTTP fields, lower-case as HTTP/2 and HTTP/3 carry them."""

    METHOD = b":method"
    SCHEME = b":scheme"
    AUTHORITY = b":authority"
    PATH = b":path"
    STATUS = b":status"
    CONTENT_TYPE = b"content-type"
    CONTENT_LENGTH = b"content-length"
    ALLOW = b"allow"
    TE = b"te"
    USER_AGENT = b"user-agent"
    # Names another place the same service is reached, RFC 7838.
    ALT_SVC = b"alt-svc"


def field_text(value: bytes) -> str:
    """Return the text of a header field value: UTF-8 when it decodes as such, else Latin-1, which takes any bytes."""
    try:
        return value.decode()
    except UnicodeDecodeError:
        return value.decode("latin-1")


def media_type(content_type: bytes | None) -> bytes | None:
    """Return the media type of a ``content-type`` value, lower-case and without parameters, as in ``application/json``.

    ``None`` stays ``None``.
    """
    return None if content_type is None else content_type.partition(b";")[0].strip().lower()
