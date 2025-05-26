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
from typing import Any, Final

from ._loader import ExtensionLoader
from .exceptions import ExtensionError

__all__ = ["ExtensionManager"]


class ExtensionManager:
    """Manages extension loaders for multiple interfaces.

    Serves as a registry of ExtensionLoader instances, each responsible for
    a specific interface type. Provides unified access to extension implementations
    across the framework.
    """

    __slots__ = ("_loaders",)
    _loaders: Final[dict[type, ExtensionLoader]]

    def __init__(self, loaders: dict[type, ExtensionLoader]) -> None:
        """Initialize the extension manager with a set of loaders.

        Args:
            loaders: A mapping from interface types to their corresponding
                ExtensionLoader instances
        """
        self._loaders = loaders

    def get_loader(self, interface: type) -> ExtensionLoader:
        """Get the extension loader for a specific interface.

        Args:
            interface: The interface/base class to find a loader for

        Returns:
            The extension loader responsible for the specified interface

        Raises:
            ExtensionError: If no loader is registered for the interface
        """
        if interface not in self._loaders:
            raise ExtensionError(f"No ExtensionLoader registered for interface '{interface.__name__}'.")
        return self._loaders[interface]

    def list_names(self, interface: type) -> list[str]:
        """List all registered implementation names for an interface.

        Args:
            interface: The interface type to query

        Returns:
            List of registered implementation names for the interface

        Example:
            names = manager.list_names(Compressor)
            # ['identity', 'gzip', 'bzip2']
        """
        return self.get_loader(interface).list_names()

    def load_class(self, interface: type, name: str) -> type:
        """Load an implementation class for the given interface and name.

        Args:
            interface: The interface type to load an implementation for
            name: Name of the registered implementation to load

        Returns:
            The implementation class

        Raises:
            ExtensionError: If no loader exists for the interface or
                if the named implementation is not found
        """
        return self.get_loader(interface).load_class(name)

    def create_instance(self, interface: type, name: str, *args, **kwargs) -> Any:
        """Create an instance of the specified implementation.

        Args:
            interface: The interface type to create an implementation for
            name: Name of the registered implementation to instantiate
            *args: Positional arguments to pass to the constructor
            **kwargs: Keyword arguments to pass to the constructor

        Returns:
            An instance of the implementation

        Example:
            compressor = manager.create_instance(Compressor, "gzip", level=9)
        """
        return self.get_loader(interface).create_instance(name, *args, **kwargs)
