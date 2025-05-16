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
import functools
from typing import Any, Optional

import anyio
from anyio import to_thread
from kazoo.client import KazooClient
from kazoo.exceptions import NoNodeError
from kazoo.interfaces import IAsyncResult

from dubbo import logger
from dubbo.common import URL, constants
from dubbo.remoting.zookeeper import (
    AsyncChildrenListener,
    AsyncDataListener,
    AsyncStateListener,
    AsyncZookeeperClient,
)

from ._base import (
    ChildrenMultiListenerAdapterFactory,
    DataMultiListenerAdapterFactory,
    StateMultiListenerAdapter,
)

_LOGGER = logger.get_instance()


class KazooFuture:
    """
    A simple future class to handle asynchronous operations using anyio.
    """

    def __init__(self) -> None:
        self._event = anyio.Event()
        self._result: Any = None
        self._exc: Optional[Exception] = None

    def set_result(self, result: Any) -> None:
        self._result = result
        self._event.set()

    def set_exception(self, exc: Exception) -> None:
        self._exc = exc
        self._event.set()

    async def result(self) -> Any:
        await self._event.wait()
        if self._exc:
            raise self._exc
        return self._result

    async def exception(self) -> Optional[Exception]:
        await self._event.wait()
        return self._exc

    @staticmethod
    def wrap_async(async_obj: "IAsyncResult") -> "KazooFuture":
        future = KazooFuture()

        def _run(_future: "KazooFuture", _res: "IAsyncResult"):
            try:
                _future.set_result(_res.get())
            except Exception as e:
                _future.set_exception(e)

        func = functools.partial(_run, future)
        async_obj.rawlink(func)
        return future


class AsyncKazooZookeeperClient(AsyncZookeeperClient):
    __slots__ = ("_kazoo", "_state_adapter", "_data_factory", "_children_factory")

    _kazoo: KazooClient
    _state_adapter: StateMultiListenerAdapter
    _data_factory: DataMultiListenerAdapterFactory
    _children_factory: ChildrenMultiListenerAdapterFactory

    def __init__(self, url: URL) -> None:
        timeout = url.get_param_float(constants.TIMEOUT_KEY, constants.DEFAULT_TIMEOUT_VALUE)
        auth = [("digest", url.userinfo)] if url.userinfo else None
        self._kazoo = KazooClient(hosts=url.location, timeout=timeout, auth_data=auth, logger=_LOGGER)
        self._data_factory = DataMultiListenerAdapterFactory(self._kazoo, is_async=True)
        self._children_factory = ChildrenMultiListenerAdapterFactory(self._kazoo, is_async=True)
        self._state_adapter = StateMultiListenerAdapter("/", is_async=True)
        self._kazoo.add_listener(self._state_adapter)

    async def start(self) -> None:
        def _wait_start(_client, _event):
            event.wait(timeout=constants.DEFAULT_TIMEOUT_VALUE)
            if not _client.connected:
                # If the client is not connected, stop and close it
                _client.stop()
                _client.close()
                raise TimeoutError("Zookeeper client connection timed out")

        event = self._kazoo.start_async()
        await to_thread.run_sync(_wait_start, self._kazoo, event)

    def connected(self) -> bool:
        return self._kazoo.connected

    async def aclose(self) -> None:
        def _do_close(_client):
            _client.stop()
            _client.close()

        await to_thread.run_sync(_do_close, self._kazoo)

    async def exists(self, path: str) -> bool:
        def _check_exists(_client, _path):
            return _client.exists(_path) is not None

        return await to_thread.run_sync(_check_exists, self._kazoo, path)

    async def create(
        self,
        path: str,
        value: bytes = b"",
        acl: Optional[list] = None,
        ephemeral: bool = False,
        sequence: bool = False,
        makepath: bool = False,
    ) -> str:
        async_obj = self._kazoo.create_async(path, value, acl, ephemeral, sequence, makepath)
        future = KazooFuture.wrap_async(async_obj)
        return await future.result()

    async def delete(self, path: str, recursive: bool = False) -> None:
        def _do_delete(_client, _path, _recursive):
            try:
                _client.delete(_path, recursive=_recursive)
            except NoNodeError:
                pass

        await to_thread.run_sync(_do_delete, self._kazoo, path, recursive)

    async def get_children(self, path: str) -> list[str]:
        async_obj = self._kazoo.get_children_async(path)
        future = KazooFuture.wrap_async(async_obj)
        return await future.result()

    async def get_data(self, path: str) -> bytes:
        async_obj = self._kazoo.get_async(path)
        future = KazooFuture.wrap_async(async_obj)
        return await future.result()

    async def add_state_listener(self, listener: AsyncStateListener) -> None:
        self._state_adapter.add(listener)

    async def remove_state_listener(self, listener: AsyncStateListener) -> None:
        self._state_adapter.remove(listener)

    async def add_data_listener(self, path: str, listener: AsyncDataListener) -> None:
        self._data_factory.add_listener(path, listener)

    async def remove_data_listener(self, path: str, listener: AsyncDataListener) -> None:
        self._data_factory.remove_listener(path, listener)

    async def add_children_listener(self, path: str, listener: AsyncChildrenListener) -> None:
        self._children_factory.add_listener(path, listener)

    async def remove_children_listener(self, path: str, listener: AsyncChildrenListener) -> None:
        self._children_factory.remove_listener(path, listener)
