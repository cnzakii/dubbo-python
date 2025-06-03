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
from collections.abc import Awaitable
from typing import Callable, Generic, Optional, TypeVar

from anyio import abc as anyio_abc
from h2 import events as h2_events

from dubbo.logger import logger

from ..base import AsyncHttp2Stream, AsyncHttp2StreamHandlerType, CallbackManager, StreamManager
from ..exceptions import H2ConnectionError, H2ProtocolError

__all__ = ["AsyncCallbackManager", "AsyncStreamManager"]

_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")
_CallbackT = Callable[[_ValueT], Awaitable[None]]


class AsyncCallbackManager(CallbackManager[_KeyT, _ValueT, _CallbackT], Generic[_KeyT, _ValueT]):
    """Manages asynchronous callback registration and dispatch for keyed events."""

    __slots__ = ("_tg", "_callbacks")

    _tg: anyio_abc.TaskGroup
    _callbacks: dict[_KeyT, list[_CallbackT]]

    def __init__(self, task_group: anyio_abc.TaskGroup) -> None:
        self._tg = task_group
        self._callbacks = {}

    def register_callback(self, key: _KeyT, callback: _CallbackT) -> None:
        """Register a callback for the specified key."""
        if not callable(callback):
            raise TypeError("Provided callback is not callable")

        self._callbacks.setdefault(key, []).append(callback)

    def unregister_callback(self, key: _KeyT, callback: _CallbackT) -> None:
        """Unregister a specific callback for the given key."""
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
        callbacks = self._callbacks.pop(key, [])
        if callbacks:
            callback = callbacks.pop(0)
            try:
                self._tg.start_soon(callback, value)
            except Exception as e:
                logger.error("Error executing callback for key %s: %s", key, e)
            finally:
                # Restore remaining callbacks if any exist
                if callbacks:
                    self._callbacks[key] = callbacks
        else:
            logger.debug("No callbacks registered for key: %s, value: %s", key, value)

    def close(self) -> None:
        self._callbacks.clear()


class AsyncStreamManager(StreamManager[AsyncHttp2Stream]):
    """Manages HTTP/2 streams and handles stream lifecycle events.

    This class provides centralized management for HTTP/2 streams, including
    registration, unregistration, and event dispatching. It maintains stream
    count monitoring and handles connection-level events that affect multiple streams.
    """

    __slots__ = ("_tg", "_streams", "_last_active_stream_id", "_count", "_count_monitor", "_stream_handler")

    _tg: anyio_abc.TaskGroup
    _streams: dict[int, AsyncHttp2Stream]
    _count: int
    _count_monitor: Callable[[int], None]
    _stream_handler: Optional[AsyncHttp2StreamHandlerType]

    def __init__(
        self,
        task_group: anyio_abc.TaskGroup,
        stream_handler: Optional[AsyncHttp2StreamHandlerType] = None,
        count_monitor: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Initialize the stream manager."""
        self._tg = task_group
        self._streams: dict[int, AsyncHttp2Stream] = {}
        self._count = 0
        self._stream_handler = stream_handler
        self._count_monitor = count_monitor or (lambda _: None)

    @property
    def num_active_streams(self) -> int:
        """Get the count of currently active streams."""
        return self._count

    def register_stream(self, stream: AsyncHttp2Stream) -> None:
        """Register a stream with the manager."""
        if stream.id <= 0:
            raise H2ProtocolError("Cannot register stream with invalid stream_id")

        self._streams[stream.id] = stream
        self._count += 1
        self._count_monitor(self._count)

        # Start the stream handler asynchronously if configured
        if self._stream_handler:
            self._tg.start_soon(self._stream_handler, stream)

    def unregister_stream(self, stream: AsyncHttp2Stream) -> None:
        """Unregister a stream from the manager.

        Args:
            stream: The HTTP/2 stream instance to remove.
        """
        removed_stream = self._streams.pop(stream.id, None)
        if removed_stream:
            self._count -= 1
            self._count_monitor(self._count)

    def get_stream(self, stream_id: int) -> Optional[AsyncHttp2Stream]:
        """Retrieve a stream by its ID."""
        return self._streams.get(stream_id)

    def _handle_connection_window_update(self, event: h2_events.WindowUpdated) -> None:
        """Handle connection-level window update events."""
        for stream in self._streams.values():
            stream.handle_event(event)

    def dispatch_event(self, event: h2_events.Event) -> None:
        """Dispatch HTTP/2 events to appropriate handlers."""
        # Handle some special events
        if isinstance(event, h2_events.WindowUpdated) and event.stream_id == 0:
            self._handle_connection_window_update(event)
        else:
            # Handle stream-specific events
            stream_id = event.stream_id  # type: ignore[attr-defined]
            stream = self.get_stream(stream_id)
            if stream:
                stream.handle_event(event)
            else:
                logger.warning("Stream %s not found for event: %s", stream_id, event)

    def dispatch_connection_error(self, error: H2ConnectionError) -> None:
        last_stream_id = getattr(error, "last_stream_id", 0)
        # Collect and remove affected streams safely
        affected_streams = [stream for stream_id, stream in list(self._streams.items()) if stream_id > last_stream_id]

        for stream in affected_streams:
            del self._streams[stream.id]
            stream.handle_connection_error(error)
