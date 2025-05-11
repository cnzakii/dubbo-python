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
from typing import Optional, Union

from dubbo.remoting.backend.exceptions import ConnectError, ProtocolError
from dubbo.remoting.h2 import Http2ErrorCodes


class H2ProtocolError(ProtocolError):
    """Base class for HTTP/2 protocol errors."""

    __slots__ = ()


class H2ConnectionError(ConnectError):
    """Raised when there is a connection-level HTTP/2 error."""

    __slots__ = ()


class H2StreamError(H2ProtocolError):
    """Raised when there is a stream-level HTTP/2 error."""

    __slots__ = ("stream_id",)

    def __init__(self, stream_id: int, message: str):
        super().__init__(message)
        self.stream_id = stream_id

    def __str__(self) -> str:
        return f"{type(self).__name__}(stream_id={self.stream_id}, message={self.args[0]})"

    def __repr__(self) -> str:
        return str(self)


class H2StreamInactiveError(H2StreamError):
    """Raised when the HTTP/2 stream is inactive."""

    __slots__ = ()


class H2StreamClosedError(H2StreamError):
    """Raised when the HTTP/2 stream is closed."""

    __slots__ = ("local_side", "remote_side")

    def __init__(self, stream_id: int, local_side: bool, remote_side: bool, message: Optional[str] = None):
        if message is None:
            message = f"Stream closed (local: {local_side}, remote: {remote_side})"
        super().__init__(stream_id, message)
        self.local_side = local_side
        self.remote_side = remote_side


class H2StreamResetError(H2StreamError):
    """Raised when the HTTP/2 stream is reset."""

    __slots__ = ("error_code", "remote_reset")

    def __init__(
        self, stream_id: int, error_code: Union[Http2ErrorCodes, int], remote_reset: bool, message: Optional[str] = None
    ):
        self.error_code = error_code if isinstance(error_code, Http2ErrorCodes) else Http2ErrorCodes(error_code)
        self.remote_reset = remote_reset
        if message is None:
            message = f"Stream reset with error code {self.error_code.name}({self.error_code.value})"
        super().__init__(stream_id, message)
