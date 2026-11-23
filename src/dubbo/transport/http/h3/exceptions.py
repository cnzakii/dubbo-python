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

from dubbo.exceptions import DubboError

__all__ = [
    "H3ConnectionError",
    "H3Error",
    "H3StreamError",
    "H3StreamResetError",
    "H3UsageError",
]


class H3Error(DubboError):
    """A failure of the HTTP/3 transport."""


class H3ConnectionError(H3Error):
    """A failure to establish a QUIC connection, or of the connection.

    QUIC transport error codes and HTTP/3 error codes overlap in value, so
    ``application`` tells which space ``error_code`` belongs to.

    Attributes:
        error_code: The received or generated error code, if known.
        application: Whether ``error_code`` is an HTTP/3 error code rather
            than a QUIC transport error code.
    """

    error_code: int | None
    application: bool

    def __init__(self, message: str, *, error_code: int | None = None, application: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.application = application


class H3StreamError(H3Error):
    """A failure confined to one HTTP/3 stream, such as a protocol violation on it or a field section it cannot carry.

    Attributes:
        stream_id: The QUIC stream identifier, unique on its connection;
            ``None`` when the failure came before a stream was allocated.
    """

    stream_id: int | None

    def __init__(self, message: str, *, stream_id: int | None) -> None:
        super().__init__(message)
        self.stream_id = stream_id


class H3StreamResetError(H3StreamError):
    """A direction of an HTTP/3 stream that the peer aborted.

    A RESET_STREAM from the peer aborts what it sends, and fails receives; a
    STOP_SENDING from the peer asks this side to abort what it sends, and
    fails sends.

    Attributes:
        error_code: The peer's HTTP/3 error code. Unknown extension codes
            are preserved as integers.
    """

    error_code: int

    def __init__(self, stream_id: int, error_code: int) -> None:
        super().__init__(
            f"the HTTP/3 stream {stream_id} was aborted with error code {error_code}",
            stream_id=stream_id,
        )
        self.error_code = error_code


class H3UsageError(H3Error):
    """An invalid local use of one HTTP/3 stream: operations out of order, or overlapping ones in one direction.

    Attributes:
        stream_id: The stream used.
    """

    stream_id: int

    def __init__(self, message: str, *, stream_id: int) -> None:
        super().__init__(message)
        self.stream_id = stream_id
