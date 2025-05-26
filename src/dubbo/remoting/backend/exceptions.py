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

# ==== Protocol Exceptions ====


class ProtocolError(Exception):
    """Base class for all protocol-related errors."""

    __slots__ = ()

    pass


# ==== Timeout Exceptions ====


class TimeoutException(Exception):
    """Base class for timeout-related exceptions."""

    __slots__ = ()

    pass


class ConnectTimeout(TimeoutException):
    """Raised when a connection attempt times out."""

    __slots__ = ()

    pass


class SendTimeout(TimeoutException):
    """Raised when sending data over a connection times out."""

    __slots__ = ()

    pass


class ReceiveTimeout(TimeoutException):
    """Raised when receiving data from a connection times out."""

    __slots__ = ()

    pass


# ==== Network I/O Exceptions ====
class NetworkError(Exception):
    """Base class for network I/O related errors."""

    __slots__ = ()

    pass


class ConnectError(NetworkError):
    """Raised when a low-level connection error occurs (e.g., DNS failure, refused connection)."""

    __slots__ = ()

    pass


class SendError(NetworkError):
    """Raised when sending data over a network fails."""

    __slots__ = ()

    pass


class ReceiveError(NetworkError):
    """Raised when receiving data from a network fails."""

    __slots__ = ()

    pass
