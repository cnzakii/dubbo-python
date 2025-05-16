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
import importlib
from typing import Any, Optional, Union

from .exceptions import ExtensionError

__all__ = ["ExtensionLoader"]


class ExtensionLoader:
    """
    Extension loader that dynamically loads implementation classes for a given interface.
    Supports mapping from name to module path or class, returning type-safe implementation classes.
    """

    def __init__(self, interface: type, impls: Optional[dict[str, Union[str, type]]] = None) -> None:
        """
        :param interface: The interface type whose implementations are to be loaded.
        :param impls: A mapping from name to implementation class
                    or module path string in the format 'module.submodule:ClassName'.
        """
        self._interface = interface
        self._impls: dict[str, Union[str, type]] = impls or {}
        self._cache: dict[str, type] = {}

    @property
    def interface(self) -> type:
        """Return the interface type."""
        return self._interface

    def register(self, name: str, impl: Union[str, type]) -> None:
        """
        Register an extension implementation dynamically.

        :param name: The name of the implementation.
        :param impl: The implementation class or a string path to the class.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("Implementation name must be a non-empty string.")
        if not (isinstance(impl, str) or isinstance(impl, type)):
            raise TypeError("Implementation must be a class or a string path.")
        self._impls[name] = impl
        if name in self._cache:
            del self._cache[name]  # Clear cache

    def get_impl(self, name: str) -> type:
        """
        Get the implementation class by name, with caching.

        :param name: Implementation name.
        :return: Implementation class, subclass of the interface.
        :raises ExtensionError: If the implementation is not found or loading fails.
        """
        if name in self._cache:
            return self._cache[name]

        impl = self._impls.get(name)
        if impl is None:
            raise ExtensionError(f"Extension '{name}' not found for interface '{self._interface.__name__}'")

        # If it's already a class, validate and return it
        if isinstance(impl, type):
            if not issubclass(impl, self._interface):
                raise ExtensionError(
                    f"Registered implementation '{name}' is not a subclass of '{self._interface.__name__}'."
                )
            self._cache[name] = impl
            return impl

        # If it's a string path, import dynamically
        if isinstance(impl, str):
            try:
                module_name, class_name = impl.rsplit(":", 1)
            except ValueError:
                raise ExtensionError(
                    f"Implementation path '{impl}' for '{name}' is invalid. "
                    f"Expected format 'module.submodule:ClassName'."
                )

            try:
                module = importlib.import_module(module_name)
                cls = getattr(module, class_name)
            except (ImportError, AttributeError) as e:
                raise ExtensionError(f"Failed to import '{impl}' for extension '{name}'.") from e

            if not issubclass(cls, self._interface):
                raise ExtensionError(
                    f"Loaded class '{cls.__name__}' is not a subclass of '{self._interface.__name__}'."
                )

            self._cache[name] = cls
            return cls

        raise ExtensionError(f"Unsupported implementation type for '{name}': {type(impl)}")

    def get_instance(self, name: str, *args, **kwargs) -> Any:
        """
        Instantiate the implementation class by name.

        :param name: Implementation name.
        :param args: Positional arguments to instantiate the class.
        :param kwargs: Keyword arguments to instantiate the class.
        :return: An instance of the implementation class.
        """
        impl_cls = self.get_impl(name)
        return impl_cls(*args, **kwargs)
