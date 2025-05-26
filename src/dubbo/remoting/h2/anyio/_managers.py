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
from dubbo.remoting.h2 import AsyncHttp2Stream, AsyncHttp2StreamHandlerType
from dubbo.remoting.h2.exceptions import H2ProtocolError

_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")
_CallbackT = Callable[[_ValueT], Awaitable[None]]

__all__ = ["CallbackManager", "StreamManager"]


class CallbackManager(Generic[_KeyT, _ValueT]):
    """Manages asynchronous callback registration and dispatch for keyed events.

    This class provides a mechanism for registering callbacks with specific keys
    and dispatching them asynchronously when events occur. Callbacks are executed
    in the provided task group to ensure proper concurrency management.

    Type parameters:
        _KeyT: The type of keys used to identify callback groups.
        _ValueT: The type of values passed to callbacks when dispatched.
    """

    __slots__ = ("_tg", "_callbacks")

    _tg: anyio_abc.TaskGroup
    _callbacks: dict[_KeyT, list[_CallbackT]]

    def __init__(self, task_group: anyio_abc.TaskGroup) -> None:
        """Initialize the callback manager.

        Args:
            task_group: The AnyIO task group used for executing callbacks.
        """
        self._tg = task_group
        self._callbacks = {}

    def register(self, key: _KeyT, callback: _CallbackT) -> None:
        """Register a callback for the specified key.

        Args:
            key: The key to associate with the callback.
            callback: The async callable to register.

        Raises:
            TypeError: If the provided callback is not callable.
        """
        if not callable(callback):
            raise TypeError("Provided callback is not callable")

        self._callbacks.setdefault(key, []).append(callback)

    def unregister(self, key: _KeyT, callback: _CallbackT) -> None:
        """Unregister a specific callback for the given key.

        Args:
            key: The key associated with the callback.
            callback: The callback to remove.
        """
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

    async def dispatch_one(self, key: _KeyT, value: _ValueT) -> None:
        """Dispatch a single callback for the specified key.

        This method removes and executes the first callback registered for the key.
        If the callback execution fails, an error is logged but execution continues.
        Any remaining callbacks for the key are preserved.

        Args:
            key: The key to dispatch callbacks for.
            value: The value to pass to the callback.
        """
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

    async def aclose(self) -> None:
        """Close the callback manager and release all resources.

        This method clears all registered callbacks and should be called
        when the manager is no longer needed.
        """
        self._callbacks.clear()


