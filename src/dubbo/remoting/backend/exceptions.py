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
from typing import Iterator, Type


@contextlib.contextmanager
def map_exc(mapping: dict[Type[Exception], Type[Exception]]) -> Iterator[None]:
    """
    Context manager to translate specific exceptions into custom exceptions.

    Example:
        with map_exc({OSError: ConnectError}):
            do_network_operation()

    If an exception occurs, and it matches a key in the mapping,
    the corresponding mapped exception is raised instead, preserving the traceback.

    :param mapping: A dictionary mapping from original exceptions to new exception types.
    :type mapping: Mapping[Type[Exception], Type[Exception]]
    :yield: None
    """
    try:
        yield
    except Exception as exc:
        for from_exc, to_exc in mapping.items():
            if isinstance(exc, from_exc):
                raise to_exc(exc) from exc
        raise  # Re-raise original exception if no mapping matched


# ==== Protocol Exceptions ====


class ProtocolError(Exception):
    """Base class for all protocol-related errors."""

    pass


# ==== Timeout Exceptions ====


class TimeoutException(Exception):
    """Base class for timeout-related exceptions."""

    pass


class PoolTimeout(TimeoutException):
    """Raised when no connection is available from the pool within the timeout."""

    pass


class ConnectTimeout(TimeoutException):
    """Raised when a connection attempt times out."""

    pass


class SendTimeout(TimeoutException):
    """Raised when sending data over a connection times out."""

    pass


class ReceiveTimeout(TimeoutException):
    """Raised when receiving data from a connection times out."""

    pass


# ==== Network I/O Exceptions ====


class NetworkError(Exception):
    """Base class for network I/O related errors."""

    pass


class ConnectError(NetworkError):
    """Raised when a low-level connection error occurs (e.g., DNS failure, refused connection)."""

    pass


class SendError(NetworkError):
    """Raised when sending data over a network fails."""

    pass


class ReceiveError(NetworkError):
    """Raised when receiving data from a network fails."""

    pass
