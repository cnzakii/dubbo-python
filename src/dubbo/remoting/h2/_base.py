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
from typing import Awaitable, Callable, Optional, Union

from dubbo.common.types import BytesLike

from .headers import Http2Headers
from .registries import Http2ErrorCodes, Http2SettingCodes

__all__ = ["AsyncHttp2Stream", "AsyncHttp2Connection"]


class AsyncHttp2Stream(abc.ABC):
    """
    Represents a single bidirectional HTTP/2 stream within an HTTP/2 connection.

    Each stream may carry a single HTTP request/response exchange, and supports
    independent flow control and state transitions.
    """

    @property
    @abc.abstractmethod
    def stream_id(self) -> int:
        """
        The unique integer identifier of the HTTP/2 stream.

        Stream IDs are odd for client-initiated streams and even for server-initiated ones.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def send_headers(self, headers: Http2Headers, end_stream: bool = False) -> None:
        """
        Send a HEADERS frame or TRAILERS frame on this stream.

        This method should be called once at the beginning of the stream to send the request/response headers.
        Subsequent calls with `end_stream=True` may send trailing headers.

        :param headers: The headers to send. Pseudo-headers must be properly ordered and valid.
        :param end_stream: Whether to end the stream after sending these headers.
        :raises H2StreamClosedError: If the stream is closed before trailers arrive.
        :raises H2StreamResetError: If the stream was reset before trailers arrived.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def send_data(self, data: BytesLike, end_stream: bool = False) -> None:
        """
        Send a DATA frame (or multiple if flow control requires it).

        This method must respect connection-level flow control windows.

        :param data: The payload to send, as bytes, bytearray, or memoryview.
        :param end_stream: Whether to end the stream after this data.
        :raises H2StreamInactiveError: If the stream is inactive.
        :raises H2StreamClosedError: If the stream is closed before trailers arrive.
        :raises H2StreamResetError: If the stream was reset before trailers arrived.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """
        Gracefully close the stream by sending an empty DATA frame with END_STREAM flag.

        Should be used when no more data or trailers need to be sent.
        :raises H2StreamInactiveError: If the stream is inactive.
        :raises H2StreamClosedError: If the stream is closed before trailers arrive.
        :raises H2StreamResetError: If the stream was reset before trailers arrived.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def reset(self, error_code: Union[Http2ErrorCodes, int] = Http2ErrorCodes.NO_ERROR) -> None:
        """
        Immediately abort the stream with an RST_STREAM frame.

        This is used to indicate a stream-level error or cancel a stream in progress.

        :param error_code: The error code to indicate the reason for the reset.
        :raises H2StreamInactiveError: If the stream is inactive.
        :raises H2StreamClosedError: If the stream is closed before trailers arrive.
        :raises H2StreamResetError: If the stream was reset before trailers arrived.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_headers(self, timeout: Optional[float] = None) -> Optional[Http2Headers]:
        """
        Await the reception of the initial headers for this stream.

        Blocks until headers are available or timeout occurs.

        :param timeout: Optional timeout in seconds.
        :returns: The received headers. If timeout and no headers are received, returns None.
        :raises H2StreamInactiveError: If the stream is inactive.
        :raises H2StreamClosedError: If the stream is closed before trailers arrive.
        :raises H2StreamResetError: If the stream was reset before trailers arrived.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_trailers(self, timeout: Optional[float] = None) -> Optional[Http2Headers]:
        """
        Await the reception of trailing headers for this stream.

        Blocks until trailers are available or timeout occurs.

        :param timeout: Optional timeout in seconds.
        :returns: The received trailers. If timeout and no trailers are received, returns None.
        :raises H2StreamInactiveError: If the stream is inactive.
        :raises H2StreamClosedError: If the stream is closed before trailers arrive.
        :raises H2StreamResetError: If the stream was reset before trailers arrived.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_data(self, max_bytes: int = -1, timeout: Optional[float] = None) -> bytes:
        """
        Receive a chunk of DATA from the stream, respecting optional limits.

        May return less than `max_bytes` depending on available data and flow control.
        An empty bytes object may indicate stream closure.

        :param max_bytes: Maximum number of bytes to receive (-1 for unlimited).
        :param timeout: Optional timeout in seconds to wait for data.
        :returns: The received data as bytes.
        :raises H2StreamInactiveError: If the stream is inactive.
        :raises H2StreamClosedError: If the stream is closed before trailers arrive.
        :raises H2StreamResetError: If the stream was reset before trailers arrived.
        """
        raise NotImplementedError()


class AsyncHttp2Connection(abc.ABC):
    """
    Represents an abstract HTTP/2 connection that manages stream lifecycle, settings,
    ping/keepalive, and graceful shutdown.

    All HTTP/2 streams originate from and are managed by a connection.
    """

    def create_stream(self, stream_id: int = -1) -> AsyncHttp2Stream:
        """
        Create and return a new stream on this connection.

        Client-side implementations should auto-generate stream IDs if `stream_id == -1`.
        Server-side may reject custom IDs if out of order.

        :param stream_id: Stream ID to use, or -1 to auto-assign.
        :returns: An instance of `AsyncHttp2Stream`.
        :raises StreamIdError: If the provided stream ID is invalid.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def update_settings(self, settings: dict[Union[Http2SettingCodes, int], int]) -> None:
        """
        Send a SETTINGS frame to update configuration on the remote peer.

        Applies settings locally, sends SETTINGS frame, and waits for acknowledgment (ACK).
        This is an asynchronous, blocking operation until ACK is received.

        :param settings: Mapping of setting codes to their desired integer values.
        :raises H2ProtocolError: If invalid settings or values are used.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def ping(self, payload: BytesLike, ack_callback: Callable[[], Awaitable[None]]) -> None:
        """
        Send a PING frame to the peer and register an async callback upon ACK.

        Used for liveness checking or RTT measurement.

        :param payload: Exactly 8 bytes of payload as required by RFC.
        :param ack_callback: Async function to call when a corresponding ACK is received.
        :raises ValueError: If the payload is not exactly 8 bytes.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(
        self,
        error_code: Union[Http2ErrorCodes, int] = Http2ErrorCodes.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[BytesLike] = None,
    ) -> None:
        """
        Gracefully close the HTTP/2 connection by sending a GOAWAY frame.

        Notifies the peer of the last stream ID accepted and the reason for shutdown.

        :param error_code: HTTP/2 error code for the GOAWAY frame.
        :param last_stream_id: The last successfully processed stream ID.
        :param additional_data: Optional debug information (should not exceed 2^16 bytes).
        """
        raise NotImplementedError()
