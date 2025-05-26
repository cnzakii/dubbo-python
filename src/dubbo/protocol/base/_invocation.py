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
from typing import Any, Union

if typing.TYPE_CHECKING:
    from ._invoker import AsyncInvoker, Invoker

__all__ = ["Invocation"]


class Invocation(abc.ABC):
    """Represents an invocation of a remote method.

    This interface provides access to method name, service name, arguments,
    attachments, attributes, and the invoker involved in the current invocation context.

    Attachments are key-value pairs typically used for metadata passed between services,
    while attributes are internal context data not transferred between client and server.
    """

    @property
    @abc.abstractmethod
    def method_name(self) -> str:
        """Gets the name of the method being invoked.

        Returns:
            str: The invocation method name.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def service_name(self) -> str:
        """Gets the name of the service interface.

        Returns:
            str: The service interface name.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def invoker(self) -> Union["Invoker", "AsyncInvoker"]:
        """Gets the invoker associated with this invocation.

        Returns:
            Union[Invoker, AsyncInvoker]: The invoker instance.
        """
        raise NotImplementedError()

    @invoker.setter
    @abc.abstractmethod
    def invoker(self, invoker: Union["Invoker", "AsyncInvoker"]) -> None:
        """Sets the invoker for this invocation.

        Args:
            invoker: The invoker instance to be associated with this invocation.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def arguments(self) -> list[Any]:
        """Gets the argument list passed to the method invocation.

        Returns:
            list[Any]: A list of arguments.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attachments(self) -> dict[str, Any]:
        """Gets the attachments (metadata) associated with this invocation.

        Returns:
            dict[str, Any]: A dictionary of attachment key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attachment(self, key: str, default: Any = None) -> Any:
        """Retrieves an attachment value by key.

        Args:
            key: The key to look up.
            default: The default value if the key is missing.

        Returns:
            Any: The attachment value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attachment(self, key: str, value: Any) -> None:
        """Sets or updates an attachment key-value pair.

        Args:
            key: The key to set.
            value: The value to set.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attributes(self) -> dict[str, Any]:
        """Gets internal attributes related to this invocation.

        Attributes are used internally and not transferred between client and server.

        Returns:
            dict[str, Any]: A dictionary of attribute key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attribute(self, key: str, default: Any = None) -> Any:
        """Retrieves an attribute value by key.

        Args:
            key: The key to look up.
            default: The default value if the key is missing.

        Returns:
            Any: The attribute value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attribute(self, key: str, value: Any) -> None:
        """Sets or updates an attribute key-value pair.

        Args:
            key: The key to set.
            value: The value to set.
        """
        raise NotImplementedError()
