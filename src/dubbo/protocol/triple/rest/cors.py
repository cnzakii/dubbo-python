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

"""Cross-origin resource sharing, as the Fetch standard defines it, for the methods served over plain HTTP."""

from collections.abc import Mapping

import attrs

from dubbo.constants import (
    CORS_ALLOW_CREDENTIALS_KEY,
    CORS_ALLOWED_HEADERS_KEY,
    CORS_ALLOWED_METHODS_KEY,
    CORS_ALLOWED_ORIGINS_KEY,
    CORS_EXPOSED_HEADERS_KEY,
    CORS_KEY,
    CORS_MAX_AGE_KEY,
    REST_KEY,
)
from dubbo.url import URL
from dubbo.utils.converters import boolean, comma_separated

from ..streams import Headers

__all__ = ["Cors"]

_PREFIX = f"{REST_KEY}.{CORS_KEY}."
_ANY = "*"
_DEFAULT_METHODS = ("GET", "HEAD", "POST")
_DEFAULT_MAX_AGE = 1800

ORIGIN = b"origin"
REQUEST_METHOD = b"access-control-request-method"
_REQUEST_HEADERS = b"access-control-request-headers"
_ALLOW_ORIGIN = b"access-control-allow-origin"
_ALLOW_METHODS = b"access-control-allow-methods"
_ALLOW_HEADERS = b"access-control-allow-headers"
_ALLOW_CREDENTIALS = (b"access-control-allow-credentials", b"true")
_EXPOSE_HEADERS = b"access-control-expose-headers"
_MAX_AGE = b"access-control-max-age"
_VARY_ORIGIN = (b"vary", b"origin")


@attrs.define(frozen=True, kw_only=True)
class Cors:
    """Which origins may call the methods served over plain HTTP, made once from the server's URL.

    A response to an allowed origin names it in
    ``access-control-allow-origin``, or ``*`` when any origin is allowed
    without credentials; a request from another origin is served without
    those fields, which the browser then refuses to show to the page. A
    preflight request, ``OPTIONS`` with ``origin`` and
    ``access-control-request-method``, is answered 204 when the origin, the
    method and every requested header field are allowed, else 403.

    Attributes:
        origins: The allowed origins, lower-case; empty when any is.
        methods: The allowed methods, upper-case; empty when any is.
        headers: The allowed request header fields, lower-case; empty when
            any is.
        exposed: The value of ``access-control-expose-headers``, if any.
        credentials: Whether credentials are allowed.
        max_age: Seconds a browser may keep a preflight response.
    """

    origins: frozenset[str]
    methods: frozenset[str]
    headers: frozenset[str]
    exposed: bytes | None
    credentials: bool
    max_age: int

    @classmethod
    def from_url(cls, url: URL) -> "Cors | None":
        """Return the CORS settings of the server ``url``, ``rest.cors.*``; ``None`` when no origin is allowed.

        Raises:
            ValueError: A setting is invalid, or credentials are allowed for
                any origin.
        """
        origins = url.get_parameter(_PREFIX + CORS_ALLOWED_ORIGINS_KEY, comma_separated, ())
        if not origins:
            return None
        methods = url.get_parameter(_PREFIX + CORS_ALLOWED_METHODS_KEY, comma_separated, _DEFAULT_METHODS)
        headers = url.get_parameter(_PREFIX + CORS_ALLOWED_HEADERS_KEY, comma_separated, (_ANY,))
        exposed = url.get_parameter(_PREFIX + CORS_EXPOSED_HEADERS_KEY, comma_separated, ())
        credentials = url.get_parameter(_PREFIX + CORS_ALLOW_CREDENTIALS_KEY, boolean, False)
        max_age = url.get_parameter(_PREFIX + CORS_MAX_AGE_KEY, int, _DEFAULT_MAX_AGE)
        if max_age < 0:
            raise ValueError(f"URL parameter {_PREFIX + CORS_MAX_AGE_KEY!r} must not be negative")
        if credentials and _ANY in origins:
            raise ValueError("CORS cannot allow credentials for any origin; name the origins")
        return cls(
            origins=frozenset() if _ANY in origins else frozenset(origin.lower() for origin in origins),
            methods=frozenset() if _ANY in methods else frozenset(method.upper() for method in methods),
            headers=frozenset() if _ANY in headers else frozenset(header.lower() for header in headers),
            exposed=", ".join(exposed).encode("latin-1") if exposed else None,
            credentials=credentials,
            max_age=max_age,
        )

    def response_headers(self, origin: bytes | None) -> Headers:
        """Return the CORS fields of a response to a request from ``origin``; for one not allowed, only ``vary: origin`` when the allowed origins are listed."""
        allowed = self._allow_origin(origin)
        if allowed is None:
            # A response that varies by origin says so even when it names none, so caches keep them apart.
            return () if not self.origins else (_VARY_ORIGIN,)
        headers = [allowed]
        if self.origins:
            headers.append(_VARY_ORIGIN)
        if self.credentials:
            headers.append(_ALLOW_CREDENTIALS)
        if self.exposed is not None:
            headers.append((_EXPOSE_HEADERS, self.exposed))
        return tuple(headers)

    def preflight(self, fields: Mapping[bytes, bytes]) -> Headers | None:
        """Return the CORS fields of the 204 response to the preflight request of ``fields``; ``None`` to refuse it."""
        allowed = self._allow_origin(fields.get(ORIGIN))
        method = fields.get(REQUEST_METHOD, b"").decode("latin-1").strip()
        if allowed is None or (self.methods and method not in self.methods):
            return None
        requested = [
            name.strip().lower()
            for name in fields.get(_REQUEST_HEADERS, b"").decode("latin-1").split(",")
            if name.strip()
        ]
        if self.headers and any(name not in self.headers for name in requested):
            return None

        headers = [
            allowed,
            (_ALLOW_METHODS, ", ".join(sorted(self.methods)).encode() if self.methods else method.encode("latin-1")),
            (_MAX_AGE, b"%d" % self.max_age),
        ]
        if requested:
            headers.append((_ALLOW_HEADERS, ", ".join(requested).encode("latin-1")))
        if self.origins:
            headers.append(_VARY_ORIGIN)
        if self.credentials:
            headers.append(_ALLOW_CREDENTIALS)
        return tuple(headers)

    def _allow_origin(self, origin: bytes | None) -> tuple[bytes, bytes] | None:
        """Return the ``access-control-allow-origin`` field for ``origin``, or ``None`` when it is not allowed."""
        if not origin:
            return None
        if not self.origins:
            return _ALLOW_ORIGIN, b"*"
        if origin.decode("latin-1").lower() not in self.origins:
            return None
        return _ALLOW_ORIGIN, origin
