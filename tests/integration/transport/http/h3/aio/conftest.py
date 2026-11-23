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

from pathlib import Path

import pytest

from dubbo.url import URL

pytest.importorskip("ngquic")

_CERTIFICATES = Path(__file__).resolve().parents[5] / "certificates"
_SERVER_TLS = {
    "ssl-enabled": "true",
    "tls-cert-file": str(_CERTIFICATES / "localhost-cert.pem"),
    "tls-key-file": str(_CERTIFICATES / "localhost-key.pem"),
}
_CLIENT_TLS = {"ssl-enabled": "true", "ca-cert-file": str(_CERTIFICATES / "localhost-cert.pem")}

_REQUEST_HEADERS = (
    (b":method", b"POST"),
    (b":scheme", b"https"),
    (b":authority", b"localhost"),
    (b":path", b"/test"),
    (b"content-type", b"application/grpc"),
)


def _url(host: str, port: int, tls: dict[str, str], parameters: dict[str, str]) -> URL:
    """Underscores in parameter names stand for hyphens."""
    return URL(
        protocol="tri",
        host=host,
        port=port,
        parameters=tls | {name.replace("_", "-"): value for name, value in parameters.items()},
    )


@pytest.fixture
def server_url():
    """Return a function that gives the URL of a TLS server on 127.0.0.1."""

    def server_url(port: int = 0, **parameters: str) -> URL:
        return _url("127.0.0.1", port, _SERVER_TLS, parameters)

    return server_url


@pytest.fixture
def client_url():
    """Return a function that gives the URL of a client trusting the test certificate."""

    def client_url(port: int, host: str = "localhost", **parameters: str) -> URL:
        return _url(host, port, _CLIENT_TLS, parameters)

    return client_url


@pytest.fixture
def certificates():
    return _CERTIFICATES


@pytest.fixture
def request_headers():
    return _REQUEST_HEADERS


@pytest.fixture
def path_headers():
    """Return a function that gives the request headers with another ``:path``."""

    def path_headers(path: bytes) -> tuple[tuple[bytes, bytes], ...]:
        return tuple((name, path if name == b":path" else value) for name, value in _REQUEST_HEADERS)

    return path_headers
