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
import inspect
from typing import Any, Callable, Optional

from anyio import from_thread
from kazoo.client import KazooClient
from kazoo.protocol.states import KazooState, WatchedEvent, ZnodeStat

from dubbo.logger import logger
from dubbo.remoting.zookeeper import ConnectionState, DataEventType

__all__ = [
    "StateListenerAdapter",
    "DataListenerAdapter",
    "ChildrenListenerAdapter",
    "DataAdapterFactory",
    "ChildrenAdapterFactory",
]


class BaseListenerAdapter(abc.ABC):
    """
    Base class for multi-listener adapters.
    Supports synchronous and asynchronous notifications.
    """

    _path: str
    _listeners: list[Callable[..., Any]]
    _is_async: bool

    def __init__(self, path: str, is_async: bool = False) -> None:
        self._path = path
        self._listeners: list[Callable[..., Any]] = []
        self._is_async = is_async

    def add(self, listener: Callable[..., Any]) -> None:
        """
        Add a listener to the adapter.

        Args:
            listener: The listener callable.
        """
        if not callable(listener):
            raise TypeError(f"Listener must be callable, got {type(listener).__name__}")
        if self._is_async and not inspect.iscoroutinefunction(listener):
            raise TypeError("Async adapter requires coroutine listeners.")
        self._listeners.append(listener)

    def remove(self, listener: Callable[..., Any]) -> None:
        """
        Remove a listener from the adapter.

        Args:
            listener: The listener to remove.
        """
        self._listeners.remove(listener)

    def clear(self) -> None:
        """
        Clear all listeners.
        """
        self._listeners.clear()

    def __call__(self, *args: Any) -> None:
        """Dispatch notification to all listeners."""
        if self._is_async:
            self._notify_async(*args)
        else:
            self._notify_sync(*args)

    def _notify_sync(self, *args: Any) -> None:
        parsed_args = self._parse_args(*args)
        if not parsed_args:
            return
        for listener in self._listeners:
            try:
                listener(*parsed_args)
            except Exception as e:
                logger.warning("Error in sync listener: %s", e, exc_info=True)

    def _notify_async(self, *args: Any) -> None:
        parsed_args = self._parse_args(*args)
        if not parsed_args:
            return

        async def _runner(listeners, *_args):
            for listener in listeners:
                try:
                    await listener(*_args)
                except Exception as e:
                    logger.warning("Error in async listener: %s", e, exc_info=True)

        from_thread.run(_runner, self._listeners, *parsed_args)

    @abc.abstractmethod
    def _parse_args(self, *args: Any) -> Optional[tuple]:
        """Transform input args into listener arguments."""
        raise NotImplementedError()


class StateListenerAdapter(BaseListenerAdapter):
    """Adapter for Zookeeper connection state change."""

    def _parse_args(self, *args: Any) -> Optional[tuple]:
        if len(args) != 1 or not isinstance(args[0], KazooState):
            logger.error("Expected single KazooState, got: %s", args)
            return None
        try:
            return (ConnectionState(args[0]),)
        except ValueError:
            logger.error("Unknown connection state: %s", args[0])
            return None


class DataListenerAdapter(BaseListenerAdapter):
    """Adapter for Znode data change events."""

    def _parse_args(self, *args: Any) -> Optional[tuple]:
        if len(args) != 3:
            logger.error("Expected (bytes, ZnodeStat, WatchedEvent), got: %s", args)
            return None
        data, stat, event = args
        if not isinstance(data, bytes) or not isinstance(stat, ZnodeStat) or not isinstance(event, WatchedEvent):
            logger.error("Invalid data types: %s", args)
            return None
        try:
            return event.path, data, DataEventType(event.type)
        except ValueError:
            logger.error("Unknown event type: %s", event.type)
            return None


class ChildrenListenerAdapter(BaseListenerAdapter):
    """Adapter for Znode children change events."""

    def _parse_args(self, *args: Any) -> Optional[tuple]:
        if len(args) != 1 or not isinstance(args[0], list):
            logger.error("Expected single list argument, got: %s", args)
            return None
        return (args[0],)


class ListenerAdapterFactory(abc.ABC):
    """
    Factory for managing listener adapters per Znode path.
    """

    _client: KazooClient
    _adapters: dict[str, BaseListenerAdapter]
    _is_async: bool

    def __init__(self, client, is_async: bool = False) -> None:
        self._client = client
        self._is_async = is_async
        self._adapters: dict[str, BaseListenerAdapter] = {}

    def add_listener(self, path: str, listener: Callable[..., Any]) -> None:
        adapter = self._adapters.get(path)
        if not adapter:
            adapter = self._create_adapter(path)
            self._adapters[path] = adapter
        adapter.add(listener)

    def remove_listener(self, path: str, listener: Callable[..., Any]) -> None:
        adapter = self._adapters.get(path)
        if adapter:
            adapter.remove(listener)

    @abc.abstractmethod
    def _create_adapter(self, path: str) -> BaseListenerAdapter:
        """Create and attach adapter to client watch."""
        raise NotImplementedError()


class DataAdapterFactory(ListenerAdapterFactory):
    """
    Factory for managing data change listeners.
    """

    def _create_adapter(self, path: str) -> BaseListenerAdapter:
        adapter = DataListenerAdapter(path, self._is_async)
        self._client.DataWatch(path)(adapter)
        return adapter


class ChildrenAdapterFactory(ListenerAdapterFactory):
    """
    Factory for managing children change listeners.
    """

    def _create_adapter(self, path: str) -> BaseListenerAdapter:
        adapter = ChildrenListenerAdapter(path, self._is_async)
        self._client.ChildrenWatch(path)(adapter)
        return adapter
