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
    """Dynamically loads and manages implementation classes for an interface.

    Provides a registry and loading mechanism for extension implementations,
    supporting two registration methods:
    1. Direct class reference
    2. Import path string in the format 'module.submodule:ClassName'

    Extensions are loaded on demand and cached for performance.
    """

    def __init__(self, interface: type, impls: Optional[dict[str, Union[str, type]]] = None) -> None:
        """Initialize an extension loader for a specific interface.

        Args:
            interface: The interface or abstract base class to load implementations for
            impls: A mapping of names to implementations, where implementations can be
                either direct class references or module paths in the format
                'module.submodule:ClassName'
        """
        self._interface = interface
        self._impls: dict[str, Union[str, type]] = impls or {}
        self._cache: dict[str, type] = {}

    @property
    def interface(self) -> type:
        """Get the target interface type.

        Returns:
            The interface or abstract base class this loader manages
        """
        return self._interface

    def register(self, name: str, impl: Union[str, type]) -> None:
        """Register an implementation under a specified name.

        Args:
            name: Unique identifier for the implementation
            impl: Either a class object or an import path string in
                the format 'module.submodule:ClassName'

        Raises:
            ValueError: If name is empty or not a string
            TypeError: If impl is not a class or string

        Example:
            loader.register("myImpl", MyImplementation)
            loader.register("otherImpl", "mypackage.module:OtherImplementation")
        """
        if not isinstance(name, str) or not name:
            raise ValueError("Implementation name must be a non-empty string.")
        if not (isinstance(impl, str) or isinstance(impl, type)):
            raise TypeError("Implementation must be a class or a string path.")
        self._impls[name] = impl
        self._cache.pop(name, None)  # Clear cached class if exists

    def list_names(self) -> list[str]:
        """Get all registered implementation names.

        Returns:
            List of all implementation identifiers registered with this loader
        """
        return list(self._impls.keys())

    def load_class(self, name: str) -> type:
        """Load the implementation class for the given name.

        Resolves the implementation, importing it if necessary, and
        verifies that it properly implements the required interface.
        Results are cached for subsequent calls.

        Args:
            name: The name of the registered implementation to load

        Returns:
            The implementation class

        Raises:
            ExtensionError: If the implementation doesn't exist, can't be loaded,
                or doesn't implement the required interface
        """
        if name in self._cache:
            return self._cache[name]

        impl = self._impls.get(name)
        if impl is None:
            raise ExtensionError(
                f"No implementation registered under name '{name}' for interface '{self._interface.__name__}'."
            )

        # If it's already a class, validate and return it
        if isinstance(impl, type):
            if not issubclass(impl, self._interface):
                raise ExtensionError(
                    f"Registered class '{impl.__name__}' does not subclass '{self._interface.__name__}'."
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
                raise ExtensionError(f"Failed to load implementation from path '{impl}'.") from e

            if not issubclass(cls, self._interface):
                raise ExtensionError(
                    f"Loaded class '{cls.__name__}' is not a subclass of '{self._interface.__name__}'."
                )

            self._cache[name] = cls
            return cls

        raise ExtensionError(f"Unsupported implementation type for '{name}': {type(impl)}")

    def create_instance(self, name: str, *args, **kwargs) -> Any:
        """Create and return an instance of the named implementation.

        Loads the implementation class and instantiates it with the
        provided arguments.

        Args:
            name: The name of the registered implementation
            *args: Positional arguments to pass to the constructor
            **kwargs: Keyword arguments to pass to the constructor

        Returns:
            An instance of the implementation class

        Example:
            # Create an instance with constructor parameters
            instance = loader.create_instance("myImpl", arg1, arg2, option=True)
        """
        cls = self.load_class(name)
        return cls(*args, **kwargs)
