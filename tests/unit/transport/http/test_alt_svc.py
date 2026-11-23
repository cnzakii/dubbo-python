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

from dubbo.transport.http.alt_svc import AltService, format_alt_svc, parse_alt_svc


@pytest.mark.parametrize(
    ("value", "alternatives"),
    [
        (b'h3=":443"', (AltService(protocol="h3", port=443),)),
        (
            b'h3=":443"; ma=3600, h3-29=":443"',
            (AltService(protocol="h3", port=443, max_age=3600), AltService(protocol="h3-29", port=443)),
        ),
        (
            b'h3 = "Example.com:8443" ; ma="60" ; persist=1',
            (AltService(protocol="h3", host="Example.com", port=8443, max_age=60, persist=True),),
        ),
        (b'h3="[::1]:443"', (AltService(protocol="h3", host="::1", port=443),)),
        (b'w%3Dx%3Ay=":1"', (AltService(protocol="w=x:y", port=1),)),
        (b'h3=":1"; ma=soon; v="a\\"b"', (AltService(protocol="h3", port=1),)),
        (b'h3=":0", h3=":65536", h3=":x", h3=":2"', (AltService(protocol="h3", port=2),)),
        (b'h3=":1", broken, h3=":2"', (AltService(protocol="h3", port=1),)),
        (b'h3="443", h3=":\xd9\xa3", h3=":' + b"9" * 5000 + b'", h3=":2"', (AltService(protocol="h3", port=2),)),
        (b'h3=":1"; ma=' + b"9" * 5000, (AltService(protocol="h3", port=1, max_age=2**31 - 1),)),
        (b"clear", None),
        (b"Clear", None),
        (b'h3=":1", clear', None),
    ],
    ids=[
        "origin-host",
        "several",
        "parameters",
        "ipv6",
        "percent-encoded",
        "unknown-parameters",
        "invalid-ports",
        "malformed",
        "authorities-without-a-port",
        "longest-max-age",
        "clear",
        "clear-in-any-case",
        "clear-among-others",
    ],
)
def test_an_alt_svc_value_names_its_alternatives_in_order(value, alternatives) -> None:
    assert parse_alt_svc(value) == alternatives


def test_a_formatted_value_parses_back_to_the_same_alternatives() -> None:
    alternatives = (
        AltService(protocol="h3", port=443),
        AltService(protocol="h3", host="::1", port=8443, max_age=60, persist=True),
    )

    value = format_alt_svc(alternatives)

    assert value == b'h3=":443", h3="[::1]:8443"; ma=60; persist=1'
    assert parse_alt_svc(value) == alternatives


def test_a_formatted_protocol_encodes_only_what_a_token_cannot_hold() -> None:
    alternatives = (AltService(protocol="h3+x", port=1), AltService(protocol="a%b c", port=2))

    value = format_alt_svc(alternatives)

    assert value == b'h3+x=":1", a%25b%20c=":2"'
    assert parse_alt_svc(value) == alternatives
