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
from typing import Optional

from kazoo.client import KazooClient
from kazoo.exceptions import NoNodeError

from dubbo import logger
from dubbo.common import URL, constants
from dubbo.remoting.zookeeper import ChildrenListener, DataListener, StateListener, ZookeeperClient

from ._base import ChildrenMultiListenerAdapterFactory, DataMultiListenerAdapterFactory, StateMultiListenerAdapter

_LOGGER = logger.get_instance()


class KazooZookeeperClient(ZookeeperClient):
    """
    Zookeeper client implementation using Kazoo.
    """

    __slots__ = ("_kazoo", "_state_adapter", "_data_factory", "_children_factory")

    _kazoo: KazooClient
    _state_adapter: StateMultiListenerAdapter
    _data_factory: DataMultiListenerAdapterFactory
    _children_factory: ChildrenMultiListenerAdapterFactory

    def __init__(self, url: URL) -> None:
        timeout = url.get_param_float(constants.TIMEOUT_KEY, constants.DEFAULT_TIMEOUT_VALUE)
        auth = [("digest", url.userinfo)] if url.userinfo else None
        self._kazoo = KazooClient(hosts=url.location, timeout=timeout, auth_data=auth, logger=_LOGGER)
        self._data_factory = DataMultiListenerAdapterFactory(self._kazoo)
        self._children_factory = ChildrenMultiListenerAdapterFactory(self._kazoo)
        self._state_adapter = StateMultiListenerAdapter("/")
        self._kazoo.add_listener(self._state_adapter)

    def start(self) -> None:
        self._kazoo.start()

    def connected(self) -> bool:
        return self._kazoo.connected

    def close(self) -> None:
        self._kazoo.stop()
        self._kazoo.close()

    def create(
        self,
        path: str,
        value: bytes = b"",
        acl: Optional[list] = None,
        ephemeral: bool = False,
        sequence: bool = False,
        makepath: bool = False,
    ) -> str:
        return self._kazoo.create(path, value, acl, ephemeral, sequence, makepath)

    def exists(self, path: str) -> bool:
        return self._kazoo.exists(path) is not None

    def delete(self, path: str, recursive: bool = False) -> None:
        try:
            self._kazoo.delete(path, recursive=recursive)
        except NoNodeError:
            pass

    def get_data(self, path: str) -> bytes:
        data, _ = self._kazoo.get(path)
        return data

    def get_children(self, path: str) -> list[str]:
        return self._kazoo.get_children(path)

    def add_state_listener(self, listener: StateListener) -> None:
        self._state_adapter.add(listener)

    def remove_state_listener(self, listener: StateListener) -> None:
        self._state_adapter.remove(listener)

    def add_data_listener(self, path: str, listener: DataListener) -> None:
        self._data_factory.add_listener(path, listener)

    def remove_data_listener(self, path: str, listener: DataListener) -> None:
        self._data_factory.remove_listener(path, listener)

    def add_children_listener(self, path: str, listener: ChildrenListener) -> None:
        self._children_factory.add_listener(path, listener)

    def remove_children_listener(self, path: str, listener: ChildrenListener) -> None:
        self._children_factory.remove_listener(path, listener)
