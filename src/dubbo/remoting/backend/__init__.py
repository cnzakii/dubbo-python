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

"""Network backend abstraction layer for Dubbo-Python remoting.

This module provides unified network transport abstractions for TCP, UDP, and Unix domain
sockets, supporting both synchronous and asynchronous operations. It includes concrete
implementations for sync sockets and async libraries (via AnyIO for asyncio/trio support).

The design is inspired by httpcore's backend architecture (https://github.com/encode/httpcore)
but extends it with datagram support and unified interfaces for both stream and datagram
communications.

Available Backends:
    SyncBackend: Synchronous socket-based implementation
    AnyIOBackend: Asynchronous implementation using AnyIO library

Examples:
    Creating an async TCP connection:
        backend = AnyIOBackend()
        stream = await backend.connect_tcp("localhost", 8080)
        await stream.send(b"Hello")
        data = await stream.receive()
        await stream.aclose()

    Creating a sync UDP server:
        backend = SyncBackend()
        server = backend.create_udp_server("0.0.0.0", 9090)
        server.serve(handler_function)
"""

from .anyio import AnyIOBackend
from .base import (
    DEFAULT_MAX_BYTES,
    SOCKET_OPTION,
    AsyncNetworkBackend,
    AsyncNetworkServer,
    AsyncNetworkStream,
    AsyncStreamHandlerType,
    AsyncUDPDatagramHandlerType,
    AsyncUNIXDatagramHandlerType,
    NetworkBackend,
    NetworkServer,
    NetworkStream,
    StreamHandlerType,
    UDPDatagramHandlerType,
    UDPPacketType,
    UNIXDatagramHandlerType,
    UNIXDatagramPacketType,
)
from .sync import SyncBackend

__all__ = [
    "DEFAULT_MAX_BYTES",
    "SOCKET_OPTION",
    "UDPPacketType",
    "UNIXDatagramPacketType",
    "NetworkStream",
    "StreamHandlerType",
    "UDPDatagramHandlerType",
    "UNIXDatagramHandlerType",
    "NetworkServer",
    "NetworkBackend",
    "AsyncNetworkStream",
    "AsyncStreamHandlerType",
    "AsyncUDPDatagramHandlerType",
    "AsyncUNIXDatagramHandlerType",
    "AsyncNetworkServer",
    "AsyncNetworkBackend",
    "AnyIOBackend",
    "SyncBackend",
]
