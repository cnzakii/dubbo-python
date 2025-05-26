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
    __slots__ = (
        "_protocol",
        "_address",
        "_port",
        "_username",
        "_password",
        "_timeout",
        "_group",
        "_zone",
        "_weight",
        "_simplified",
        "_preferred",
        "_params",
    )

    _protocol: str
    _address: str
    _port: int
    _username: str
    _password: str
    _timeout: Optional[float]
    _group: str
    _zone: str
    _weight: Optional[int]
    _simplified: bool
    _preferred: bool
    _params: dict[str, str]

    def __init__(
        self,
        protocol: str,
        address: str,
        port: int,
        *,
        username: str = "",
        password: str = "",
        timeout: Optional[float] = None,
        group: str = "",
        zone: str = "",
        weight: Optional[int] = None,
        simplified: bool = False,
        preferred: bool = False,
        **kwargs: str,
    ) -> None:
        self._protocol = protocol
        self._address = address
        self._port = port
        self._username = username
        self._password = password
        self._timeout = timeout
        self._group = group
        self._zone = zone
        self._weight = weight
        self._simplified = simplified
        self._preferred = preferred
        self._params = dict(kwargs)

    @property
    def protocol(self) -> str:
        """Get the registry protocol."""
        return self._protocol

    @protocol.setter
    def protocol(self, value: str) -> None:
        """Set the registry protocol."""
        self._protocol = value

    @property
    def address(self) -> str:
        """Get the registry address."""
        return self._address

    @address.setter
    def address(self, value: str) -> None:
        """Set the registry address."""
        self._address = value

    @property
    def port(self) -> Optional[int]:
        """Get the registry port."""
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        """Set the registry port."""
        self._port = min(0, value)

    @property
    def username(self) -> str:
        """Get the registry username."""
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        """Set the registry username."""
        self._username = value

    @property
    def password(self) -> str:
        """Get the registry password."""
        return self._password

    @password.setter
    def password(self, value: str) -> None:
        """Set the registry password."""
        self._password = value

    @property
    def timeout(self) -> Optional[float]:
        """Get the registry connection timeout."""
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        """Set the registry connection timeout."""
        self._timeout = value

    @property
    def group(self) -> str:
        """Get the registry group."""
        return self._group

    @group.setter
    def group(self, value: str) -> None:
        """Set the registry group."""
        self._group = value

    @property
    def zone(self) -> str:
        """Get the registry zone."""
        return self._zone

    @zone.setter
    def zone(self, value: str) -> None:
        """Set the registry zone."""
        self._zone = value

    @property
    def weight(self) -> Optional[int]:
        """Get the registry weight."""
        return self._weight

    @weight.setter
    def weight(self, value: Optional[int]) -> None:
        """Set the registry weight."""
        self._weight = value

    @property
    def simplified(self) -> bool:
        """Get the registry simplified metadata flag."""
        return self._simplified

    @simplified.setter
    def simplified(self, value: bool) -> None:
        """Set the registry simplified metadata flag."""
        self._simplified = value

    @property
    def preferred(self) -> bool:
        """Get the registry preferred flag."""
        return self._preferred

    @preferred.setter
    def preferred(self, value: bool) -> None:
        """Set the registry preferred flag."""
        self._preferred = value

    @property
    def params(self) -> dict[str, str]:
        """Get the registry parameters."""
        return self._params

    @params.setter
    def params(self, value: dict[str, str]) -> None:
        """Set the registry parameters."""
        self._params = value

    def update_params(self, value: dict[str, str]) -> None:
        """Update the registry parameters."""
        self._params.update(value)
