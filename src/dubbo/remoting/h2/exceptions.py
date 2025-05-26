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

from h2 import exceptions as h2_exceptions

from dubbo.remoting.backend.exceptions import ConnectError, ProtocolError

from .registries import Http2ErrorCode


class H2ProtocolError(ProtocolError):
    """Base class for HTTP/2 protocol-specific errors."""

    __slots__ = ()


class H2ConnectionError(ConnectError):
    """Base class for connection-level HTTP/2 errors."""

    __slots__ = ()


class H2ConnectionTerminatedError(H2ConnectionError):
    """Raised when HTTP/2 connection is terminated via GOAWAY frame."""

    __slots__ = ("error_code", "last_stream_id", "additional_data", "remote_termination")

    def __init__(
        self,
        error_code: Union[Http2ErrorCode, int],
        last_stream_id: int,
        *,
        remote_termination: bool,
        additional_data: Optional[bytes] = None,
        message: Optional[str] = None,
    ) -> None:
        if last_stream_id < 0:
            raise ValueError(f"last_stream_id must be non-negative, got {last_stream_id}")

        if additional_data is not None and len(additional_data) > 65535:
            raise ValueError(f"additional_data exceeds 64KB limit: {len(additional_data)} bytes")

        self.error_code = error_code if isinstance(error_code, Http2ErrorCode) else Http2ErrorCode(error_code)
        self.last_stream_id = last_stream_id
        self.additional_data = additional_data
        self.remote_termination = remote_termination

        if message is None:
            direction = "Remote" if remote_termination else "Local"
            message = (
                f"{direction} terminated HTTP/2 connection: "
                f"{self.error_code.name} (0x{self.error_code.value:02X}), "
                f"last_stream_id={last_stream_id}"
            )
            if additional_data:
                message += f", debug_data={len(additional_data)} bytes"

        super().__init__(message)


class H2StreamError(H2ProtocolError):
    """Base class for stream-level HTTP/2 errors."""

    __slots__ = ("stream_id",)

    def __init__(self, stream_id: int, message: str) -> None:
        super().__init__(message)
        self.stream_id = stream_id

    def __str__(self) -> str:
        """Return string representation with stream ID and message."""
        return f"{type(self).__name__}(stream_id={self.stream_id}): {self.args[0]}"

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        return f"{type(self).__name__}(stream_id={self.stream_id}, message={self.args[0]!r})"


class H2StreamInactiveError(H2StreamError):
    """Raised when operating on an inactive HTTP/2 stream.

    This exception occurs when attempting operations on streams that have
    not been properly initialized, activated, or have been deallocated.
    Common causes include using stream IDs that were never created or
    accessing streams after connection cleanup.
    """

    __slots__ = ()

    def __init__(self, stream_id: int, message: Optional[str] = None) -> None:
        """Initialize inactive stream error.

        Args:
            stream_id: ID of the inactive stream.
            message: Optional custom message. Auto-generated if None.
        """
        if message is None:
            message = f"Stream {stream_id} is inactive or uninitialized"
        super().__init__(stream_id, message)


class H2StreamClosedError(H2StreamError):
    """Raised when operating on a closed HTTP/2 stream.

    This exception occurs when attempting operations on streams that have
    been closed either locally, remotely, or both. The closure state affects
    which operations are still valid on the stream.
    """

    __slots__ = ("local_side", "remote_side")

    def __init__(self, stream_id: int, local_side: bool, remote_side: bool, message: Optional[str] = None) -> None:
        if not local_side and not remote_side:
            raise ValueError("At least one side must be closed")

        if message is None:
            if local_side and remote_side:
                message = f"Stream {stream_id} is fully closed (both sides)"
            elif local_side:
                message = f"Stream {stream_id} is half-closed (local side closed)"
            else:  # remote_side is True
                message = f"Stream {stream_id} is half-closed (remote side closed)"

        super().__init__(stream_id, message)
        self.local_side = local_side
        self.remote_side = remote_side


class H2StreamResetError(H2StreamError):
    """Raised when HTTP/2 stream is reset via RST_STREAM frame."""

    __slots__ = ("error_code", "remote_reset")

    def __init__(
        self,
        stream_id: int,
        error_code: Union[Http2ErrorCode, int],
        remote_reset: bool,
        message: Optional[str] = None,
    ) -> None:
        self.error_code = error_code if isinstance(error_code, Http2ErrorCode) else Http2ErrorCode(error_code)
        self.remote_reset = remote_reset

        if message is None:
            direction = "Remote" if remote_reset else "Local"
            message = f"{direction} reset stream {stream_id}: {self.error_code.name} (0x{self.error_code.value:02X})"

        super().__init__(stream_id, message)


def convert_h2_exception(exc: h2_exceptions.H2Error) -> H2ProtocolError:
    """Convert h2 library exception to corresponding H2ProtocolError.

    Transforms low-level h2 library exceptions into the application's
    exception hierarchy, providing consistent error handling and better
    debugging information.

    Args:
        exc: The h2 library exception to convert.

    Returns:
        Corresponding H2ProtocolError subclass with appropriate details.

    Example:
        try:
            h2_connection.send_data(stream_id, data)
        except h2_exceptions.H2Error as e:
            app_error = convert_h2_exception(e)
            raise app_error from e

    Note:
        This function preserves the original exception as the cause
        for better error tracing and debugging.
    """
    # Handle stream-specific errors
    if isinstance(exc, h2_exceptions.StreamClosedError):
        # Assume both sides closed if h2 reports stream as closed
        return H2StreamClosedError(exc.stream_id, True, True)

    if isinstance(exc, h2_exceptions.NoSuchStreamError):
        return H2StreamInactiveError(exc.stream_id, f"Stream {exc.stream_id} does not exist")

    # Handle generic stream errors
    if hasattr(exc, "stream_id"):
        return H2StreamError(exc.stream_id, f"{type(exc).__name__}: {exc}")

    # Handle connection-level protocol errors
    if isinstance(exc, h2_exceptions.ProtocolError):
        try:
            error_code = Http2ErrorCode(exc.error_code)
            message = f"HTTP/2 protocol violation: {error_code.name} (0x{error_code.value:02X})"
        except (ValueError, AttributeError):
            message = f"HTTP/2 protocol violation: {type(exc).__name__}"
        return H2ProtocolError(message)

    # Generic fallback for unhandled h2 errors
    return H2ProtocolError(f"Unhandled h2 library error: {type(exc).__name__}: {exc}")
