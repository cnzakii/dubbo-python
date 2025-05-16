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
from typing import Any, Final, Union

from ._loader import ExtensionLoader
from .exceptions import ExtensionError


class ExtensionManager:
    """
    Manages multiple ExtensionLoader instances for different interfaces.
    Manages extension implementations for different interfaces.
    """

    __slots__ = ("_loaders",)

    _loaders: Final[dict[type, ExtensionLoader]]

    def __init__(self, _loaders: dict[type, ExtensionLoader]) -> None:
        # key: interface class, value: ExtensionLoader instance
        self._loaders: dict[type, ExtensionLoader] = _loaders

    def get_loader(self, interface: type) -> ExtensionLoader:
        """
        Get the ExtensionLoader for a given interface.
        Raises KeyError if not registered.
        """
        if interface not in self._loaders:
            raise ExtensionError(f"No ExtensionLoader registered for interface {interface.__name__}.")
        return self._loaders[interface]

    def get_impl(self, interface: type, name: str) -> type:
        """
        Get implementation class for given interface and implementation name.

        :param interface: Interface class.
        :param name: Implementation name.
        :return: Implementation class.
        """
        loader = self.get_loader(interface)
        return loader.get_impl(name)

    def get_instance(self, interface: type, name: str, *args, **kwargs) -> Any:
        """
        Get an instance of the implementation for the given interface and name.

        :param interface: Interface class.
        :param name: Implementation name.
        :param args: Positional args to pass to the constructor.
        :param kwargs: Keyword args to pass to the constructor.
        :return: Instance of the implementation.
        """
        loader = self.get_loader(interface)
        return loader.get_instance(name, *args, **kwargs)

    def register_impl(self, interface: type, name: str, impl: Union[str, type]) -> None:
        """
        Register a new implementation dynamically to the loader of the given interface.

        :param interface: Interface class.
        :param name: Implementation name.
        :param impl: Implementation class or module path string.（e.g. 'module.submodule:ClassName'）
        """
        loader = self.get_loader(interface)
        loader.register(name, impl)
