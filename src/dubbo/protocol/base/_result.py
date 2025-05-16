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
    """
    Represents an RPC invocation result.

    Provides methods to get and set the result value and any error,
    as well as manage key-value attachments related to the invocation.
    """

    @abc.abstractmethod
    def get_value(self) -> _T:
        """
        Get the invocation result value.

        :return: The result value.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_value(self, value: _T) -> None:
        """
        Set the invocation result value.

        :param value: The result value to set.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_exception(self) -> Optional[Exception]:
        """
        Get the exception (error) from the invocation, if any.

        :return: The exception raised during invocation, or None if no error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_exception(self, exception: Exception) -> None:
        """
        Set the exception (error) for the invocation.

        :param exception: The exception to set.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attachments(self) -> dict[str, Any]:
        """
        Get all attachments associated with this result.

        :return: A dictionary of attachment key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attachment(self, key: str, default: Any = None) -> Any:
        """
        Get an attachment value by key.

        :param key: The key of the attachment.
        :param default: The default value to return if key not found.
        :return: The attachment value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attachment(self, key: str, value: Any) -> None:
        """
        Set or add an attachment key-value pair.

        :param key: The key of the attachment.
        :param value: The value to set.
        """
        raise NotImplementedError()


class AsyncResult(abc.ABC, Generic[_T]):
    """
    Represents an asynchronous RPC invocation result.

    Provides async methods to get and set the result value and any error,
    as well as manage attachments related to the invocation.
    """

    @abc.abstractmethod
    async def get_value(self) -> _T:
        """
        Asynchronously get the invocation result value.

        :return: The result value.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def set_value(self, value: _T) -> None:
        """
        Asynchronously set the invocation result value.

        :param value: The result value to set.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def get_exception(self) -> Exception:
        """
        Asynchronously get the exception (error) from the invocation, if any.

        :return: The exception raised during invocation, or None if no error.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def set_exception(self, exception: Exception) -> None:
        """
        Asynchronously set the exception (error) for the invocation.

        :param exception: The exception to set.
        """
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def attachments(self) -> dict[str, Any]:
        """
        Get all attachments associated with this result.

        :return: A dictionary of attachment key-value pairs.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_attachment(self, key: str, default: Any = None) -> Any:
        """
        Get an attachment value by key.

        :param key: The key of the attachment.
        :param default: The default value to return if key not found.
        :return: The attachment value or default.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def set_attachment(self, key: str, value: Any) -> None:
        """
        Set or add an attachment key-value pair.

        :param key: The key of the attachment.
        :param value: The value to set.
        """
        raise NotImplementedError()
