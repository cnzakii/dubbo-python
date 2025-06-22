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
from contextlib import contextmanager
from typing import Optional

from dubbo.common import URL, constants
from dubbo.logger import logger
from dubbo.protocol.exceptions import RpcError
from dubbo.registry.base import NotifyListenerType, Registry
from dubbo.remoting.zookeeper import ChildrenListenerType, ConnectionState, ZookeeperClient

from ._base import BaseZookeeperRegistry

__all__ = ["ZookeeperRegistry"]


class ZookeeperRegistry(Registry, BaseZookeeperRegistry):
    _url: URL
    _client: ZookeeperClient

    # some attributes to hold the state of the registry
    _root: str
    _listener_map: dict[tuple[str, NotifyListenerType], ChildrenListenerType]

    def __init__(self, url: URL, client: ZookeeperClient) -> None:
        self._url = url
        self._client = client
        self._root = constants.DUBBO

        self._initialize()

    def _initialize(self) -> None:
        # set the root path for the registry
        group = self._url.get_group(self._root)
        self._root = group if group.startswith("/") else f"/{group}"

        # add state listener
        def _state_listener(state: ConnectionState) -> None:
            """Handle state changes of the Zookeeper connection."""
            if state == ConnectionState.LOST:
                logger.warning("[AsyncZookeeperRegistry] Connection lost...")
            # TODO: Handle other states like CONNECTED, SUSPENDED, etc.

        self._client.add_state_listener(_state_listener)

    @contextmanager
    def _guard(self, err_msg: Optional[str] = None):
        """Context manager to guard the execution of registry operations.
        Raises RpcError if the Zookeeper client is not available or if an exception occurs.
        """
        if not self.is_available():
            raise RpcError("Zookeeper client is not available.")
        try:
            yield
        except RpcError:
            raise  # re-raise
        except Exception as e:
            err_msg = err_msg or "An error occurred during registry operation."
            raise RpcError(err_msg) from e

    @property
    def root_path(self) -> str:
        """Return the root path used for service registration."""
        return self._root

    def register(self, url: URL) -> None:
        """Register a URL to the registry."""
        with self._guard(f"Failed to register URL {url} in Zookeeper."):
            self._client.create(
                path=self.get_registry_path(url),
                ephemeral=url.get_param_bool(constants.DYNAMIC_KEY, default=True),
                makepath=True,
            )

    def unregister(self, url: URL) -> None:
        """Unregister a URL from the registry."""
        with self._guard(f"Failed to unregister URL {url} from Zookeeper."):
            self._client.delete(path=self.get_registry_path(url))

    def subscribe(self, url: URL, listener: NotifyListenerType) -> None:
        """Subscribe to a URL in the registry."""

        def _listener_wrapper(children: list[str]) -> None:
            if not children:
                return
            try:
                urls = [URL.from_str(child, decode=True) for child in children]
                listener(urls)
            except Exception as e:
                logger.exception(f"Error in notify listener for path {path}: {e}")

        with self._guard(f"Failed to subscribe to URL {url} in Zookeeper."):
            path = self.get_category_path(url)
            if not self._client.exists(path):
                self._client.create(path, makepath=True)

            key = (path, listener)
            if key not in self._listener_map:
                self._client.add_children_listener(path, _listener_wrapper)
                self._listener_map[key] = _listener_wrapper
            else:
                logger.debug(f"Listener already registered for {path}")

    def unsubscribe(self, url: URL, listener: NotifyListenerType) -> None:
        """Unsubscribe from a URL in the registry."""
        path = self.get_category_path(url)
        key = (path, listener)

        with self._guard(f"Failed to unsubscribe from URL {url} in Zookeeper."):
            wrapper = self._listener_map.pop(key, None)
            if wrapper is not None:
                self._client.remove_children_listener(path, wrapper)
            else:
                logger.warning(f"No listener found for {path} with {listener}")

    def get_url(self) -> URL:
        """Get the URL of the registry."""
        return self._url

    def is_available(self) -> bool:
        """Check if the registry is available."""
        return self._client.connected

    def destroy(self) -> None:
        """Destroy the registry and release resources."""
        if self._client.connected:
            try:
                self._client.close()
            except Exception as e:
                logger.error(f"Error closing Zookeeper client: {e}")
