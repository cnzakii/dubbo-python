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

from dubbo.remoting.zookeeper import ConnectionState, DataEventType


class BaseMultiListenerAdapter(abc.ABC):
    """
    Base class for multi-listener adapters.
    Supports both sync and async listener notification.
    """

    _path: str
    _listeners: list[Callable[..., Any]]
    _is_async: bool

    def __init__(self, path: str, is_async: bool = False) -> None:
        self._path = path
        self._listeners = []
        self._is_async = is_async

    def add(self, listener: Callable[..., Any]) -> None:
        """
        Add a listener to the adapter.

        :param listener: The listener callable.
        """
        if not callable(listener):
            raise TypeError(f"Listener must be callable, got {type(listener).__name__}")
        if self._is_async and not inspect.iscoroutinefunction(listener):
            raise TypeError(
                f"Listener must be a coroutine function when is_async is True, got {type(listener).__name__}"
            )
        self._listeners.append(listener)

    def remove(self, listener: Callable[..., Any]) -> None:
        """
        Remove a listener from the adapter.

        :param listener: The listener to remove.
        """
        self._listeners.remove(listener)

    def clear(self) -> None:
        """
        Clear all listeners.
        """
        self._listeners.clear()

    def notify(self, *args: Any) -> None:
        """
        Notify all listeners synchronously.

        :param args: Positional arguments.
        """
        new_args = self._construct_args(*args)
        if not self._listeners or new_args is None:
            return

        for listener in self._listeners:
            try:
                listener(*new_args)
            except Exception:  # pragma: nocover
                pass

    def notify_async(self, *args: Any) -> None:
        """
        Notify all listeners asynchronously using AnyIO.

        :param args: Positional arguments.
        """
        new_args = self._construct_args(*args)
        if not self._listeners or not new_args:
            return

        async def _do_notify(_listeners, *_args):
            for listener in _listeners:
                try:
                    await listener(*_args)
                except Exception:  # pragma: nocover
                    pass

        from_thread.run(_do_notify, self._listeners, *new_args)

    def __call__(self, *args: Any) -> None:
        """
        Invoke the notification process.
        """
        if self._is_async:
            self.notify_async(*args)
        else:
            self.notify(*args)

    @abc.abstractmethod
    def _construct_args(self, *args: Any) -> Optional[tuple]:
        """
        Construct arguments for the listener based on the provided args.
        :param args: Positional arguments.
        :return: A tuple of arguments for the listener or None if not applicable.
        :rtype: Optional[tuple]
        """
        raise NotImplementedError()


class StateMultiListenerAdapter(BaseMultiListenerAdapter):
    def _construct_args(self, *args: Any) -> Optional[tuple]:
        if len(args) != 1 or not isinstance(args[0], KazooState):
            return None
        try:
            state = ConnectionState(args[0])
            return (state,)
        except ValueError:
            return None


class DataMultiListenerAdapter(BaseMultiListenerAdapter):
    """
    Listener adapter for Znode data changes.
    """

    def _construct_args(self, *args: Any) -> Optional[tuple]:
        """
        Construct arguments for the listener based on the provided args.
        """
        if len(args) != 3 or not (
            isinstance(args[0], bytes) and isinstance(args[1], ZnodeStat) and isinstance(args[2], WatchedEvent)
        ):
            return None

        data, stat, event = args
        try:
            event_type = DataEventType(event.type)
            return event.path, data, event_type
        except ValueError:
            return None


class ChildrenMultiListenerAdapter(BaseMultiListenerAdapter):
    """
    Listener adapter for Znode children changes.
    """

    def _construct_args(self, *args: Any) -> Optional[tuple]:
        """
        Construct arguments for the listener based on the provided args.
        """
        if len(args) != 1 or not isinstance(args[0], list):
            return None
        return (args[0],)


class BaseMultiListenerAdapterFactory(abc.ABC):
    """
    Abstract factory for creating and managing listener adapters for specific paths.
    """

    __slots__ = ("_client", "_adapters", "_is_async")

    _client: KazooClient
    _adapters: dict[str, BaseMultiListenerAdapter]
    _is_async: bool

    def __init__(self, client: KazooClient, is_async: bool = False) -> None:
        self._client = client
        self._adapters = {}
        self._is_async = is_async

    def add_listener(self, path: str, listener: Any) -> None:
        adapter = self._adapters.get(path)
        if adapter is not None:
            adapter.add(listener)
            return
        adapter = self._adapters.setdefault(path, self._create(path))
        adapter.add(listener)

    @abc.abstractmethod
    def _create(self, path: str) -> BaseMultiListenerAdapter:
        """Create a new adapter for the given path."""
        raise NotImplementedError()

    def remove_listener(self, path: str, listener: Any) -> None:
        adapter = self._adapters.get(path)
        if adapter:
            adapter.remove(listener)


class DataMultiListenerAdapterFactory(BaseMultiListenerAdapterFactory):
    """
    Factory for `DataMultiListenerAdapter` instances.
    """

    def _create(self, path: str) -> DataMultiListenerAdapter:
        adapter = DataMultiListenerAdapter(path, self._is_async)
        self._client.DataWatch(path)(adapter)
        return adapter


class ChildrenMultiListenerAdapterFactory(BaseMultiListenerAdapterFactory):
    """
    Factory for `ChildrenMultiListenerAdapter` instances.
    """

    def _create(self, path: str) -> ChildrenMultiListenerAdapter:
        adapter = ChildrenMultiListenerAdapter(path, self._is_async)
        self._client.ChildrenWatch(path)(adapter)
        return adapter
