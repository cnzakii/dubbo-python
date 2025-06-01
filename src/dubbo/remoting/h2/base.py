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
from collections.abc import Awaitable
from typing import Callable, Optional, Union

from h2 import events as h2_events

from dubbo.common import URL
from dubbo.common.types import BytesLike

from .common import HeadersType, Http2ChangedSettingsType, Http2SettingsType
from .registries import Http2ErrorCode

__all__ = [
    "Http2StreamHandlerType",
    "Http2ConnectionHandlerType",
    "PingAckHandlerType",
    "SettingsAckHandlerType",
    "AsyncHttp2StreamHandlerType",
    "AsyncHttp2ConnectionHandlerType",
    "AsyncPingAckHandlerType",
    "AsyncSettingsAckHandlerType",
    "Http2Stream",
    "Http2Connection",
    "Http2Client",
    "Http2Server",
    "Http2Transport",
    "AsyncHttp2Stream",
    "AsyncHttp2Connection",
    "AsyncHttp2Client",
    "AsyncHttp2Server",
    "AsyncHttp2Transport",
]

# Synchronous HTTP/2 handler type definitions
Http2StreamHandlerType = Callable[["Http2Stream"], None]
"""Callback type for handling HTTP/2 stream events."""

Http2ConnectionHandlerType = Callable[["Http2Connection"], None]
"""Callback type for handling HTTP/2 connection events."""

PingAckHandlerType = Callable[[bytes], None]
"""Callback type for handling PING ACK responses."""

SettingsAckHandlerType = Callable[[Http2ChangedSettingsType], None]
"""Callback type for handling SETTINGS ACK responses."""

# Asynchronous versions of the handler types
AsyncHttp2StreamHandlerType = Callable[["AsyncHttp2Stream"], Awaitable[None]]
AsyncHttp2ConnectionHandlerType = Callable[["AsyncHttp2Connection"], Awaitable[None]]
AsyncPingAckHandlerType = Callable[[bytes], Awaitable[None]]
AsyncSettingsAckHandlerType = Callable[[Http2ChangedSettingsType], Awaitable[None]]


