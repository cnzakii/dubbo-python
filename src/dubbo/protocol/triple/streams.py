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

"""What Triple uses of an HTTP/2 or HTTP/3 request stream, which both versions provide alike."""

import typing
from types import TracebackType
from typing import Self, TypeAlias

from dubbo.transport.http.h2 import H2Error, H2StreamResetError
from dubbo.transport.http.h3 import H3Error, H3StreamResetError

__all__ = [
    "RESET_ERRORS",
    "TRANSPORT_ERRORS",
    "Headers",
    "HttpClientStream",
    "HttpServerStream",
    "HttpStream",
    "TransportError",
]

# An ordered field section of lowercase names and raw values.
Headers: TypeAlias = tuple[tuple[bytes, bytes], ...]
# The failures of a stream or its connection.
TransportError: TypeAlias = H2Error | H3Error
TRANSPORT_ERRORS = (H2Error, H3Error)
# The aborts of a stream by its peer.
RESET_ERRORS = (H2StreamResetError, H3StreamResetError)


class HttpStream(typing.Protocol):
    """One request stream, used as `dubbo.transport.http.h2.H2Stream` and `dubbo.transport.http.h3.H3Stream` describe.

    Its operations raise one of `TRANSPORT_ERRORS` when the stream or its
    connection fails, and one of `RESET_ERRORS` when the peer aborts it.
    ``reset`` aborts it as cancelled, and so does closing a stream whose
    directions have not both ended.
    """

    async def send_data(self, data: bytes, *, end_stream: bool = False) -> None: ...

    async def send_trailers(self, trailers: Headers) -> None: ...

    async def end(self) -> None: ...

    async def reset(self) -> None: ...

    async def receive_data_exactly(self, nbytes: int) -> bytes: ...

    async def receive_trailers(self) -> Headers | None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None: ...


class HttpServerStream(HttpStream, typing.Protocol):
    """The stream of a request a server receives."""

    @property
    def peer(self) -> str | None: ...

    @property
    def request_headers(self) -> Headers: ...

    async def send_response(self, headers: Headers, *, end_stream: bool = False) -> None: ...


class HttpClientStream(HttpStream, typing.Protocol):
    """The stream of a request a client sends."""

    async def receive_response(self) -> Headers: ...
