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
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Callable, Generic, Optional, TypeVar

from h2 import events as h2_events

from dubbo.logger import logger
from dubbo.remoting.h2.exceptions import H2ConnectionError, H2ProtocolError

from ..base import CallbackManager, Http2Stream, Http2StreamHandlerType, StreamManager

__all__ = ["SyncCallbackManager", "SyncStreamManager"]

_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")
_CallbackT = Callable[[_ValueT], None]


class SyncCallbackManager(CallbackManager[_KeyT, _ValueT, _CallbackT], Generic[_KeyT, _ValueT]):
    """Thread-safe manager for callback registration and dispatch."""

    __slots__ = ("_executor", "_lock", "_callbacks")

    _executor: ThreadPoolExecutor
    _lock: threading.RLock
    _callbacks: dict[_KeyT, list[_CallbackT]]

    def __init__(self, executor: ThreadPoolExecutor) -> None:
        """Initialize the callback manager.

        Args:
            executor: Thread pool executor for executing callbacks.
        """
        self._executor = executor
        self._lock = threading.RLock()
        self._callbacks = {}

    def register_callback(self, key: _KeyT, callback: _CallbackT) -> None:
        """Register a callback for the specified key."""
        if not callable(callback):
            raise TypeError("Provided callback is not callable")

        with self._lock:
            self._callbacks.setdefault(key, []).append(callback)

    def unregister_callback(self, key: _KeyT, callback: _CallbackT) -> None:
        """Unregister a callback for the specified key."""
        with self._lock:
            callbacks = self._callbacks.get(key)
            if not callbacks:
                logger.warning("No callbacks registered for key: %s", key)
                return

            try:
                callbacks.remove(callback)
            except ValueError:
                logger.warning("Callback not found for key %s: %s", key, callback)
                return

            if not callbacks:
                del self._callbacks[key]

    def dispatch_one(self, key: _KeyT, value: _ValueT) -> None:
        """Dispatch a single callback for the specified key."""
        with self._lock:
            callbacks = self._callbacks.pop(key, [])

        if callbacks:
            callback = callbacks.pop(0)
            try:
                future = self._executor.submit(callback, value)
                future.add_done_callback(lambda f: f.result())  # Ensure exceptions are raised in the thread
            finally:
                if callbacks:
                    with self._lock:
                        # Merge the remaining callbacks back into the dictionary
                        new_callbacks = self._callbacks.pop(key, [])
                        callbacks.extend(new_callbacks)
                        self._callbacks[key] = callbacks
        else:
            logger.debug("No callbacks registered for key: %s", key)

    def close(self) -> None:
        """Close the callback manager and clear all registered callbacks."""
        with self._lock:
            self._callbacks.clear()


class SyncStreamManager(StreamManager[Http2Stream]):
    """Thread-safe manager for HTTP/2 stream lifecycle management."""

    __slots__ = (
        "_executor",
        "_lock",
        "_streams",
        "_count",
        "_count_monitor",
        "_stream_handler",
    )

    _executor: ThreadPoolExecutor
    _lock: threading.RLock
    _streams: dict[int, Http2Stream]
    _count: int
    _count_monitor: Callable[[int], None]
    _stream_handler: Optional[Http2StreamHandlerType]

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        stream_handler: Optional[Http2StreamHandlerType] = None,
        count_monitor: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Initialize the stream manager."""
        self._executor = executor
        self._lock = threading.RLock()

        self._streams = {}
        self._count = 0

        self._stream_handler = stream_handler
        self._count_monitor = count_monitor or (lambda _: None)

    @property
    def num_active_streams(self) -> int:
        """Get the count of currently active streams."""
        with self._lock:
            return self._count

    def register_stream(self, stream: Http2Stream) -> None:
        """Register a new stream for management."""
        if stream.id <= 0:
            raise H2ProtocolError("Cannot register stream with invalid stream_id")

        with self._lock:
            self._streams[stream.id] = stream
            self._count += 1

        self._count_monitor(self._count)

        if self._stream_handler:
            future = self._executor.submit(self._stream_handler, stream)
            future.add_done_callback(lambda f: f.result())  # Ensure exceptions are raised in the thread

    def unregister_stream(self, stream: Http2Stream) -> None:
        """Unregister a stream from management."""
        with self._lock:
            removed_stream = self._streams.pop(stream.id, None)
            if removed_stream:
                self._count -= 1

        if removed_stream:
            self._count_monitor(self._count)

    def get_stream(self, stream_id: int) -> Optional[Http2Stream]:
        """Get a stream by its ID."""
        with self._lock:
            return self._streams.get(stream_id)

    def _handle_connection_window_update(self, event: h2_events.WindowUpdated) -> None:
        """Handle connection-level window update events."""
        with self._lock:
            streams_list = list(self._streams.values())

        for stream in streams_list:
            stream.handle_event(event)

    def dispatch_event(self, event: h2_events.Event) -> None:
        """Dispatch HTTP/2 events to appropriate handlers."""
        if isinstance(event, h2_events.WindowUpdated) and event.stream_id == 0:
            self._handle_connection_window_update(event)
        else:
            stream_id = getattr(event, "stream_id", 0)
            stream = self.get_stream(stream_id)
            if stream:
                stream.handle_event(event)
            else:
                logger.warning("Stream %s not found for event: %s", stream_id, event)

    def dispatch_connection_error(self, error: H2ConnectionError) -> None:
        last_stream_id = getattr(error, "last_stream_id", 0)
        # Collect and remove affected streams safely
        with self._lock:
            affected_streams = [
                stream for stream_id, stream in list(self._streams.items()) if stream_id > last_stream_id
            ]

            for stream in affected_streams:
                del self._streams[stream.id]
                stream.handle_connection_error(error)