class Http2Stream(abc.ABC):
    """Abstract base class for HTTP/2 stream management and operations.

    Represents a single bidirectional HTTP/2 stream within an HTTP/2 connection.
    Each stream carries a single HTTP request/response exchange and supports
    independent flow control and state transitions according to RFC 7540.

    Example:
        stream.send_headers([(':method', 'GET'), (':path', '/api')])
        stream.send_data(b'request body', end_stream=True)
        response_headers = stream.receive_headers()
        response_data = stream.receive_data()

    """

    @property
    @abc.abstractmethod
    def stream_id(self) -> int:
        """The unique integer identifier of the HTTP/2 stream.

        Stream IDs are assigned according to RFC 7540 rules:
        - Odd numbers for client-initiated streams
        - Even numbers for server-initiated streams
        - Must be positive and greater than previously used IDs

        Returns:
            The stream ID as a positive integer.

        Note:
            Stream ID 0 is reserved for connection-level operations and
            should never be used for actual streams.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def local_closed(self) -> bool:
        """Check if the stream is closed from the local side.

        Returns:
            True if the stream is closed locally, False otherwise.

        Note:
            A locally closed stream can still receive data from the remote peer
            until the remote side also closes (half-closed state).
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def remote_closed(self) -> bool:
        """Check if the stream is closed from the remote side.

        Returns:
            True if the stream is closed remotely, False otherwise.

        Note:
            A remotely closed stream can still send data to the remote peer
            until the local side also closes (half-closed state).
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def send_headers(self, headers: HeadersType, end_stream: bool = False, timeout: Optional[float] = None) -> None:
        """Send HTTP/2 HEADERS frame on this stream.

        Sends request/response headers or trailing headers. The first call typically
        sends the initial headers (pseudo-headers for requests), while subsequent calls
        with end_stream=True send trailing headers.

        Args:
            headers: Header collection as (name, value) string tuples. For requests,
                pseudo-headers (:method, :path, :scheme, :authority) must be included
                and properly ordered according to RFC 7540 Section 8.1.2.1.
            end_stream: If True, closes the stream after sending headers, indicating
                no data frames will follow. Use for headerless responses or requests.
            timeout: Maximum time in seconds to wait for the operation. If None,
                uses implementation-specific default timeout.

        Raises:
            TimeoutError: If the operation exceeds the specified timeout.
            H2StreamClosedError: If the stream is already closed and cannot send.
            H2StreamResetError: If the stream was reset and is no longer usable.
            H2ProtocolError: If headers are malformed, pseudo-headers are incorrectly
                ordered, or protocol constraints are violated.
            H2ConnectionError: If the underlying connection has failed.

        """
        raise NotImplementedError()

    @abc.abstractmethod
    def send_data(self, data: BytesLike, end_stream: bool = False, timeout: Optional[float] = None) -> None:
        """Send HTTP/2 DATA frame(s) with automatic flow control management.

        Sends request/response body data while respecting HTTP/2 flow control windows
        at both stream and connection levels. Large payloads may be automatically
        fragmented into multiple DATA frames based on flow control availability.

        Args:
            data: Payload data as bytes, bytearray, or memoryview. Can be empty
                to send a zero-length DATA frame (useful with end_stream=True).
            end_stream: If True, closes the stream after sending data, indicating
                this is the final frame for the stream.
            timeout: Maximum time in seconds to wait for flow control windows
                and network transmission. If None, uses implementation default.

        Raises:
            TimeoutError: If the operation exceeds the timeout while waiting for
                flow control windows or network transmission.
            H2StreamInactiveError: If the stream has not been properly initialized
                with send_headers() first.
            H2StreamClosedError: If the stream is closed and cannot send data.
            H2StreamResetError: If the stream was reset and is no longer usable.
            H2ProtocolError: If attempting to send data after trailing headers,
                or other HTTP/2 protocol violations.
            H2ConnectionError: If the underlying connection has failed.

        Note:
            This method handles flow control automatically. It may block until
            sufficient flow control credits are available. Large data will be
            sent incrementally as flow control windows permit.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self, timeout: Optional[float] = None) -> None:
        """Gracefully close the stream by sending an END_STREAM flag.

        Sends an empty DATA frame with the END_STREAM flag set, indicating that
        no more data or headers will be sent on this stream. This is the preferred
        method for clean stream termination when no more data needs to be sent.

        Args:
            timeout: Maximum time in seconds to wait for the close operation.
                If None, uses implementation-specific default timeout.

        Raises:
            TimeoutError: If the operation exceeds the specified timeout.
            H2StreamInactiveError: If the stream has not been initialized with
                send_headers() and cannot be closed.
            H2StreamClosedError: If the stream is already closed locally.
            H2StreamResetError: If the stream was reset and is no longer usable.
            H2ProtocolError: If the stream state prevents graceful closure.
            H2ConnectionError: If the underlying connection has failed.

        Note:
            This method is equivalent to calling send_data(b'', end_stream=True).
            After calling close(), the stream cannot send additional data or headers.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def reset(
        self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR, timeout: Optional[float] = None
    ) -> None:
        """Immediately abort the stream by sending an RST_STREAM frame.

        Terminates the stream abruptly, indicating an error condition or cancellation.
        This is more forceful than graceful closure and immediately invalidates the
        stream for both sending and receiving operations.

        Args:
            error_code: HTTP/2 error code indicating the reason for stream reset.
                Common codes include CANCEL (user cancellation), INTERNAL_ERROR
                (implementation error), and PROTOCOL_ERROR (HTTP/2 violations).
            timeout: Maximum time in seconds to wait for the reset operation.
                If None, uses implementation-specific default timeout.

        Raises:
            TimeoutError: If the operation exceeds the specified timeout.
            H2StreamInactiveError: If the stream has not been initialized and
                cannot be reset.
            H2ProtocolError: If protocol constraints prevent the reset operation.
            H2ConnectionError: If the underlying connection has failed.

        Note:
            After reset, the stream becomes completely unusable. The remote peer
            will also be notified and should stop processing the stream.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def receive_headers(self, timeout: Optional[float] = None) -> HeadersType:
        """Receive the initial headers for this stream.

        Blocks until headers are available or timeout occurs.
        Returns the HEADERS frame received on this stream.

        Args:
            timeout: Optional timeout for the operation in seconds.

        Returns:
            The received headers as a list of (name, value) tuples.

        Raises:
            TimeoutError: If the operation times out.
            H2StreamInactiveError: If the stream is inactive.
            H2StreamClosedError: If the stream was closed before headers.
            H2StreamResetError: If the stream was reset before headers.
            H2ProtocolError: If protocol violations occur.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def receive_trailers(self, timeout: Optional[float] = None) -> HeadersType:
        """Receive trailing headers for this stream.

        Blocks until trailers are available or timeout occurs.
        Trailers are the final HEADERS frame with END_STREAM flag.

        Args:
            timeout: Optional timeout for the operation in seconds.

        Returns:
            The received trailers as a list of (name, value) tuples.

        Raises:
            TimeoutError: If the operation times out.
            H2StreamInactiveError: If the stream is inactive.
            H2StreamClosedError: If the stream was closed before trailers.
            H2StreamResetError: If the stream was reset before trailers.
            H2ProtocolError: If protocol violations occur.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def receive_data(self, max_bytes: int = -1, strict: bool = False, timeout: Optional[float] = None) -> bytes:
        """Receive data chunk from the stream with optional limits.

        May return less than max_bytes depending on available data and flow control.
        In strict mode, raises if exact byte count cannot be fulfilled due to closure.

        Args:
            max_bytes: Maximum bytes to receive (-1 for unlimited).
            strict: Whether to enforce exactly max_bytes reception.
            timeout: Optional timeout for the operation in seconds.

        Returns:
            The received data as bytes.

        Raises:
            TimeoutError: If the operation times out.
            H2StreamInactiveError: If the stream is inactive.
            H2StreamClosedError: If the stream was closed.
            H2StreamResetError: If the stream was reset.
            H2ProtocolError: If protocol violations occur.
            ValueError: If max_bytes is invalid or strict mode constraints fail.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def handle_event(self, event: h2_events.Event) -> None:
        """Handle HTTP/2 event for internal stream management.

        Note:
            This method is for internal use by the stream manager.
            Users should not call this directly.

        Args:
            event: The HTTP/2 event to handle.

        Raises:
            H2StreamInactiveError: If the stream is inactive.
            H2StreamClosedError: If the stream is closed.
            H2StreamResetError: If the stream was reset.
            H2ProtocolError: If protocol violations occur.
        """
        raise NotImplementedError()


class Http2Connection(abc.ABC):
    """Abstract base class for HTTP/2 connection management.

    This class defines the interface for HTTP/2 connections that handle stream lifecycle,
    connection settings, ping/keepalive functionality, and graceful shutdown procedures.
    All HTTP/2 streams originate from and are managed by a connection instance.

    Note:
        This is an abstract base class and cannot be instantiated directly.
        Concrete implementations should be used for actual HTTP/2 communication.

    Example:
        connection = Http2Connection(...)
        connection.start()
        stream = connection.create_stream()
        stream.send_headers([(':method', 'GET'), (':path', '/')])
        connection.close()
    """

    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        """Check if the connection is currently established.

        Returns:
            True if the connection is active, False otherwise.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def start(self) -> None:
        """Start the HTTP/2 connection.

        Initializes the connection and prepares it for stream creation and data exchange.
        This method must be called before creating streams or sending any frames.

        Raises:
            ConnectionError: If the connection cannot be established.
            H2ProtocolError: If HTTP/2 protocol negotiation fails.
            OSError: If underlying network operations fail.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_stream(self, stream_id: int = -1) -> "Http2Stream":
        """Create and return a new stream on this connection.

        Args:
            stream_id: Stream ID to use, or -1 to auto-assign. **In most cases, you
                should NOT provide a custom stream_id and let the implementation
                auto-assign it (-1).** If provided, it must be a positive odd number
                for client-initiated streams, or a positive even number for
                server-initiated streams. Manual stream ID assignment should only
                be used in advanced scenarios where you understand HTTP/2 stream
                management.

        Returns:
            "Http2Stream": A new stream instance ready for communication.

        Raises:
            H2ProtocolError: If the operation violates the provisions of HTTP/2
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def update_settings(
        self,
        settings: Http2SettingsType,
        handler: Optional[SettingsAckHandlerType] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Send a SETTINGS frame to update configuration on the remote peer.

        Applies settings locally and sends SETTINGS frame to the remote peer.
        If a handler is provided, it will be called when the SETTINGS ACK is received

        Args:
            settings: Mapping of setting codes to their desired integer values.
                Valid setting codes are defined in Http2SettingCode enum.
            handler: Optional callback to handle SETTINGS ACK responses.
            timeout: Optional timeout for the operation in seconds.

        Raises:
            H2ProtocolError: If settings contain invalid values or violate protocol constraints.
            H2ConnectionError: If the connection is closed or in an invalid state.
            TimeoutError: If the operation exceeds the specified timeout.
            ValueError: If settings parameter contains invalid setting codes or values.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def ping(
        self, payload: BytesLike, handler: Optional[PingAckHandlerType] = None, timeout: Optional[float] = None
    ) -> None:
        """Send a PING frame to the peer and register a callback upon ACK.

        Used for liveness checking, RTT measurement, or custom signaling.
        The callback will be invoked when the corresponding PING ACK is received.

        Args:
            payload: Exactly 8 bytes of payload data as required by RFC 7540.
                This data will be echoed back in the PING ACK frame.
            handler: Function to call when a corresponding ACK is received.
                The callback should handle any exceptions internally.
            timeout: Optional timeout for the operation in seconds.

        Raises:
            H2ProtocolError: If payload is not exactly 8 bytes or other protocol violations occur.
            TimeoutError: If the operation times out.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[BytesLike] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Gracefully close the HTTP/2 connection by sending a GOAWAY frame.

        Notifies the peer of the last stream ID accepted and the reason for shutdown.
        This method should be called to properly terminate the connection and allow
        the peer to handle in-flight streams appropriately.

        Args:
            error_code: HTTP/2 error code indicating the reason for closure.
                Defaults to NO_ERROR for graceful shutdown.
            last_stream_id: The highest-numbered stream ID for which the sender
                of the GOAWAY frame might have taken some action on or might yet
                take action on. If None, uses the connection's internal tracking.
            additional_data: Optional debug information (should not exceed 2^16 bytes).
                This data is for debugging purposes only and may not be processed
                by all implementations.
            timeout: Optional timeout for the operation in seconds.
        """
        raise NotImplementedError()


class Http2Client(Http2Connection, abc.ABC):
    """Abstract base class for HTTP/2 client connections.

    Inherits all capabilities from Http2Connection but specifically designed
    for client-side HTTP/2 operations. Client connections initiate streams
    with odd-numbered stream IDs.

    Example:
        with transport.connect(URL("http://example.com")) as client:
            stream = client.create_stream()
            stream.send_headers([(':method', 'GET'), (':path', '/')])
            response_headers = stream.receive_headers()
            response_data = stream.receive_data()
    """

    @abc.abstractmethod
    def __enter__(self) -> "Http2Client":
        raise NotImplementedError()

    @abc.abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        raise NotImplementedError()


class Http2Server(abc.ABC):
    """Abstract base class for HTTP/2 server implementations.

    Handles incoming HTTP/2 connections and provides stream management
    for server-side operations. Server-initiated streams use even-numbered
    stream IDs.
    """

    @abc.abstractmethod
    def serve(
        self, stream_handler: Http2StreamHandlerType, connection_handler: Optional[Http2ConnectionHandlerType] = None
    ) -> None:
        """Start serving HTTP/2 connections with provided handlers.

        Args:
            stream_handler: Callback invoked for each new incoming stream.
                Should handle the complete stream lifecycle.
            connection_handler: Optional callback for connection-level events.
                Called when new connections are established.

        Raises:
            ConnectionError: If the server cannot bind to the specified address.
            H2ProtocolError: If HTTP/2 protocol violations occur.
        """
        raise NotImplementedError()


class Http2Transport(abc.ABC):
    """Abstract base class for HTTP/2 transport implementations.

    Provides factory methods for creating HTTP/2 client and server instances
    based on URL schemes and transport protocols (TCP, TLS, etc.).
    """

    @abc.abstractmethod
    def connect(self, url: URL) -> Http2Client:
        """Establish an HTTP/2 client connection to the specified URL.

        Args:
            url: Target URL containing scheme, host, port, and other connection details.

        Returns:
            A configured HTTP/2 client connection ready for stream creation.

        Raises:
            ConnectionError: If connection establishment fails.
            ValueError: If the URL is invalid or unsupported.
            H2ProtocolError: If HTTP/2 protocol negotiation fails.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def bind(self, url: URL) -> Http2Server:
        """Create an HTTP/2 server bound to the specified URL.

        Args:
            url: Binding URL containing scheme, host, port, and server configuration.

        Returns:
            A configured HTTP/2 server ready to accept connections.

        Raises:
            OSError: If binding to the specified address fails.
            ValueError: If the URL is invalid or unsupported.
            H2ProtocolError: If HTTP/2 server configuration is invalid.
        """
        raise NotImplementedError()


# ---------------------------------------------------------------
# Asynchronous Interface
# ---------------------------------------------------------------
class AsyncHttp2Stream(abc.ABC):
    """Asynchronous version of Http2Stream.

    Provides the same functionality as Http2Stream but with async/await syntax
    for non-blocking operations.
    """

    @property
    @abc.abstractmethod
    def stream_id(self) -> int:
        """The unique integer identifier of the HTTP/2 stream.

        Returns:
            The stream ID as a positive integer.
        """
        raise NotImplementedError()

    @property
    def local_closed(self) -> bool:
        """Check if the stream is closed from the local side.

        Returns:
            True if the stream is closed locally, False otherwise.
        """
        raise NotImplementedError()

    @property
    def remote_closed(self) -> bool:
        """Check if the stream is closed from the remote side.

        Returns:
            True if the stream is closed remotely, False otherwise.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def send_headers(self, headers: HeadersType, end_stream: bool = False) -> None:
        """Async version of send_headers.

        Args:
            headers: Headers to send. Pseudo-headers must be properly ordered.
            end_stream: Whether to end the stream after sending headers.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def send_data(self, data: BytesLike, end_stream: bool = False) -> None:
        """Async version of send_data.

        Args:
            data: Payload to send as bytes, bytearray, or memoryview.
            end_stream: Whether to end the stream after this data.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Async version of close.

        Gracefully close the stream by sending END_STREAM flag.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def reset(self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR) -> None:
        """Async version of reset.

        Args:
            error_code: HTTP/2 error code indicating reset reason.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_headers(self) -> HeadersType:
        """Async version of receive_headers.

        Returns:
            The received headers as a list of (name, value) tuples.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_trailers(self) -> HeadersType:
        """Async version of receive_trailers.

        Returns:
            The received trailers as a list of (name, value) tuples.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def receive_data(self, max_bytes: int = -1, strict: bool = False) -> bytes:
        """Async version of receive_data.

        Args:
            max_bytes: Maximum bytes to receive (-1 for unlimited).
            strict: Whether to enforce exactly max_bytes reception.

        Returns:
            The received data as bytes.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def handle_event(self, event: h2_events.Event) -> None:
        """Async version of handle_event.

        Args:
            event: The HTTP/2 event to handle.
        """
        raise NotImplementedError()


class AsyncHttp2Connection(abc.ABC):
    """Asynchronous HTTP/2 connection interface for managing streams, settings, and connection lifecycle.

    This class provides the asynchronous equivalent of Http2Connection, enabling
    non-blocking HTTP/2 operations using async/await syntax. All operations are
    asynchronous and support proper cancellation and timeout handling.

    Note:
        This is an abstract base class and cannot be instantiated directly.
        Concrete implementations should be used for actual HTTP/2 communication.
    """

    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        """Check if the connection is currently established.

        Returns:
            True if the connection is active, False otherwise.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def create_stream(self, stream_id: int = -1) -> "AsyncHttp2Stream":
        """Async version of create_stream.

        Args:
            stream_id: Stream ID to use, or -1 to auto-assign.

        Returns:
            A new async stream instance ready for communication.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def update_settings(
        self, settings: Http2SettingsType, handler: Optional[AsyncSettingsAckHandlerType] = None
    ) -> None:
        """Async version of update_settings.

        Args:
            settings: Mapping of setting codes to their desired values.
            handler: Optional callback for SETTINGS ACK responses.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def ping(self, payload: BytesLike, handler: Optional[AsyncPingAckHandlerType] = None) -> None:
        """Async version of ping.

        Args:
            payload: Exactly 8 bytes of payload data.
            handler: Optional callback for PING ACK responses.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[BytesLike] = None,
    ) -> None:
        """Async version of close.

        Args:
            error_code: HTTP/2 error code indicating closure reason.
            last_stream_id: Highest stream ID that may have been processed.
            additional_data: Optional debug information.
        """
        raise NotImplementedError()


