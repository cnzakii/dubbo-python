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
from typing import Any, Generic, Optional, TypeVar

__all__ = ["Result", "AsyncResult"]

_T = TypeVar("_T")


class Result(abc.ABC, Generic[_T]):
    """Represents an RPC invocation result.

    Provides methods to get and set the result value and any error,
    as well as manage key-value attachments related to the invocation.
    """

    @abc.abstractmethod
    def get_value(self) -> _T:
        """Gets the invocation result value.

        Returns:
            _T: The result value.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_value(self, value: _T) -> None:
        """Sets the invocation result value.

        Args:
            value: The result value to set.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_exception(self) -> Optional[Exception]:
        """Gets the exception (error) from the invocation, if any.

        Returns:
            Optional[Exception]: The exception raised during invocation,
                or None if no error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_exception(self, exception: Exception) -> None:
        """Sets the exception (error) for the invocation.

        Args:
            exception: The exception to set.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attachments(self) -> dict[str, Any]:
        """Gets all attachments associated with this result.

        Returns:
            dict[str, Any]: A dictionary of attachment key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attachment(self, key: str, default: Any = None) -> Any:
        """Gets an attachment value by key.

        Args:
            key: The key of the attachment.
            default: The default value to return if key not found.

        Returns:
            Any: The attachment value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attachment(self, key: str, value: Any) -> None:
        """Sets or adds an attachment key-value pair.

        Args:
            key: The key of the attachment.
            value: The value to set.
        """
        raise NotImplementedError()


class AsyncResult(abc.ABC, Generic[_T]):
    """Represents an asynchronous RPC invocation result.

    Provides async methods to get and set the result value and any error,
    as well as manage attachments related to the invocation.
    """

    @abc.abstractmethod
    async def get_value(self) -> _T:
        """Asynchronously gets the invocation result value.

        Returns:
            _T: The result value.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def set_value(self, value: _T) -> None:
        """Asynchronously sets the invocation result value.

        Args:
            value: The result value to set.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def get_exception(self) -> Exception:
        """Asynchronously gets the exception (error) from the invocation, if any.

        Returns:
            Exception: The exception raised during invocation, or None if no error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def set_exception(self, exception: Exception) -> None:
        """Asynchronously sets the exception (error) for the invocation.

        Args:
            exception: The exception to set.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attachments(self) -> dict[str, Any]:
        """Gets all attachments associated with this result.

        Returns:
            dict[str, Any]: A dictionary of attachment key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attachment(self, key: str, default: Any = None) -> Any:
        """Gets an attachment value by key.

        Args:
            key: The key of the attachment.
            default: The default value to return if key not found.

        Returns:
            Any: The attachment value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attachment(self, key: str, value: Any) -> None:
        """Sets or adds an attachment key-value pair.

        Args:
            key: The key of the attachment.
            value: The value to set.
        """
        raise NotImplementedError()
