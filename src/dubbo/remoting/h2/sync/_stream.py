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
from dubbo.remoting.h2 import HeadersType, Http2ErrorCode, Http2Stream
from dubbo.remoting.h2.common import ReceivedData, parse_headers
from dubbo.remoting.h2.exceptions import (
    H2ConnectionError,
    H2ProtocolError,
    H2StreamClosedError,
    H2StreamError,
    H2StreamInactiveError,
    H2StreamResetError,
    convert_h2_exception,
)

from ._tracker import Deadline, SendTracker
from ._utils import EndOfStream, StreamEmptyError, StreamFullError, SyncObjectStream

if typing.TYPE_CHECKING:
    from ._connection import SyncHttp2Connection


__all__ = ["SyncHttp2Stream"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], None]]

_DUMMY_RECEIVED_DATA = ReceivedData(memoryview(b""), 0)


class SyncHttp2Stream(Http2Stream):
    __slots__ = (
        "_stream_id",
        "_conn",
        "_local_exc",
        "_remote_exc",
        "_window_update",
        "_event_dispatcher",
        "_headers_received",
        "_received_headers",
        "_trailers_received",
        "_received_trailers",
        "_received_data_buffer",
    )

    _stream_id: int
    _conn: "SyncHttp2Connection"

    # some exceptions
    _local_exc: Optional[Exception]
    _remote_exc: Optional[Exception]

    # flow control
    _window_update: threading.Event

    # event dispatcher
    _event_dispatcher: _EventDispatcher

    # Received headers and trailers
    _headers_received: threading.Event
    _received_headers: Optional[HeadersType]

    _trailers_received: threading.Event
    _received_trailers: Optional[HeadersType]

    _received_data_buffer: tuple[ReceivedData, SyncObjectStream[ReceivedData]]

    def __init__(self, connection: "SyncHttp2Connection", stream_id: int) -> None:
        # base attributes
        self._conn = connection
        self._stream_id = stream_id

        # flow control
        self._window_update = threading.Event()

        # received headers and trailers
        self._headers_received = threading.Event()
        self._received_headers: Optional[HeadersType] = None
        self._trailers_received = threading.Event()
        self._received_trailers: Optional[HeadersType] = None

        # received data buffer
        self._received_data_buffer = (_DUMMY_RECEIVED_DATA, SyncObjectStream(max_size=1000))

        self._event_dispatcher = {
            h2_events.RequestReceived: self._handle_request,
            h2_events.ResponseReceived: self._handle_response,
            h2_events.TrailersReceived: self._handle_trailers,
            h2_events.DataReceived: self._handle_data,
            h2_events.WindowUpdated: self._handle_window_update,
            h2_events.StreamReset: self._handle_stream_reset,
            h2_events.StreamEnded: self._handle_stream_ended,
            h2_events.ConnectionTerminated: self._handle_connection_terminated,
        }

        # Initialize local and remote exceptions
        self._local_exc = self._remote_exc = None
        if stream_id <= 0:
            exc = H2StreamInactiveError(message="Stream is not initialized, please call `send_headers` first")
            self._update_state(exc)

    def __enter__(self):
        """
        Enter the context manager, returning the stream itself.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit the context manager, closing the stream if it is not already closed.
        """
        try:
            if not self.local_closed or not self.remote_closed:
                # If the stream is not closed, reset it to ensure proper cleanup.
                self.reset(Http2ErrorCode.NO_ERROR)
        except Exception:
            pass  # Ignore any exceptions during reset

    # ------ Properties ------

    @property
    def stream_id(self) -> int:
        return self._stream_id

    @property
    def local_closed(self) -> bool:
        return self._local_exc is not None

    @property
    def remote_closed(self) -> bool:
        return self._remote_exc is not None

    # ------ State Machine ------

    def _update_state(self, exc: Optional[Exception]) -> None:
        """
        Update the internal state with the given exception.

        - If `exc` is None: reset both local and remote exceptions.
        - If `exc` indicates a hard termination (H2StreamInactiveError or H2StreamResetError),
          set both sides to the same exception.
        - If `exc` is a H2StreamClosedError, merge with current state and update accordingly.
        """
        if exc is None:
            # Clear both local and remote exceptions
            self._local_exc = self._remote_exc = None
        elif isinstance(exc, H2StreamInactiveError):
            # No initialization yet, set local exception only
            self._local_exc = exc
        if isinstance(exc, H2StreamResetError):
            # Reset the stream, set local and remote exceptions
            self._local_exc = self._remote_exc = exc
        elif isinstance(exc, H2StreamClosedError):
            if exc.local_side and exc.remote_side:
                new_exc = exc
            else:
                # Merge partial local/remote closed state with previous state
                local_side = exc.local_side or isinstance(self._local_exc, H2StreamInactiveError)
                remote_side = exc.remote_side or isinstance(self._remote_exc, H2StreamInactiveError)
                new_exc = H2StreamClosedError(self.stream_id, local_side, remote_side)

            self._local_exc = new_exc if new_exc.local_side else self._local_exc
            self._remote_exc = new_exc if new_exc.remote_side else self._remote_exc

        # Unblock all waiting operations
        if self._remote_exc:
            self._headers_received.set()
            self._trailers_received.set()
            self._window_update.set()
            self._received_data_buffer[1].stop()

        # Both sides are closed, unregister the stream
        if self._local_exc and self._remote_exc:
            self._conn.stream_manager.unregister(self)

    @contextmanager
    def _send_guard(self):
        """Context manager for guarding send operations.

        This context manager catches and converts various HTTP/2 exceptions into
        appropriate stream-level exceptions, ensuring consistent error handling
        across all send operations.

        Raises:
            H2ProtocolError: For protocol-level errors.
            H2ConnectionError: For connection-level errors.
            H2StreamError: For general stream errors.
        """
        try:
            yield
        except (H2ProtocolError, H2ConnectionError):
            # already handled by the stream, just re-raise
            raise
        except h2_exceptions.H2Error as e:
            inner_exc = convert_h2_exception(e)
            if isinstance(inner_exc, H2StreamClosedError):
                self._update_state(inner_exc)
            raise inner_exc from e
        except Exception as e:
            # Convert any other exception to H2StreamError
            inner_exc = H2StreamError(self._stream_id, f"Stream operation failed: {e}")
            raise inner_exc from e

    # ------ Atomic Operations ------
    def _atomic_initialize(self, headers: HeadersType, end_stream: bool) -> None:
        if self._stream_id > 0:
            raise H2ProtocolError("Stream id is already set, cannot be changed")

        h2_core = self._conn.h2_core
        if not h2_core.config.client_side:
            raise H2ProtocolError("Only client can initialize the stream")

        # Clear abnormal status
        self._update_state(None)

        # In client-side HTTP/2, obtaining a new stream ID and sending the initial HEADERS frame
        # must be treated as an atomic operation to avoid protocol errors.
        self._stream_id = h2_core.get_next_available_stream_id()
        self._conn.stream_manager.register(self)
        self._atomic_send_headers(headers, end_stream)

    def _atomic_send_headers(self, headers: HeadersType, end_stream: bool) -> None:
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.send_headers(
            stream_id=self._stream_id,
            headers=headers,
            end_stream=end_stream,
        )

        # update local exception
        if end_stream:
            exc = H2StreamClosedError(self._stream_id, True, False)
            self._update_state(exc)

    def _atomic_send_data(self, data_view: memoryview, end_stream: bool) -> int:
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        window_size = h2_core.local_flow_control_window(self._stream_id)
        data_len = len(data_view)

        if window_size == 0:
            # No window available, wait for a window update
            return -1

        chunk_size = min(window_size, data_len)
        chunk_view = data_view[:chunk_size]
        is_final_chunk = chunk_size == data_len

        # Send DATA frame
        h2_core.send_data(
            stream_id=self._stream_id, data=chunk_view, end_stream=end_stream if is_final_chunk else False
        )

        if end_stream and is_final_chunk:
            # Stream is fully closed locally
            exc = H2StreamClosedError(self._stream_id, True, False)
            self._update_state(exc)

        return chunk_size

    def _atomic_ack_data(self, ack_size: int) -> None:
        """
        Acknowledge the received data to the remote peer by updating flow control.

        Args:
            ack_size: The size of the data to acknowledge.

        """
        h2_core = self._conn.h2_core
        h2_core.acknowledge_received_data(ack_size, self._stream_id)

    def _atomic_reset(self, error_code: Union[Http2ErrorCode, int]) -> None:
        """
        Send RST_STREAM to the stream.
        """
        if self._local_exc:
            raise self._local_exc

        h2_core = self._conn.h2_core
        h2_core.reset_stream(self._stream_id, error_code)

        # update local and remote exceptions
        exc = H2StreamResetError(self._stream_id, error_code, False, "Stream reset by local peer")
        self._update_state(exc)

    # ------ Public Methods ------
    def send_headers(self, headers: HeadersType, end_stream: bool = False, timeout: Optional[float] = None) -> None:
        with self._send_guard():
            if self._stream_id <= 0:
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

    def reset(
        self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR, timeout: Optional[float] = None
    ) -> None:
        with self._send_guard():
            tracker = SendTracker(lambda: self._atomic_reset(error_code), timeout=timeout)
            self._conn.send(tracker)
            tracker.result()

    def close(self, timeout: Optional[float] = None) -> None:
        self.send_data(b"", end_stream=True)

    def receive_headers(self, timeout: Optional[float] = None) -> HeadersType:
        # wait for the event to be set
        self._headers_received.wait(timeout=timeout)
        # get the headers
        headers = self._received_headers
        if headers:
            return headers

        # if headers is None, it means the stream is closed
        if self._remote_exc:
            raise self._remote_exc
        else:
            raise H2StreamError(self._stream_id, "No headers received, but stream is not closed")

    def receive_trailers(self, timeout: Optional[float] = None) -> HeadersType:
        # wait for the event to be set
        self._trailers_received.wait(timeout=timeout)
        # get the headers
        trailers = self._received_trailers
        if trailers:
            return trailers

        # if trailers is None, it means the stream is closed
        if self._remote_exc:
            raise self._remote_exc
        else:
            raise H2StreamError(self._stream_id, "No trailers received, but stream is not closed")

    def receive_data(self, max_bytes: int = -1, strict: bool = False, timeout: Optional[float] = None) -> bytes:
        if max_bytes <= 0 and max_bytes != -1:
            raise ValueError("max_bytes must be -1 or greater than 0")
        elif max_bytes == -1 and strict:
            raise ValueError("max_bytes cannot be -1 when strict is True")

        prev_data, recv_stream = self._received_data_buffer
        chunks: list[memoryview] = []
        total_ack = 0
        total_bytes = 0

        if max_bytes < 0 and strict:
            raise RuntimeError("`max_bytes` must be >= 0 when `strict` is True")

        deadline = Deadline(timeout)
        while max_bytes == -1 or total_bytes < max_bytes:
            # Step 1: Drain existing buffer
            if not prev_data.is_empty():
                chunk = prev_data.get_data(max_bytes - total_bytes if max_bytes > 0 else -1)
                total_bytes += len(chunk)
                chunks.append(chunk)
                total_ack += prev_data.ack_size

            # Step 2: Exit early if buffer still has data
            if not prev_data.is_empty():
                break

            # Step 3: Block or not depending on state
            try:
                if total_bytes > 0 and not strict:
                    # We have received some, and the pattern is not strict,
                    # only attempting to get more data non-blocking.
                    next_data = recv_stream.get_nowait()
                else:
                    # We have not received any data or have not obtained enough data,
                    # then we need to wait for the blockage.

                    with self._conn.h2_core_lock:
                        remote_window = self._conn.h2_core.remote_flow_control_window(self._stream_id)
                    if total_ack >= remote_window:
                        # If the remote window is smaller than the total ack, we need to send ACK to the remote peer.
                        tracker = SendTracker(lambda: self._atomic_ack_data(total_ack), no_wait=True)
                        self._conn.send(tracker)
                        exc = tracker.exception()  # Ignore exceptions
                        if not exc:
                            total_ack = 0

                    next_data = recv_stream.get(timeout=deadline.remaining())
            except (EndOfStream, StreamEmptyError):
                # If the stream is empty or the stream is ended, we can exit
                next_data = None

            # Step 4: Handle end-of-stream signal
            if next_data is None:
                break
            prev_data = next_data

        # If remote error was set, and we failed to read as required
        if self._remote_exc and (total_bytes != max_bytes if strict else total_bytes == 0):
            raise self._remote_exc

        # Update internal buffer state
        self._received_data_buffer = (prev_data, recv_stream)

        # Send window update (ack) if needed
        if total_ack > 0:
            tracker = SendTracker(lambda: self._atomic_ack_data(total_ack), no_wait=True)
            self._conn.send(tracker)
            tracker.exception()  # Ignore exceptions

        return b"".join(chunks)

    # ------ Event Handlers ------
    def _handle_request(self, event: h2_events.RequestReceived) -> None:
        """
        Handle the request received event.
        """
        self._received_headers = parse_headers(event.headers)
        self._headers_received.set()

    def _handle_response(self, event: h2_events.ResponseReceived) -> None:
        """
        Handle the response received event.
        """
        self._received_headers = parse_headers(event.headers)
        self._headers_received.set()

    def _handle_trailers(self, event: h2_events.TrailersReceived) -> None:
        """
        Handle the trailers received event.
        """
        self._received_trailers = parse_headers(event.headers)
        self._trailers_received.set()

    def _handle_data(self, event: h2_events.DataReceived) -> None:
        """
        Handle the data received event.
        """
        # Update the received data buffer
        stream = self._received_data_buffer[1]
        try:
            stream.put_nowait(ReceivedData.from_h2(event))
        except StreamFullError:
            logger.error(
                "[H2Stream] Receive buffer full — dropping data. stream_id=%s, data_length=%d",
                self._stream_id,
                len(event.data or b""),
            )

    def _handle_window_update(self, event: h2_events.WindowUpdated) -> None:
        """
        Handle the window update event.
        """
        # Notify the window update
        self._window_update.set()

    def _handle_stream_reset(self, event: h2_events.StreamReset) -> None:
        """
        Handle the stream reset event.
        """
        error_code: Union[int, None] = event.error_code
        if error_code is None:
            # If no error code is provided, use NO_ERROR
            error_code = Http2ErrorCode.NO_ERROR
        exc = H2StreamResetError(self._stream_id, error_code, False, "Stream reset by remote peer")
        self._update_state(exc)

    def _handle_stream_ended(self, event: h2_events.StreamEnded) -> None:
        """
        Handle the stream end event.
        """
        exc = H2StreamClosedError(self._stream_id, False, True)
        self._update_state(exc)

    def _handle_connection_terminated(self, event: h2_events.ConnectionTerminated) -> None:
        """
        Handle the connection termination event.
        """
        # Handle connection termination
        exc = H2StreamClosedError(self._stream_id, True, True, "Stream closed due to connection termination")
        self._update_state(exc)

    def _handle_ignored_event(self, event: h2_events.Event) -> None:
        """Handle ignored HTTP/2 events.

        Args:
            event: The ignored event from h2 library.
        """
        logger.debug("Ignored event: %s", type(event))

    def handle_event(self, event: h2_events.Event) -> None:
        """
        Handle the event from the h2 connection.
        """
        handler = self._event_dispatcher.get(type(event), self._handle_ignored_event)
        handler(event)