class AsyncHttp2Client(AsyncHttp2Connection, abc.ABC):
    """Async HTTP/2 client connection.

    Async version of Http2Client for client-side operations.


    Example:
        async def main():
            async with await transport.connect(...) as client:
                stream = await connection.create_stream()
                await stream.send_headers([(':method', 'GET'), (':path', '/')])
                response_headers = await stream.receive_headers()

        asyncio.run(main())
    """

    @abc.abstractmethod
    async def __aenter__(self) -> "AsyncHttp2Client":
        """Enter the async context manager and return the client instance."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        """Exit the async context manager and clean up resources."""
        raise NotImplementedError()


class AsyncHttp2Server(abc.ABC):
    """Async HTTP/2 server implementation.

    Async version of Http2Server for handling incoming connections.
    """

    @abc.abstractmethod
    async def serve(
        self,
        stream_handler: AsyncHttp2StreamHandlerType,
        connection_handler: Optional[AsyncHttp2ConnectionHandlerType] = None,
    ) -> None:
        """Async version of serve.

        Args:
            stream_handler: Async callback for each new incoming stream.
            connection_handler: Optional async callback for connection events.
        """
        raise NotImplementedError()


class AsyncHttp2Transport(abc.ABC):
    """Async HTTP/2 transport implementation.

    Async version of Http2Transport for creating client and server instances.
    """

    @abc.abstractmethod
    async def connect(self, url: URL) -> AsyncHttp2Client:
        """Async version of connect.

        Args:
            url: Target URL for connection establishment.

        Returns:
            A configured async HTTP/2 client connection.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def bind(self, url: URL) -> AsyncHttp2Server:
        """Async version of bind.

        Args:
            url: Binding URL for server configuration.

        Returns:
            A configured async HTTP/2 server.
        """
        raise NotImplementedError()
