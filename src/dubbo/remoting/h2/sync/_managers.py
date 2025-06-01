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
from dubbo.remoting.h2 import Http2Stream, Http2StreamHandlerType
from dubbo.remoting.h2.exceptions import H2ProtocolError

__all__ = ["CallbackManager", "StreamManager"]

_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")
_CallbackT = Callable[[_ValueT], None]


class CallbackManager(Generic[_KeyT, _ValueT]):
    """Thread-safe manager for callback registration and dispatch.

    This class provides a mechanism for registering callbacks with specific keys
    and dispatching them using a thread pool executor. All operations are thread-safe
    and callbacks are executed asynchronously.

    Type parameters:
        _KeyT: The type of keys used to identify callback groups.
        _ValueT: The type of values passed to callbacks when dispatched.
    """

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

    def register(self, key: _KeyT, callback: _CallbackT) -> None:
        """Register a callback for the specified key.

        Args:
            key: The key to associate with the callback.
            callback: The callable to register for this key.

        Raises:
            TypeError: If the callback is not callable.
        """
        if not callable(callback):
            raise TypeError("Provided callback is not callable")

        with self._lock:
            self._callbacks.setdefault(key, []).append(callback)

    def unregister(self, key: _KeyT, callback: _CallbackT) -> None:
        """Unregister a callback for the specified key.

        Args:
            key: The key associated with the callback.
            callback: The callable to unregister for this key.
        """
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
        """Dispatch a single callback for the specified key.

        Removes and executes the first callback associated with the key.
        The callback is executed asynchronously in the thread pool.

        Args:
            key: The key to dispatch callbacks for.
            value: The value to pass to the callback.
        """
        with self._lock:
            callbacks = self._callbacks.pop(key, [])

        if callbacks:
            callback = callbacks.pop(0)
            try:
                self._executor.submit(callback, value)
            except Exception as e:
                logger.error("Error executing callback for key %s: %s", key, e)
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
        """Close the callback manager and clear all registered callbacks.

        This method clears all registered callbacks and releases resources.
        No callbacks will be dispatched after this method is called.
        """
        with self._lock:
            self._callbacks.clear()


class StreamManager:
    """Thread-safe manager for HTTP/2 stream lifecycle management.

    This class manages the registration, tracking, and lifecycle of HTTP/2 streams
    in a synchronous environment. It provides thread-safe operations for stream
    registration/unregistration and event dispatching.
    """

    __slots__ = (
        "_executor",
        "_lock",
        "_streams",
        "_last_active_stream_id",
        "_count",
        "_count_monitor",
        "_stream_handler",
    )

    _executor: ThreadPoolExecutor
    _lock: threading.RLock
    _streams: dict[int, Http2Stream]
    _last_active_stream_id: int
    _count: int
    _count_monitor: Callable[[int], None]
    _stream_handler: Optional[Http2StreamHandlerType]

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        stream_handler: Optional[Http2StreamHandlerType] = None,
        count_monitor: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Initialize the stream manager.

        Args:
            executor: Thread pool executor for executing stream handlers.
            stream_handler: Optional handler for new streams.
            count_monitor: Optional callback for monitoring stream count changes.
        """
        self._executor = executor
        self._lock = threading.RLock()

        self._streams = {}
        self._last_active_stream_id = 0
        self._count = 0

        self._stream_handler = stream_handler
        self._count_monitor = count_monitor or (lambda _: None)

    @property
    def count(self) -> int:
        """Get the current count of registered streams."""
        with self._lock:
            return self._count

    def register(self, stream: Http2Stream) -> None:
        """Register a new stream for management.

        Args:
            stream: The HTTP/2 stream to register.

        Raises:
            H2ProtocolError: If the stream ID is invalid or out of order.
        """
        if stream.stream_id <= 0:
            raise H2ProtocolError("Cannot register stream with invalid stream_id")

        if stream.stream_id < self._last_active_stream_id:
            raise H2ProtocolError("Cannot register stream with ID lower than last active stream")

        with self._lock:
            self._streams[stream.stream_id] = stream
            self._last_active_stream_id = stream.stream_id
            self._count += 1

        self._count_monitor(self._count)
        logger.debug(
            "Registered stream: id=%d, total=%d",
            stream.stream_id,
            self._count,
        )

        if self._stream_handler:
            self._executor.submit(self._stream_handler, stream)

    def unregister(self, stream: Http2Stream) -> None:
        """Unregister a stream from management.

        Args:
            stream: The HTTP/2 stream to unregister.
        """
        with self._lock:
            removed_stream = self._streams.pop(stream.stream_id, None)
            if removed_stream:
                self._count -= 1

        if removed_stream:
            self._count_monitor(self._count)
            logger.debug(
                "Unregistered stream: id=%d, remaining=%d",
                stream.stream_id,
                self._count,
            )

    def get_stream(self, stream_id: int) -> Optional[Http2Stream]:
        """Get a stream by its ID.

        Args:
            stream_id: The ID of the stream to retrieve.

        Returns:
            The stream if found, None otherwise.
        """
        with self._lock:
            return self._streams.get(stream_id)

    def _handle_connection_window_update(self, event: h2_events.WindowUpdated) -> None:
        """Handle connection-level window update events.

        Dispatches the window update event to all active streams.

        Args:
            event: The window update event to handle.
        """
        with self._lock:
            streams_list = list(self._streams.values())

        for stream in streams_list:
            stream.handle_event(event)

    def _handle_connection_termination(self, event: h2_events.ConnectionTerminated) -> None:
        """Handle connection termination events.

        Removes and notifies all streams with IDs greater than the last stream ID
        specified in the termination event.

        Args:
            event: The connection termination event to handle.
        """
        last_stream_id = event.last_stream_id or 0
        removed_streams = []

        with self._lock:
            # Create a list of stream IDs to remove to avoid modifying dict during iteration
            stream_ids_to_remove = [stream_id for stream_id in self._streams.keys() if stream_id > last_stream_id]

            for stream_id in stream_ids_to_remove:
                stream = self._streams.pop(stream_id)
                removed_streams.append(stream)

        if removed_streams:
            for stream in removed_streams:
                stream.handle_event(event)
            self._count -= len(removed_streams)
            self._count_monitor(self._count)

            logger.debug(
                "Dispatched ConnectionTerminated event and removed %d streams (ID > %d)",
                len(removed_streams),
                last_stream_id,
            )

    def dispatch_event(self, event: h2_events.Event) -> None:
        """Dispatch HTTP/2 events to appropriate handlers.

        Connection-level events (WindowUpdated with stream_id=0, ConnectionTerminated)
        are handled specially, while stream-specific events are forwarded to the
        corresponding stream.

        Args:
            event: The HTTP/2 event to dispatch.
        """
        if isinstance(event, h2_events.WindowUpdated) and event.stream_id == 0:
            self._handle_connection_window_update(event)
        elif isinstance(event, h2_events.ConnectionTerminated):
            self._handle_connection_termination(event)
        else:
            stream_id = getattr(event, "stream_id", 0)
            stream = self.get_stream(stream_id)
            if stream:
                stream.handle_event(event)
            else:
                logger.warning("Stream %s not found for event: %s", stream_id, event)

    def close(self) -> None:
        """Close the stream manager and clear all registered streams.

        This method removes all registered streams and resets the internal state.
        """
        original_count = self._count
        with self._lock:
            self._streams.clear()
            self._last_active_stream_id = 0
            self._count = 0
        if original_count != self._count:
            self._count_monitor(self._count)
