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
import inspect
from typing import Any


def is_async_callable(obj: Any) -> bool:
    """
    Check if the given object is an async callable (async function or async class method).
    Args:
        obj: The object to check.
    Returns:
        bool: True if the object is an async callable, False otherwise.
    """
    if inspect.iscoroutinefunction(obj):
        return True
    if inspect.isclass(obj) and hasattr(obj, "__call__") and inspect.iscoroutinefunction(obj.__call__):
        return True
    return False
