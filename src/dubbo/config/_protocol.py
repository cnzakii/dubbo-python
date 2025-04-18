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

from ._base import BaseConfig


class ProtocolConfig(BaseConfig):
    """
    Configuration for the protocol.
    """

    _name: str
    _host: Optional[str]
    _port: Optional[int]

    def __init__(self, name: str, host: Optional[str] = None, port: Optional[int] = None):
        """
        Initialize the protocol configuration
        :param name: protocol name
        :type name: str
        :param host: host address
        :type host: str
        :param port: port number
        :type port: int
        """
        self._name = name
        self._host = host
        self._port = port
