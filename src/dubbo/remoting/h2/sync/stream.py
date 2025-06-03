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
import threading
import typing
from contextlib import contextmanager
from typing import Callable, Optional, TypeVar, Union

from h2 import events as h2_events, exceptions as h2_exceptions

from dubbo.common.types import BytesLike
from dubbo.logger import logger

from ..base import HeadersType, Http2Connection, Http2Stream, parse_headers
from ..exceptions import (
    H2ConnectionError,
    H2ProtocolError,
    H2StreamClosedError,
    H2StreamError,
    H2StreamInactiveError,
    H2StreamResetError,
    convert_h2_exception,
)
from ..registries import Http2ErrorCode
from ._buffer import StreamDataBuffer
from ._tracker import Deadline, SendTracker

if typing.TYPE_CHECKING:
    from .connection import SyncHttp2Connection


__all__ = ["SyncHttp2Stream"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], None]]


class SyncHttp2Stream(Http2Stream):
    __slots__ = (
        "_id",
        "_conn",
        "_local_exc",
        "_remote_exc",
        "_exc_lock",
        "_window_update",
        "_event_dispatcher",
        "_headers_received",
        "_received_headers",
        "_trailers_received",
        "_received_trailers",
        "_received_data",
    )

    _id: int
    _conn: "SyncHttp2Connection"

    # some exceptions
    _local_exc: Optional[H2StreamError]
    _remote_exc: Optional[H2StreamError]
    _exc_lock: threading.RLock

    # flow control
    _window_update: threading.Event

    # event dispatcher
    _event_dispatcher: _EventDispatcher

    # Received headers and trailers
    _headers_received: threading.Event
    _received_headers: Optional[HeadersType]

    _trailers_received: threading.Event
    _received_trailers: Optional[HeadersType]

    _received_data: StreamDataBuffer

    def __init__(self, connection: "SyncHttp2Connection", stream_id: int) -> None:
        # base attributes
        self._conn = connection
        self._id = stream_id
        self.local_exc = None
        self.remote_exc = (
            None
            if stream_id > 0
            else H2StreamInactiveError(message="Stream is not initialized, please call `send_headers` first")
        )
        self._exc_lock = threading.RLock()

        # flow control
        self._window_update = threading.Event()

        # received storage
        self._headers_received = threading.Event()
        self._received_headers: Optional[HeadersType] = None
        self._trailers_received = threading.Event()
        self._received_trailers: Optional[HeadersType] = None
        self._received_data = StreamDataBuffer(ack_callback=self.ack_data)

        self._event_dispatcher: _EventDispatcher = {
            h2_events.RequestReceived: self._handle_headers_received,
            h2_events.ResponseReceived: self._handle_headers_received,
            h2_events.TrailersReceived: self._handle_trailers_received,
            h2_events.DataReceived: self._handle_data_received,
            h2_events.StreamEnded: self._handle_stream_ended,
            h2_events.StreamReset: self._handle_stream_reset,
        }

    def __enter__(self):
        """
        Enter the context manager, returning the stream itself.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit the context manager, closing the stream if it is not already closed.
        """
        self.close()

    # ------ Properties ------

    @property
    def connection(self) -> Http2Connection:
        """Get the connection associated with this stream."""
        return self._conn

    @property
    def id(self) -> int:
        """Get the unique identifier for this stream."""
        return self._id

    @id.setter
    def id(self, value: int) -> None:
        """Set the unique identifier for this stream."""
        if self._id > 0:
            raise H2ProtocolError(f"Stream ID has already been assigned (id={self._id}). Cannot reassign to {value}.")
        self._id = value

    @property
    def local_exc(self) -> Optional[H2StreamError]:
        """Get the local exception associated with this stream."""
        return self._local_exc

    @local_exc.setter
    def local_exc(self, exc: Optional[H2StreamError]) -> None:
        """Set the local exception for this stream."""
        self._local_exc = exc

    @property
    def remote_exc(self) -> Optional[H2StreamError]:
        """Get the remote exception associated with this stream."""
        return self._remote_exc

    @remote_exc.setter
    def remote_exc(self, exc: Optional[H2StreamError]) -> None:
        """Set the remote exception for this stream."""
        self._remote_exc = exc

    # ------ Stream State Management ------
    def handle_stream_error(self, exc: Union[H2StreamResetError, H2StreamClosedError, None]) -> None:
        """Handle stream errors by updating the local and remote exceptions."""
        with self._exc_lock:
            super().handle_stream_error(exc)

    def _on_remote_closed(self, exc: H2StreamError) -> None:
        """Clean up remote sources."""
        self._headers_received.set()
        self._trailers_received.set()
        self._window_update.set()
        self._received_data.close()

    @contextmanager
    def _send_guard(self):
        """Context manager for guarding send operations."""
        try:
            yield
        except (H2ProtocolError, H2ConnectionError):
            # already handled by the stream, just re-raise
            raise
        except h2_exceptions.H2Error as e:
            inner_exc = convert_h2_exception(e)
            if isinstance(inner_exc, H2StreamClosedError):
                self.handle_stream_error(inner_exc)
            raise inner_exc from e
        except Exception as e:
            # Convert any other exception to H2StreamError
            inner_exc = H2StreamError(self._id, f"Stream operation failed: {e}")
            raise inner_exc from e

    # ------ Sending Methods ------
    def send_headers(self, headers: HeadersType, end_stream: bool = False, timeout: Optional[float] = None) -> None:
        with self._send_guard():
            if self._id <= 0:
                # Stream is not initialized, initialize it with the provided headers
                tracker = SendTracker(lambda: self._atomic_initialize(headers, end_stream), timeout=timeout)
            else:
                # Stream already initialized, send headers frame
                tracker = SendTracker(lambda: self._atomic_send_headers(headers, end_stream), timeout=timeout)

            # Trigger the tracker to send data
            self._conn.send(tracker)
            # Wait for the result or exception
            tracker.result()

    def send_data(self, data: BytesLike, end_stream: bool = False, timeout: Optional[float] = None) -> None:
        with self._send_guard():
            data_view = memoryview(data)
            deadline = Deadline(timeout)
            while True:
                tracker: SendTracker[int] = SendTracker(
                    lambda: self._atomic_send_data(data_view, end_stream),
                    deadline=deadline,
                )
                self._conn.send(tracker)
                consumed = tracker.result()

                if consumed == -1:
                    # Flow control window is zero, wait for a window update
                    self._window_update.clear()
                    self._window_update.wait(timeout=deadline.remaining())
                else:
                    # Data was sent, update the data view
                    data_view = data_view[consumed:]
                    if len(data_view) == 0:
                        break

    def end(self, timeout: Optional[float] = None) -> None:
        """Sends an empty DATA frame with the END_STREAM flag set, indicating that
        no more data or headers will be sent on this stream."""
        self.send_data(b"", end_stream=True, timeout=timeout)

    def reset(
        self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR, timeout: Optional[float] = None
    ) -> None:
        with self._send_guard():
            tracker = SendTracker(lambda: self._atomic_reset(error_code), timeout=timeout)
            self._conn.send(tracker)
            tracker.result()

    def close(
        self,
    ) -> None:
        """Close the stream, releasing any resources associated with it."""
        if self.local_exc and isinstance(self.local_exc, H2StreamInactiveError):
            raise self.local_exc

        logger.debug("Closing HTTP/2 stream %s", self._id)

        # Acknowledge any unacknowledged data before closing
        unacked_size = self._received_data.get_unacked_size()
        if unacked_size:
            logger.debug(
                "Stream %s: acknowledging %d bytes of unacknowledged data before closing", self._id, unacked_size
            )
            tracker = SendTracker(lambda: self._atomic_ack_data(unacked_size), no_wait=True)
            self._conn.send_nowait(tracker)

        if self.local_exc and self._remote_exc:
            logger.debug("Stream %s: both local and remote sides already closed", self._id)
            return
        else:
            logger.warning(
                "Stream %s: stream not fully closed; sending RST_STREAM with NO_ERROR to force cleanup",
                self._id,
            )
            self.reset(Http2ErrorCode.NO_ERROR)

    # ------ Receiving Methods ------
    def receive_headers(self, timeout: Optional[float] = None) -> HeadersType:
        """Wait for headers to be received."""
        if self._received_headers:
            return self._received_headers

        if self.remote_exc:
            raise self.remote_exc

        self._headers_received.wait(timeout=timeout)

        # Recursive call is safe: headers will be set before next wait,
        # so recursion will not go deeper than one additional level.
        return self.receive_headers()

    def receive_trailers(self, timeout: Optional[float] = None) -> HeadersType:
        """Wait for trailers to be received."""
        if self._received_trailers:
            return self._received_trailers

        if self.remote_exc:
            raise self.remote_exc

        self._trailers_received.wait(timeout=timeout)

        return self.receive_trailers()

    def ack_data(self, ack_size: int) -> None:
        """Acknowledge the received data"""
        if ack_size <= 0:
            return
        tracker = SendTracker(lambda: self._atomic_ack_data(ack_size), no_wait=True)
        self._conn.send_nowait(tracker)

    def receive_data(self, max_bytes: int = -1, timeout: Optional[float] = None) -> bytes:
        """Wait for data to be received."""
        try:
            return self._received_data.get_data(max_bytes, timeout=timeout)
        except RuntimeError:
            # buffer is closed, stream is likely closed
            if self._remote_exc:
                raise self._remote_exc
            else:
                # This should never happen
                raise H2StreamError(
                    self._id,
                    f"Stream {self._id} closed before receiving data, "
                    f"but no remote exception was recorded. This indicates a protocol or internal logic error.",
                )

    def receive_data_exactly(self, exact_bytes: int, timeout: Optional[float] = None) -> bytes:
        """Wait for exactly `exact_bytes` bytes of data to be received."""
        if exact_bytes < 0:
            raise ValueError("exact_bytes must be non-negative")

        try:
            return self._received_data.get_data_exactly(exact_bytes, timeout=timeout)
        except RuntimeError:
            # buffer is closed, stream is likely closed
            if self._remote_exc:
                raise self._remote_exc
            else:
                # This should never happen
                raise H2StreamError(
                    self._id,
                    f"Stream {self._id} closed before receiving {exact_bytes} bytes, "
                    f"but no remote exception was recorded. This indicates a protocol or internal logic error.",
                )

    # ------ Event Handling ------
    def handle_event(self, event: h2_events.Event):
        """Handle an HTTP/2 event."""
        handle_fn = self._event_dispatcher.get(type(event), self._handle_ignored_event)
        handle_fn(event)

    def _handle_headers_received(self, event: Union[h2_events.RequestReceived, h2_events.ResponseReceived]) -> None:
        """Handle headers received event."""
        self._received_headers = parse_headers(event.headers)
        self._headers_received.set()

    def _handle_trailers_received(self, event: h2_events.TrailersReceived) -> None:
        """Handle trailers received event."""
        self._received_trailers = parse_headers(event.headers)
        self._trailers_received.set()

    def _handle_data_received(self, event: h2_events.DataReceived) -> None:
        """Handle data received event."""
        try:
            self._received_data.append(event.data or b"", event.flow_controlled_length or 0)
        except RuntimeError as e:
            logger.error("Data reception failed on stream %s: %s", self._id, e)

    def _handle_stream_ended(self, event: h2_events.StreamEnded) -> None:
        """Handle stream ended event."""
        exc = H2StreamClosedError(self._id, False, True)
        self.handle_stream_error(exc)

    def _handle_stream_reset(self, event: h2_events.StreamReset) -> None:
        """Handle stream reset event."""
        error_code = Http2ErrorCode(event.error_code) if event.error_code else Http2ErrorCode.NO_ERROR
        exc = H2StreamResetError(self._id, error_code, event.remote_reset)
        self.handle_stream_error(exc)

    def _handle_window_update(self, event: h2_events.WindowUpdated) -> None:
        """Handle window update event."""
        self._window_update.set()

    def _handle_ignored_event(self, event: h2_events.Event) -> None:
        """Handle events that are not explicitly handled."""
        logger.debug("Ignored event: %r on stream %s", event, self._id)
