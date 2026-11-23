# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

from collections.abc import Mapping

from dubbo.config.data import ConfigData
from dubbo.config.exceptions import ConfigParseError
from dubbo.utils.yaml import load_yaml

from .base import ConfigParser

__all__ = ["YamlParser"]


class YamlParser(ConfigParser):
    """Reads a YAML mapping with safe constructors and acyclic aliases."""

    def loads(self, text: str, /) -> ConfigData:
        try:
            data = load_yaml(text)
        except ValueError as error:
            raise ConfigParseError(f"invalid YAML configuration: {error}") from error

        if not isinstance(data, Mapping):
            raise ConfigParseError("configuration must have a mapping at its root")
        return data
