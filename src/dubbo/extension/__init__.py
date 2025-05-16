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

from ._loader import ExtensionLoader
from ._manager import ExtensionManager as _ExtensionManager
from .exceptions import ExtensionError

__all__ = ["ExtensionLoader", "ExtensionError", "extension_manager"]


def _load_extensions() -> _ExtensionManager:
    """
    Initialize and load all extension points and their implementations.

    This function imports all registered extension registries from the internal module,
    creates corresponding ExtensionLoader instances for each interface, and
    returns an ExtensionManager that manages all these loaders.
    """
    from ._internal import get_all_registries

    # Retrieve all extension registries defined in the internal module
    registries = get_all_registries()

    # Build a mapping from interface type to its ExtensionLoader instance
    loaders = {registry.interface: ExtensionLoader(registry.interface, registry.impls) for registry in registries}

    # Create and return the centralized ExtensionManager with all loaders registered
    return _ExtensionManager(loaders)


# Instantiate the global extension manager by loading all extensions on import
extension_manager = _load_extensions()
