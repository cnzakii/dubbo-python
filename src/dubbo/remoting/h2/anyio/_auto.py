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

import sniffio

from dubbo.common import URL
from dubbo.remoting.h2 import (
    AsyncHttp2Client,
    AsyncHttp2Server,
    AsyncHttp2Transport,
)

__all__ = ["AutoHttp2Transport"]


class AutoHttp2Transport(AsyncHttp2Transport):
    """
    AutoHttp2Transport automatically selects the appropriate HTTP/2 transport
    implementation based on the current async backend in use.
    """

    __slots__ = ("_transport",)

    _transport: AsyncHttp2Transport

    def __init__(self):
        asynclib = sniffio.current_async_library()
        if asynclib == "asyncio":
            from .aio import AioHttp2Transport

            self._transport = AioHttp2Transport()
        else:
            from .any import AnyIOHttp2Transport

            self._transport = AnyIOHttp2Transport()

    async def connect(self, url: URL) -> AsyncHttp2Client:
        """Connects to the given URL and returns an HTTP/2 client connection."""
        return await self._transport.connect(url)

    async def bind(self, url: URL) -> AsyncHttp2Server:
        """Binds to the given URL and returns an HTTP/2 server connection."""
        return await self._transport.bind(url)
