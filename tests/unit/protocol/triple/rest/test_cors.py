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

from dubbo.protocol.triple.rest.cors import Cors
from dubbo.url import URL


def _cors(query: str) -> Cors | None:
    return Cors.from_url(URL.parse(f"tri://127.0.0.1:1/x?{query}"))


def _preflight(cors: Cors, origin: bytes, method: bytes = b"GET", headers: bytes | None = None):
    fields = {b"origin": origin, b"access-control-request-method": method}
    if headers is not None:
        fields[b"access-control-request-headers"] = headers
    found = cors.preflight(fields)
    return None if found is None else dict(found)


def test_cors_is_off_without_allowed_origins() -> None:
    assert _cors("rest.cors.max-age=5") is None


def test_named_origins_are_echoed_and_vary_the_response() -> None:
    cors = _cors(
        "rest.cors.allowed-origins=https://A.example,https://b.example&rest.cors.allow-credentials=true"
        "&rest.cors.exposed-headers=x-a,x-b"
    )

    assert dict(cors.response_headers(b"https://a.example")) == {
        b"access-control-allow-origin": b"https://a.example",
        b"vary": b"origin",
        b"access-control-allow-credentials": b"true",
        b"access-control-expose-headers": b"x-a, x-b",
    }
    assert cors.response_headers(b"https://c.example") == ((b"vary", b"origin"),)
    assert cors.response_headers(None) == ((b"vary", b"origin"),)


def test_any_origin_is_answered_with_a_star_and_no_vary() -> None:
    cors = _cors("rest.cors.allowed-origins=*")

    assert cors.response_headers(b"https://a.example") == ((b"access-control-allow-origin", b"*"),)
    assert cors.response_headers(None) == ()


def test_a_preflight_allowed_by_the_defaults_names_what_it_may_send() -> None:
    cors = _cors("rest.cors.allowed-origins=https://a.example")

    assert _preflight(cors, b"https://a.example", b"POST", b"X-Token, content-type") == {
        b"access-control-allow-origin": b"https://a.example",
        b"access-control-allow-methods": b"GET, HEAD, POST",
        b"access-control-allow-headers": b"x-token, content-type",
        b"access-control-max-age": b"1800",
        b"vary": b"origin",
    }


@pytest.mark.parametrize(
    ("origin", "method", "headers"),
    [
        (b"https://c.example", b"GET", None),
        (b"https://a.example", b"DELETE", None),
        (b"https://a.example", b"GET", b"x-token, x-other"),
    ],
    ids=["origin", "method", "header"],
)
def test_a_preflight_is_refused_when_anything_it_asks_for_is_not_allowed(origin, method, headers) -> None:
    cors = _cors(
        "rest.cors.allowed-origins=https://a.example&rest.cors.allowed-methods=GET,put&rest.cors.allowed-headers=X-Token"
    )

    assert _preflight(cors, origin, method, headers) is None
    assert _preflight(cors, b"https://a.example", b"PUT", b"x-token")[b"access-control-allow-methods"] == b"GET, PUT"


def test_any_method_and_header_are_answered_with_those_asked_for() -> None:
    cors = _cors("rest.cors.allowed-origins=*&rest.cors.allowed-methods=*&rest.cors.max-age=0")

    answer = _preflight(cors, b"https://a.example", b"PURGE", b"x-a")

    assert answer[b"access-control-allow-methods"] == b"PURGE"
    assert answer[b"access-control-allow-headers"] == b"x-a"
    assert answer[b"access-control-max-age"] == b"0"


@pytest.mark.parametrize(
    ("query", "match"),
    [
        ("rest.cors.allowed-origins=*&rest.cors.allow-credentials=true", "credentials for any origin"),
        ("rest.cors.allowed-origins=*&rest.cors.max-age=-1", "must not be negative"),
    ],
)
def test_invalid_settings_are_refused(query, match) -> None:
    with pytest.raises(ValueError, match=match):
        _cors(query)
