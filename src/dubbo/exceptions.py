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
import contextlib
from collections.abc import Iterator

# ==== Exception Mapping ====
ExceptionMapping = dict[type[Exception], type[Exception]]


@contextlib.contextmanager
def map_exceptions(mapping: ExceptionMapping) -> Iterator[None]:
    """Context manager for translating exceptions to custom exception types.

    Args:
        mapping: Dictionary mapping original exception types to target types.

    Example:
        with map_exceptions({OSError: ConnectError, socket.timeout: SendError}):
            perform_network_operation()
    """
    try:
        yield
    except Exception as exc:
        for from_exc, to_exc in mapping.items():
            if isinstance(exc, from_exc):
                raise to_exc(exc) from exc
        raise  # Re-raise original exception if no mapping matched
