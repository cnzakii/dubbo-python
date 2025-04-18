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

from typing import Any

from ... import constants
from ._base import BaseConfigParser

try:
    import yaml
except ImportError:
    raise ImportError("PyYAML is required for YAML configuration parsing.Please install it using 'pip install pyyaml'.")

__all__ = ["YamlConfigParser"]


class YamlConfigParser(BaseConfigParser):
    """
    YAML configuration parser that implements the BaseConfigParser interface.

    Parses YAML-formatted configuration files and returns their contents as a dictionary.
    """

    __slots__ = ()

    def parse_str(self, config_content: str, *args, **kwargs) -> dict[str, Any]:
        """
        Parse YAML configuration from a string.

        :param config_content: Raw YAML content as a string.
        :type config_content: str
        :returns: Parsed YAML data as a dictionary.
        :rtype: dict[str, Any]
        :raises ImportError: If PyYAML is not installed.
        :raises ValueError: If the content is not valid YAML.
        """
        try:
            return yaml.safe_load(config_content)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML content: {e}") from e

    def parse_file(self, file_name: str, encoding: str = constants.UTF_8, *args, **kwargs) -> dict[str, Any]:
        """
        Parse YAML configuration from a file.

        :param file_name: Path to the YAML config file.
        :type file_name: str
        :param encoding: File encoding, defaults to UTF-8.
        :type encoding: str
        :returns: Parsed YAML data as a dictionary.
        :rtype: dict[str, Any]
        :raises FileNotFoundError: If the file cannot be found.
        :raises ImportError: If PyYAML is not installed.
        :raises ValueError: If the file content is not valid YAML.
        """
        try:
            with open(file_name, encoding=encoding) as f:
                content = f.read()
        except FileNotFoundError:
            raise
        try:
            return self.parse_str(content)
        except ValueError as e:
            raise ValueError(f"Failed to parse YAML file '{file_name}': {e}") from e
