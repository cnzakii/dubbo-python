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
import enum
from collections.abc import Awaitable
from typing import Callable, Optional

__all__ = [
    "ConnectionState",
    "DataEventType",
    "StateListener",
    "DataListener",
    "ChildrenListener",
    "ZookeeperClient",
    "AsyncStateListener",
    "AsyncDataListener",
    "AsyncChildrenListener",
    "AsyncZookeeperClient",
]


class ConnectionState(enum.StrEnum):
    """
    Enumeration of possible ZooKeeper connection states.
    """

    SUSPENDED = "SUSPENDED"
    CONNECTED = "CONNECTED"
    LOST = "LOST"


class DataEventType(enum.StrEnum):
    """
    Enumeration of data-related event types.
    """

    CREATED = "CREATED"
    DELETED = "DELETED"
    CHANGED = "CHANGED"
    CHILD = "CHILD"
    NONE = "NONE"


# Called whenever the ZooKeeper connection state changes.
StateListener = Callable[[ConnectionState], None]
# Called whenever a data node event occurs.
DataListener = Callable[[str, bytes, DataEventType], None]
# Called whenever the direct children of a watched path change.
ChildrenListener = Callable[[list[str]], None]


class ZookeeperClient(abc.ABC):
    """
    Abstract base class defining the interface for a ZooKeeper client.

    Subclasses must implement lifecycle management, CRUD operations,
    and listener registration for data and child-node events.
    """

    @abc.abstractmethod
    def start(self) -> None:
        """
        Start the ZooKeeper client and establish a session.

        Opens network connections, negotiates a session with the ensemble,
        and prepares the client to perform operations.

        :raises ZookeeperError: If the connection cannot be established.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def connected(self) -> bool:
        """
        Check if the ZooKeeper client is currently connected.
        :return: True if connected, False otherwise.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """
        Close the ZooKeeper client session and release resources.

        Gracefully shuts down connections, cancels all active watches,
        and cleans up any internal state.

        :raises ZookeeperError: If an error occurs during shutdown.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create(
        self,
        path: str,
        value: bytes = b"",
        acl: Optional[list] = None,
        ephemeral: bool = False,
        sequence: bool = False,
        makepath: bool = False,
    ) -> str:
        """
        Create a node at the given path with the provided data.

        :param path:        The ZooKeeper node path to create.
        :param value:       The initial data for the node (bytes).
        :param acl:         Optional ACL list to apply to the node.
        :param ephemeral:   If True, node is removed when session ends.
        :param sequence:    If True, path is suffixed with a unique sequence number.
        :param makepath:    If True, create missing parent nodes recursively.
        :return:            The real path of the created node, including any sequence suffix.

        :raises NodeExistsError:     If the node already exists (non-sequential).
        :raises NoNodeError:         If a parent node is missing and makepath is False.
        :raises ZookeeperError:      For any other server-side error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def exists(self, path: str) -> bool:
        """
        Check whether a node exists at the specified path.

        :param path: The ZooKeeper node path to check.
        :return:     True if the node exists, False otherwise.

        :raises ZookeeperError: If an error occurs during the check.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def delete(self, path: str, recursive: bool = False) -> None:
        """
        Delete the node at the given path.

        :param path:      The ZooKeeper node path to delete.
        :param recursive: If True, delete all children recursively.

        :raises NoNodeError:    If the node does not exist.
        :raises ZookeeperError: For any other server-side error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_data(self, path: str) -> bytes:
        """
        Retrieve the data stored at the specified node path.

        :param path: The ZooKeeper node path to read.
        :return:     The raw data bytes of the node.

        :raises NoNodeError:    If the node does not exist.
        :raises ZookeeperError: For any other server-side error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_children(self, path: str) -> list[str]:
        """
        List the direct children of the specified node.

        :param path: The ZooKeeper node path whose children to list.
        :return:     A list of child node names.

        :raises NoNodeError:    If the node does not exist.
        :raises ZookeeperError: For any other server-side error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def add_state_listener(self, listener: StateListener) -> None:
        """
        Register a state listener to receive connection state changes.

        :param listener: An implementation of StateListener to receive callbacks.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def remove_state_listener(self, listener: StateListener) -> None:
        """
        Unregister a previously added state listener.

        :param listener: The listener to remove.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def add_data_listener(self, path: str, listener: DataListener) -> None:
        """
        Register a data listener on the specified node.

        The listener will be notified when the node is created, updated,
        deleted, or when its children change.

        :param path:     The ZooKeeper node path to watch.
        :param listener: An implementation of DataListener to receive callbacks.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def remove_data_listener(self, path: str, listener: DataListener) -> None:
        """
        Unregister a previously added data listener from the specified node.

        :param path:     The ZooKeeper node path.
        :param listener: The listener to remove.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def add_children_listener(self, path: str, listener: ChildrenListener) -> None:
        """
        Register a children listener on the specified node.

        The listener will be notified when the direct children of the node
        are added or removed.

        :param path:     The ZooKeeper node path to watch.
        :param listener: An implementation of ChildrenListener to receive callbacks.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def remove_children_listener(self, path: str, listener: ChildrenListener) -> None:
        """
        Unregister a previously added children listener from the specified node.

        :param path:     The ZooKeeper node path.
        :param listener: The listener to remove.
        """
        raise NotImplementedError()


# Async versions of the listeners
AsyncStateListener = Callable[[ConnectionState], Awaitable[None]]
AsyncDataListener = Callable[[str, bytes, DataEventType], Awaitable[None]]
AsyncChildrenListener = Callable[[list[str]], Awaitable[None]]


class AsyncZookeeperClient(abc.ABC):
    """
    Asynchronous version of Zookeeper Client
    """

    @abc.abstractmethod
    async def start(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def connected(self) -> bool:
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def create(
        self,
        path: str,
        value: bytes = b"",
        acl: Optional[list] = None,
        ephemeral: bool = False,
        sequence: bool = False,
        makepath: bool = False,
    ) -> str:
        raise NotImplementedError()

    @abc.abstractmethod
    async def exists(self, path: str) -> bool:
        raise NotImplementedError()

    @abc.abstractmethod
    async def delete(self, path: str, recursive: bool = False) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def get_data(self, path: str) -> bytes:
        raise NotImplementedError()

    @abc.abstractmethod
    async def get_children(self, path: str) -> list[str]:
        raise NotImplementedError()

    @abc.abstractmethod
    async def add_state_listener(self, listener: AsyncStateListener) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def remove_state_listener(self, listener: AsyncStateListener) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def add_data_listener(self, path: str, listener: AsyncDataListener) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def remove_data_listener(self, path: str, listener: AsyncDataListener) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def add_children_listener(self, path: str, listener: AsyncChildrenListener) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    async def remove_children_listener(self, path: str, listener: AsyncChildrenListener) -> None:
        raise NotImplementedError()
