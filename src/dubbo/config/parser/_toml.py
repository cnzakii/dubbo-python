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
import sys
from typing import Any

from ... import constants
from ._base import BaseConfigParser

if sys.version_info >= (3, 11):
    import tomllib as toml
else:
    try:
        import tomli as toml
    except ImportError:
        raise ImportError("tomli is required for Python < 3.11. Please install it with `pip install tomli`.")

__all__ = ["TomlConfigParser"]


class TomlConfigParser(BaseConfigParser):
    """
    TOML configuration parser that implements the BaseConfigParser interface.

    Parses TOML-formatted configuration files and returns their contents as a dictionary.
    """

    __slots__ = ()

    def parse_str(self, config_content: str, *args, **kwargs) -> dict[str, Any]:
        """
        Parse TOML configuration from a string.

        :param config_content: Raw TOML content as a string.
        :type config_content: str
        :returns: Parsed TOML data as a dictionary.
        :rtype: dict[str, Any]
        :raises toml.TOMLDecodeError: If the content is not valid TOML.
        """
        try:
            return toml.loads(config_content)
        except Exception as e:
            raise ValueError(f"Invalid TOML content: {e}") from e

    def parse_file(self, file_name: str, encoding: str = constants.UTF_8, *args, **kwargs) -> dict[str, Any]:
        """
        Parse TOML configuration from a file.

        :param file_name: Path to the TOML config file.
        :type file_name: str
        :param encoding: File encoding, defaults to UTF-8.
        :type encoding: str
        :returns: Parsed TOML data as a dictionary.
        :rtype: dict[str, Any]
        :raises FileNotFoundError: If the file cannot be found.
        :raises ValueError: If the file content is not valid TOML.
        """
        try:
            with open(file_name, encoding=encoding) as f:
                content = f.read()
        except FileNotFoundError:
            raise
        try:
            return self.parse_str(content)
        except ValueError as e:
            raise ValueError(f"Failed to parse TOML file '{file_name}': {e}") from e
