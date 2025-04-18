#
# licensed to the apache software foundation (asf) under one or more
# contributor license agreements.  see the notice file distributed with
# this work for additional information regarding copyright ownership.
# the asf licenses this file to you under the apache license, version 2.0
# (the "license"); you may not use this file except in compliance with
# the license.  you may obtain a copy of the license at
#
#     http://www.apache.org/licenses/license-2.0
#
# unless required by applicable law or agreed to in writing, software
# distributed under the license is distributed on an "as is" basis,
# without warranties or conditions of any kind, either express or implied.
# see the license for the specific language governing permissions and
# limitations under the license.
from typing import Optional

from ._base import BaseConfig


class ApplicationConfig(BaseConfig):
    """
    configuration for the dubbo application.
    """

    __slots__ = ("config_id", "name", "version", "owner", "organization", "architecture", "environment")

    name: str
    version: Optional[str]
    owner: Optional[str]
    organization: Optional[str]
    architecture: Optional[str]
    environment: Optional[str]

    def __init__(
        self,
        name: str,
        version: Optional[str] = None,
        owner: Optional[str] = None,
        organization: Optional[str] = None,
        architecture: Optional[str] = None,
        environment: Optional[str] = None,
    ):
        """
        Initialize the application configuration
        :param name: application name
        :type name: str
        :param version: application version
        :type version: str
        :param owner: application owner
        :type owner: str
        :param organization: organization name (BU or Department)
        :type organization: str
        :param architecture: application architecture (microservices, monolith, etc.)
        :type architecture: str
        :param environment: application environment (development, testing, production, etc.)
        """
        self.name = name
        self.version = version
        self.owner = owner
        self.organization = organization
        self.architecture = architecture
        self.environment = environment