class StreamManager:
    """Manages HTTP/2 streams and handles stream lifecycle events.

    This class provides centralized management for HTTP/2 streams, including
    registration, unregistration, and event dispatching. It maintains stream
    count monitoring and handles connection-level events that affect multiple streams.
    """

    __slots__ = ("_tg", "_streams", "_last_active_stream_id", "_count", "_count_monitor", "_stream_handler")

    _tg: anyio_abc.TaskGroup
    _streams: dict[int, AsyncHttp2Stream]
    _last_active_stream_id: int
    _count: int
    _count_monitor: Callable[[int], None]
    _stream_handler: Optional[AsyncHttp2StreamHandlerType]

    def __init__(
        self,
        task_group: anyio_abc.TaskGroup,
        stream_handler: Optional[AsyncHttp2StreamHandlerType] = None,
        count_monitor: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Initialize the stream manager.

        Args:
            task_group: The AnyIO task group for executing stream handlers.
            stream_handler: Optional handler function for newly registered streams.
            count_monitor: Optional callback for monitoring stream count changes.
        """
        self._tg = task_group
        self._streams = {}
        self._last_active_stream_id = 0
        self._count = 0
        self._stream_handler = stream_handler
        self._count_monitor = count_monitor or (lambda _: None)

    def register(self, stream: AsyncHttp2Stream) -> None:
        """Register a stream with the manager.

        Args:
            stream: The HTTP/2 stream instance to register.

        Raises:
            H2ProtocolError: If the stream has an invalid ID or violates stream ordering.
        """
        if stream.stream_id <= 0:
            raise H2ProtocolError("Cannot register stream with invalid stream_id")

        if stream.stream_id < self._last_active_stream_id:
            raise H2ProtocolError("Cannot register stream with ID lower than last active stream")

        self._streams[stream.stream_id] = stream
        self._last_active_stream_id = stream.stream_id
        self._count += 1
        self._count_monitor(self._count)

        logger.debug(
            "Registered stream: id=%d, total=%d",
            stream.stream_id,
            self._count,
        )

        # Start the stream handler asynchronously if configured
        if self._stream_handler:
            self._tg.start_soon(self._stream_handler, stream)

    def unregister(self, stream: AsyncHttp2Stream) -> None:
        """Unregister a stream from the manager.

        Args:
            stream: The HTTP/2 stream instance to remove.
        """
        removed_stream = self._streams.pop(stream.stream_id, None)
        if removed_stream:
            self._count -= 1
            self._count_monitor(self._count)
            logger.debug(
                "Unregistered stream: id=%d, remaining=%d",
                stream.stream_id,
                self._count,
            )

    def get_stream(self, stream_id: int) -> Optional[AsyncHttp2Stream]:
        """Retrieve a stream by its ID.

        Args:
            stream_id: The ID of the stream to retrieve.

        Returns:
            The stream instance if found, None otherwise.
        """
        return self._streams.get(stream_id)

    async def _handle_connection_window_update(self, event: h2_events.WindowUpdated) -> None:
        """Handle connection-level window update events.

        Distributes window update events to all active streams since connection-level
        window updates affect the flow control of all streams.

        Args:
            event: The window update event to distribute.
        """
        for stream in self._streams.values():
            await stream.handle_event(event)

    async def _handle_connection_termination(self, event: h2_events.ConnectionTerminated) -> None:
        """Handle connection termination events.

        When a connection is terminated, all streams with IDs greater than the
        last_stream_id should be removed and notified of the termination.

        Args:
            event: The connection termination event.
        """
        last_stream_id = event.last_stream_id or 0

        # Collect streams to remove (avoid modifying dict during iteration)
        streams_to_remove = [
            (stream_id, stream) for stream_id, stream in self._streams.items() if stream_id > last_stream_id
        ]

        if streams_to_remove:
            # Remove streams from the manager
            for stream_id, _ in streams_to_remove:
                del self._streams[stream_id]

            # Notify removed streams of the termination
            for _, stream in streams_to_remove:
                await stream.handle_event(event)

            # Update counters
            self._count -= len(streams_to_remove)
            self._count_monitor(self._count)

            logger.debug(
                "Dispatched ConnectionTerminated event and removed %d streams (ID > %d)",
                len(streams_to_remove),
                last_stream_id,
            )

    async def dispatch_event(self, event: h2_events.Event) -> None:
        """Dispatch HTTP/2 events to appropriate handlers.

        Connection-level events (WindowUpdated with stream_id=0, ConnectionTerminated)
        are handled specially, while stream-specific events are forwarded to the
        corresponding stream.

        Args:
            event: The HTTP/2 event to dispatch.
        """
        # Handle some special events
        if isinstance(event, h2_events.WindowUpdated) and event.stream_id == 0:
            await self._handle_connection_window_update(event)
        elif isinstance(event, h2_events.ConnectionTerminated):
            await self._handle_connection_termination(event)
        else:
            # Handle stream-specific events
            stream_id = getattr(event, "stream_id", 0)
            stream = self.get_stream(stream_id)
            if stream:
                await stream.handle_event(event)
            else:
                logger.warning("Stream %s not found for event: %s", stream_id, event)

    async def aclose(self) -> None:
        """Close the stream manager and release all resources.

        This method clears all registered streams and should be called when
        the manager is no longer needed.
        """
        self._streams.clear()
        self._last_active_stream_id = 0
        if self._count > 0:
            self._count_monitor(0)
        self._count = 0
