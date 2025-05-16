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

from dubbo.common import URL

if typing.TYPE_CHECKING:
    from ._exporter import AsyncExporter, Exporter
    from ._invoker import AsyncInvoker, Invoker

__all__ = ["Protocol", "AsyncProtocol"]


class Protocol(abc.ABC):
    """
    Protocol interface that defines methods for exporting services for remote invocation,
    referring remote services, and destroying protocol-related resources.

    Conventions:
    - When the 'invoke()' method is called on the Invoker returned by 'refer()',
      the protocol should execute the 'invoke()' method of the Invoker received by 'export()' with the same URL.
    - The Invoker returned by 'refer()' is implemented by the protocol, and is responsible
      for sending remote invocation requests.
    - The Invoker passed to 'export()' is implemented by the framework and should not be managed by
      the protocol implementation.

    Notes:
    - Protocol implementations do not handle transparent proxy conversion; that is done at other layers.
    - Protocols do not need to rely on TCP connections; they may be based on other IPC mechanisms like file sharing.
    - Implementations must be thread-safe and generally follow singleton usage.
    """

    @abc.abstractmethod
    def export(self, invoker: "Invoker") -> "Exporter":
        """
        Export a service for remote invocation.

        :param invoker: The service invoker to be exported.
        :return: An Exporter reference for the exported service, which can be used to unexport later.
        :raises RpcException: If an error occurs during export (e.g., port already in use).
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def refer(self, url: URL) -> "Invoker":
        """
        Refer (connect to) a remote service.

        :param url: The URL of the remote service to refer.
        :return: An Invoker serving as a local proxy for the remote service.
        :raises RpcException: If errors occur while connecting to the service provider.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def destroy(self):
        """
        Destroy the protocol.

        Actions:
        1. Cancel all services exported or referred by this protocol.
        2. Release all occupied resources, such as network connections and ports.
        3. The protocol may still export and refer new services after destruction.
        """
        raise NotImplementedError()


class AsyncProtocol(abc.ABC):
    """
    Asynchronous version of Protocol interface.

    Provides async methods to export services, refer remote services, and destroy protocol resources.
    """

    @abc.abstractmethod
    async def export(self, invoker: "AsyncInvoker") -> "AsyncExporter":
        """
        Asynchronously export a service for remote invocation.

        :param invoker: The async service invoker to export.
        :return: An async Exporter for the exported service.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def refer(self, url: URL) -> "AsyncInvoker":
        """
        Asynchronously refer (connect to) a remote service.

        :param url: The URL of the remote service.
        :return: An async Invoker serving as a local proxy.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def destroy(self):
        """
        Asynchronously destroy the protocol and release resources.
        """
        raise NotImplementedError()
