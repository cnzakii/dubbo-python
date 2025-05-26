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

from ._url import URL

__all__ = ["Node", "AsyncNode"]


class Node(abc.ABC):
    """Base class for all Dubbo framework nodes.

    A node represents any component in the Dubbo network that has a URL,
    can be checked for availability, and can be destroyed when no longer needed.
    """

    @abc.abstractmethod
    def get_url(self) -> URL:
        """Get the URL associated with this node.

        Returns:
            URL object containing the node's address and parameters.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if this node is currently available.

        Returns:
            True if the node is available for use, False otherwise.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def destroy(self) -> None:
        """Release resources associated with this node.

        Should be called when the node is no longer needed to ensure
        proper cleanup of resources.
        """
        raise NotImplementedError()


class AsyncNode(abc.ABC):
    """Asynchronous version of the Node interface.

    Similar to Node, but provides asynchronous destroy method for
    non-blocking resource cleanup in asynchronous contexts.
    """

    @abc.abstractmethod
    def get_url(self) -> URL:
        """Get the URL associated with this node.

        Returns:
            URL object containing the node's address and parameters.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if this node is currently available.

        Returns:
            True if the node is available for use, False otherwise.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    async def destroy(self) -> None:
        """Asynchronously release resources associated with this node.

        Allows for non-blocking cleanup operations in asynchronous contexts.
        """
        raise NotImplementedError()
