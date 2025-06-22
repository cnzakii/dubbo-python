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
import typing

from dubbo.protocol import AsyncInvoker, Invoker

if typing.TYPE_CHECKING:
    from .directory import AsyncDirectory, Directory

__all__ = ["Cluster", "AsyncCluster", "ClusterInvoker"]


class ClusterInvoker(Invoker, abc.ABC):
    """ClusterInvoker"""

    @property
    @abc.abstractmethod
    def directory(self) -> "Directory":
        """Get the directory"""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def destroyed(self) -> bool:
        """Whether the cluster has been destroyed"""
        raise NotImplementedError()


class Cluster(abc.ABC):
    """Cluster interface for service invocation"""

    @abc.abstractmethod
    def join(self, directory: "Directory") -> Invoker:
        """Join a directory to create an invoker for service invocation.

        Args:
            directory: The directory containing available invokers.

        Returns:
            Invoker: An invoker that can be used to perform service calls.
        """
        raise NotImplementedError()


class AsyncCluster(abc.ABC):
    """Asynchronous Cluster interface for service invocation"""

    @abc.abstractmethod
    async def join(self, directory: "AsyncDirectory") -> AsyncInvoker:
        """Asynchronously join a directory to create an invoker for service invocation.

        Args:
            directory: The asynchronous directory containing available invokers.

        Returns:
            AsyncInvoker: An asynchronous invoker that can be used to perform service calls.
        """
        raise NotImplementedError()
