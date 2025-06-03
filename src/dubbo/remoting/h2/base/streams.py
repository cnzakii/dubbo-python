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
import abc
import typing
from types import TracebackType
from typing import Callable, Optional, TypeVar, Union

from h2 import events as h2_events

from ..exceptions import H2ConnectionError, H2ProtocolError, H2StreamClosedError, H2StreamError, H2StreamResetError
from ..registries import Http2ErrorCode
from .common import HeadersType

if typing.TYPE_CHECKING:
    from .connections import AsyncHttp2Connection, BaseHttp2Connection, Http2Connection

__all__ = ["Http2Stream", "AsyncHttp2Stream"]

_T_Event = TypeVar("_T_Event", bound=h2_events.Event)
_EventDispatcher = dict[type[_T_Event], Callable[[_T_Event], None]]


class BaseHttp2Stream(abc.ABC):
    """Base class for HTTP/2 streams"""

    # ------ Properties ------

    @property
    @abc.abstractmethod
    def connection(self) -> "BaseHttp2Connection":
        """Get the connection associated with this stream."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def id(self) -> int:
        """Get the stream ID."""
        raise NotImplementedError()

    @id.setter
    def id(self, value: int) -> None:
        """Set the stream ID."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def local_exc(self) -> Optional[H2StreamError]:
        """Get the local exception for the stream."""
        raise NotImplementedError()

    @local_exc.setter
    @abc.abstractmethod
    def local_exc(self, value: Optional[H2StreamError]) -> None:
        """Set the local exception for the stream."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def remote_exc(self) -> Optional[H2StreamError]:
        """Get the remote exception for the stream."""
        raise NotImplementedError()

    @remote_exc.setter
    @abc.abstractmethod
    def remote_exc(self, value: Optional[H2StreamError]) -> None:
        """Set the remote exception for the stream."""
        raise NotImplementedError()

    # ------ Stream State Management ------
    def handle_connection_error(self, exc: H2ConnectionError) -> None:
        """Handle connection-level errors."""
        stream_exc = H2StreamClosedError(self.id, True, True, f"Stream closed due to connection error: {exc}")
        self.handle_stream_error(stream_exc)

    def handle_stream_error(self, exc: Union[H2StreamResetError, H2StreamClosedError, None]) -> None:
        """Handle stream-level errors."""
        if self.local_exc and self.remote_exc:
            # If both local and remote exceptions are already set, ignore further errors
            return

        if exc is None:
            # clean up any existing errors
            self.local_exc = self.remote_exc = None
            return

        if isinstance(exc, H2StreamResetError):
            # Handle stream reset error
            self.local_exc = self.remote_exc = exc
        else:
            # handle stream closed errors
            if exc.local_side and exc.remote_side:
                new_exc = exc
            else:
                local_side = isinstance(self.local_exc, H2StreamClosedError) or exc.local_side
                remote_side = isinstance(self.remote_exc, H2StreamClosedError) or exc.remote_side
                new_exc = H2StreamClosedError(self.id, local_side, remote_side)

            if new_exc.local_side:
                self.local_exc = new_exc
            if new_exc.remote_side:
                self.remote_exc = new_exc

        if self.remote_exc:
            self._on_remote_closed(exc)

        if self.remote_exc and self.local_exc:
            # If both local and remote exceptions are set, unregister the stream
            self.connection.stream_manager.unregister_stream(self)

    @abc.abstractmethod
    def _on_remote_closed(self, exc: H2StreamError) -> None:
        """Callback when the remote side closes the stream."""
        raise NotImplementedError()

    # ------ Atomic Sending Methods ------
    def _atomic_initialize(self, headers: HeadersType, end_stream: bool) -> None:
        """Initialize the stream.
        This method atomically obtains a new stream ID and sends the initial
        HEADERS frame to avoid protocol errors. Only client-side connections
        can initialize streams.
        """
        if self.id > 0:
            raise H2ProtocolError("Stream id is already set, cannot be changed")

        h2_core = self.connection.h2_core
        if not h2_core.config.client_side:
            raise H2ProtocolError("Only client can initialize the stream")

        # Clear abnormal status
        self.handle_stream_error(None)

        # In client-side HTTP/2, obtaining a new stream ID and sending the initial HEADERS frame
        # must be treated as an atomic operation to avoid protocol errors.
        self.id = h2_core.get_next_available_stream_id()
        self.connection.stream_manager.register_stream(self)
        self._atomic_send_headers(headers, end_stream)

    def _atomic_send_headers(self, headers: HeadersType, end_stream: bool) -> None:
        """Send headers or trailers to the stream."""
        if self.local_exc:
            raise self.local_exc

        h2_core = self.connection.h2_core
        h2_core.send_headers(
            stream_id=self.id,
            headers=headers,
            end_stream=end_stream,
        )

        # update local exception
        if end_stream:
            exc = H2StreamClosedError(self.id, True, False)
            self.handle_stream_error(exc)

    def _atomic_send_data(self, data_view: memoryview, end_stream: bool) -> int:
        """Send data to the stream respecting flow control."""
        if self.local_exc:
            raise self.local_exc

        h2_core = self.connection.h2_core
        window_size = h2_core.local_flow_control_window(self.id)
        data_len = len(data_view)

        if window_size == 0:
            # No window available, wait for a window update
            return -1

        chunk_size = min(window_size, data_len)
        chunk_view = data_view[:chunk_size]
        is_final_chunk = chunk_size == data_len

        # Send DATA frame
        h2_core.send_data(stream_id=self.id, data=chunk_view, end_stream=end_stream if is_final_chunk else False)

        if end_stream and is_final_chunk:
            # Stream is fully closed locally
            exc = H2StreamClosedError(self.id, True, False)
            self.handle_stream_error(exc)

        # Return the size of the chunk sent
        return chunk_size

    def _atomic_ack_data(self, ack_size: int) -> None:
        """Acknowledge received data on the stream."""
        h2_core = self.connection.h2_core
        h2_core.acknowledge_received_data(ack_size, self.id)

    def _atomic_reset(self, error_code: Union[Http2ErrorCode, int]) -> None:
        """Reset the stream with an error code."""
        if self.local_exc:
            raise self.local_exc

        h2_core = self.connection.h2_core
        h2_core.reset_stream(self.id, error_code)

        # update local and remote exceptions
        exc = H2StreamResetError(self.id, error_code, False, "Stream reset by local peer")
        self.handle_stream_error(exc)

    # --- Event Handling Methods ------
    @abc.abstractmethod
    def handle_event(self, event: h2_events.Event) -> None:
        """Handle an H2 event for this stream."""
        raise NotImplementedError()


class Http2Stream(BaseHttp2Stream, abc.ABC):
    """Synchronous base class for HTTP/2 streams."""

    @abc.abstractmethod
    def __enter__(self) -> "Http2Stream":
        """Enter the synchronous context manager."""
        raise NotImplementedError()

    @abc.abstractmethod
    def __exit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        """Exit the synchronous context manager."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def connection(self) -> "Http2Connection":
        """Get the connection associated with this stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def send_headers(self, headers: HeadersType, end_stream: bool = False, timeout: Optional[float] = None) -> None:
        """Send headers or trailers to remote peer."""
        raise NotImplementedError()

    @abc.abstractmethod
    def send_data(self, data: bytes, end_stream: bool = False, timeout: Optional[float] = None) -> None:
        """Send data to remote peer."""
        raise NotImplementedError()

    @abc.abstractmethod
    def end(self, timeout: Optional[float] = None) -> None:
        """Sends an empty DATA frame with the END_STREAM flag set, indicating that
        no more data or headers will be sent on this stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def reset(
        self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR, timeout: Optional[float] = None
    ) -> None:
        """Reset the stream with an error code."""
        raise NotImplementedError()

    @abc.abstractmethod
    def receive_headers(self, timeout: Optional[float] = None) -> HeadersType:
        """Receive headers from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def receive_trailers(self, timeout: Optional[float] = None) -> HeadersType:
        """Receive trailers from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def receive_data(self, max_bytes: int = -1, timeout: Optional[float] = None) -> bytes:
        """Receive data from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def receive_data_exactly(self, exact_bytes: int, timeout: Optional[float] = None) -> bytes:
        """Receive exactly the specified number of bytes from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """Close the stream synchronously."""
        raise NotImplementedError()


class AsyncHttp2Stream(BaseHttp2Stream, abc.ABC):
    """Asynchronous base class for HTTP/2 streams."""

    @abc.abstractmethod
    async def __aenter__(self) -> "AsyncHttp2Stream":
        """Enter the asynchronous context manager."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def __aexit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        """Exit the asynchronous context manager."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def connection(self) -> "AsyncHttp2Connection":
        """Get the connection associated with this stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def send_headers(self, headers: HeadersType, end_stream: bool = False) -> None:
        """Send headers or trailers to remote peer."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def send_data(self, data: bytes, end_stream: bool = False) -> None:
        """Send data to remote peer."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def end(self) -> None:
        """Sends an empty DATA frame with the END_STREAM flag set, indicating that
        no more data or headers will be sent on this stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def reset(self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR) -> None:
        """Reset the stream with an error code."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_headers(self) -> HeadersType:
        """Receive headers from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_trailers(self) -> HeadersType:
        """Receive trailers from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_data(self, max_bytes: int = -1) -> bytes:
        """Receive data from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_data_exactly(self, exact_bytes: int) -> bytes:
        """Receive exactly the specified number of bytes from the stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Close the stream asynchronously."""
        raise NotImplementedError()
