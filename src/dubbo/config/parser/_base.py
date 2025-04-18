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
import abc
from typing import Any

from ... import constants

__all__ = ["BaseConfigParser"]


class BaseConfigParser(abc.ABC):
    """
    Abstract base class for all configuration file parsers.

    Subclasses must implement both string‑based and file‑based parsing methods
    to return the parsed configuration object.
    """

    @abc.abstractmethod
    def parse_str(self, config_content: str, *args, **kwargs) -> dict[str, Any]:
        """
        Parse configuration data from a string.

        :param config_content: The raw configuration content as a string.
        :type config_content: str
        :returns: The parsed configuration
        :rtype: dict[str, Any]
        :raises ValueError: If the content is invalid or cannot be parsed.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def parse_file(self, file_name: str, encoding: str = constants.UTF_8, *args, **kwargs) -> dict[str, Any]:
        """
        Parse configuration data from a file.

        :param file_name: Path to the configuration file to parse.
        :type file_name: str
        :param encoding: File encoding to use when reading. Defaults to UTF‑8.
        :type encoding: str
        :returns:  The parsed configuration
        :rtype: Any
        :raises FileNotFoundError: If the file does not exist.
        :raises ValueError: If the file content is invalid or cannot be parsed.
        """
        raise NotImplementedError()
