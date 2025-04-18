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

import configparser
from typing import Any

from ... import constants
from ._base import BaseConfigParser

__all__ = ["IniConfigParser"]


def _to_nested_dict(flat_dict: dict[str, Any], split_symbol: str = ".") -> dict[str, Any]:
    """
    Convert a flat dictionary with delimited keys into a nested dictionary.

    :param flat_dict: The flat dictionary where keys may contain split_symbol to represent nesting.
    :type flat_dict: dict[str, Any]
    :param split_symbol: The delimiter used to split keys into nested levels.
    :type split_symbol: str
    :returns: A nested dictionary structure.
    :rtype: dict[str, Any]
    """
    nested_dict = {}
    for key, value in flat_dict.items():
        keys = key.split(split_symbol)
        d = nested_dict
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
    return nested_dict


class IniConfigParser(BaseConfigParser):
    """
    INI configuration parser that supports both flat and nested dictionary outputs.
    """

    __slots__ = ("_parser",)

    _parser: configparser.ConfigParser

    def __init__(self):
        """
        Initialize the IniConfigParser with a new ConfigParser instance.
        """
        self._parser = configparser.ConfigParser()

    def parse_str(self, config_content: str, return_nested: bool = False, *args, **kwargs) -> dict[str, Any]:
        """
        Parse INI-formatted configuration from a string.

        :param config_content: Raw INI content as a string.
        :type config_content: str
        :param return_nested: If True, produces nested dict; otherwise a flat dict.
        :type return_nested: bool
        :returns: Parsed configuration as a dictionary.
        :rtype: dict[str, Any]
        :raises ValueError: If the content is malformed and cannot be parsed.
        """
        try:
            self._parser.read_string(config_content)
        except configparser.Error as e:
            raise ValueError(f"Failed to parse INI string: {e}")
        return self._to_dict(return_nested)

    def parse_file(
        self, file_name: str, encoding: str = constants.UTF_8, return_nested: bool = False, *args, **kwargs
    ) -> dict[str, Any]:
        """
        Parse INI-formatted configuration from a file.

        :param file_name: Path to the INI file.
        :type file_name: str
        :param encoding: File encoding to use, defaults to UTF-8.
        :type encoding: str
        :param return_nested: If True, produces nested dict; otherwise a flat dict.
        :type return_nested: bool
        :returns: Parsed configuration as a dictionary.
        :rtype: dict[str, Any]
        :raises FileNotFoundError: If the file cannot be found.
        :raises ValueError: If the file content is malformed and cannot be parsed.
        """
        try:
            read_success = self._parser.read(file_name, encoding=encoding)
            if not read_success:
                raise FileNotFoundError(f"INI file not found: {file_name}")
        except configparser.Error as e:
            raise ValueError(f"Failed to parse INI file '{file_name}': {e}")
        return self._to_dict(return_nested)

    def _to_dict(self, return_nested: bool = False) -> dict[str, Any]:
        """
        Convert the parser state to a dictionary representation.

        :param return_nested: If True, returns nested dict by splitting keys on '.'.
        :type return_nested: bool
        :returns: Flat or nested dictionary of sections and their key-value pairs.
        :rtype: dict[str, Any]
        """
        flat_dict = {section: dict(self._parser.items(section)) for section in self._parser.sections()}
        return flat_dict if not return_nested else _to_nested_dict(flat_dict)
