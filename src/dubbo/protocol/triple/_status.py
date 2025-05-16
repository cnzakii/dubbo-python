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
from typing import Optional

from dubbo.protocol.triple.grpc.constants import StatusCode


class TriRpcStatus:
    __slots__ = ("_code", "_exc", "_desc")

    _code: StatusCode
    _exc: Optional[Exception]
    _desc: Optional[str]

    def __init__(self, code: StatusCode, exc: Optional[Exception] = None, desc: Optional[str] = None):
        self._code = code
        self._exc = exc
        self._desc = desc

    @property
    def code(self) -> StatusCode:
        return self._code

    @property
    def exception(self) -> Optional[Exception]:
        return self._exc

    @property
    def description(self) -> Optional[str]:
        return self._desc
