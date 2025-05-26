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

from dubbo.common import constants

from ._base import BaseConfig

__all__ = ["ApplicationConfig"]


class ApplicationConfig(BaseConfig):
    """
    Configuration for the dubbo application.
    """

    __slots__ = ("_name", "_version", "_owner", "_organization", "_architecture", "_environment")

    _name: str
    _version: str
    _owner: str
    _organization: str
    _architecture: str
    _environment: str

    def __init__(
        self,
        name: str,
        *,
        version: str = "",
        owner: str = "",
        organization: str = "",
        architecture: str = "",
        environment: str = "",
    ) -> None:
        self.name = name
        self.version = version
        self.owner = owner
        self.organization = organization
        self.architecture = architecture
        self.environment = environment

    @property
    def name(self) -> str:
        """Get the application name."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        """Set the application name."""
        self._name = value

    @property
    def version(self) -> str:
        """Get the application version."""
        return self._version

    @version.setter
    def version(self, value: str) -> None:
        """Set the application version."""
        self._version = value

    @property
    def owner(self) -> str:
        """Get the application owner."""
        return self._owner

    @owner.setter
    def owner(self, value: str) -> None:
        """Set the application owner."""
        self._owner = value

    @property
    def organization(self) -> str:
        """Get the application organization."""
        return self._organization

    @organization.setter
    def organization(self, value: str) -> None:
        """Set the application organization."""
        self._organization = value

    @property
    def architecture(self) -> str:
        """Get the application architecture."""
        return self._architecture

    @architecture.setter
    def architecture(self, value: str) -> None:
        """Set the application architecture."""
        self._architecture = value

    @property
    def environment(self) -> str:
        """Get the application environment."""
        return self._environment

    @environment.setter
    def environment(self, value: str) -> None:
        """Set the application environment."""
        if value not in constants.ENVIRONMENT_VALUES:
            raise ValueError(f"Invalid environment value: {value}. Must be one of {constants.ENVIRONMENT_VALUES}.")
        self._environment = value
