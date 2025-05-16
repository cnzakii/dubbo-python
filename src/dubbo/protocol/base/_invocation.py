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
from typing import Any

if typing.TYPE_CHECKING:
    from ._invoker import Invoker

__all__ = ["Invocation"]


class Invocation(abc.ABC):
    """
    Represents an invocation of a remote method.

    This interface provides access to method name, service name, arguments,
    attachments, attributes, and the invoker involved in the current invocation context.

    Attachments are key-value pairs typically used for metadata passed between services,
    while attributes are internal context data not transferred between client and server.
    """

    @property
    @abc.abstractmethod
    def method_name(self) -> str:
        """
        Get the name of the method being invoked.

        :return: The invocation method name.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def service_name(self) -> str:
        """
        Get the name of the service interface.

        :return: The service interface name.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def invoker(self) -> "Invoker":
        """
        Get the invoker associated with this invocation.

        :return: The Invoker instance.
        """
        raise NotImplementedError()

    @invoker.setter
    @abc.abstractmethod
    def invoker(self, invoker: "Invoker") -> None:
        """
        Set the invoker for this invocation.

        :param invoker: The Invoker instance.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def arguments(self) -> list[Any]:
        """
        Get the argument list passed to the method invocation.

        :return: A list of arguments.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attachments(self) -> dict[str, Any]:
        """
        Get the attachments (metadata) associated with this invocation.

        :return: A dictionary of attachment key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attachment(self, key: str, default: Any = None) -> Any:
        """
        Retrieve an attachment value by key.

        :param key: The key to look up.
        :param default: The default value if the key is missing.
        :return: The attachment value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attachment(self, key: str, value: Any) -> None:
        """
        Set or update an attachment key-value pair.

        :param key: The key to set.
        :param value: The value to set.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attributes(self) -> dict[str, Any]:
        """
        Get internal attributes related to this invocation.

        Attributes are used internally and not transferred between client and server.

        :return: A dictionary of attribute key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attribute(self, key: str, default: Any = None) -> Any:
        """
        Retrieve an attribute value by key.

        :param key: The key to look up.
        :param default: The default value if the key is missing.
        :return: The attribute value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attribute(self, key: str, value: Any) -> None:
        """
        Set or update an attribute key-value pair.

        :param key: The key to set.
        :param value: The value to set.
        """
        raise NotImplementedError()
