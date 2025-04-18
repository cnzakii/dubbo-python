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


class RegistryConfig(BaseConfig):
    """
    Configuration for service registration and discovery.
    """

    _protocol: str
    _address: str
    _username: Optional[str]
    _password: Optional[str]
    _port: Optional[int]
    _parameters: dict[str, str]

    def __init__(
        self,
        protocol: str,
        address: str,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize a RegistryConfig instance.

        :param protocol: Communication protocol for registry (e.g., 'zookeeper').
        :type protocol: str
        :param address: Hostname or IP of the registry server.
        :type address: str
        :param port: TCP port for the registry service, defaults to None.
        :type port: Optional[int]
        :param username: Username for authentication, defaults to None.
        :type username: Optional[str]
        :param password: Password for authentication, defaults to None.
        :type password: Optional[str]
        """
        self._protocol = protocol
        self._address = address
        self._port = port
        self._username = username
        self._password = password
        self._parameters = {}

    @property
    def protocol(self) -> str:
        return self._protocol

    @protocol.setter
    def protocol(self, value: str) -> None:
        self._protocol = value

    @property
    def address(self) -> str:
        return self._address

    @address.setter
    def address(self, value: str) -> None:
        self._address = value

    @property
    def port(self) -> Optional[int]:
        return self._port

    @port.setter
    def port(self, value: Optional[int]) -> None:
        self._port = value

    @property
    def username(self) -> Optional[str]:
        return self._username

    @username.setter
    def username(self, value: Optional[str]) -> None:
        self._username = value

    @property
    def password(self) -> Optional[str]:
        return self._password

    @password.setter
    def password(self, value: Optional[str]) -> None:
        self._password = value

    @property
    def parameters(self) -> dict[str, str]:
        return self._parameters

    @parameters.setter
    def parameters(self, value: dict[str, str]) -> None:
        self._parameters = value
