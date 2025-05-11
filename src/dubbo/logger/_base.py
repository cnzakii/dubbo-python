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
import enum
import os
from dataclasses import dataclass
from typing import Optional, Union

__all__ = ["DubboLogger", "LoggerLevel", "ConsoleOptions", "FileOptions"]


class LoggerLevel(enum.IntEnum):
    """
    Logging levels that indicate the severity of events.

    :cvar DEBUG: Detailed information, typically of interest only when diagnosing problems.
    :cvar INFO: Confirmation that things are working as expected.
    :cvar WARNING: An indication that something unexpected happened, or indicative of some problem in the near future.
    :cvar ERROR: Due to a more serious problem, the software has not been able to perform some function.
    :cvar CRITICAL: A very serious error, indicating that the program itself may be unable to continue running.
    """

    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5

    def __lt__(self, other):
        """
        Compare logging levels for ordering.

        :param other: The other logging level to compare with.
        :type other: LoggerLevel
        :return: True if this level is less than the other, False otherwise.
        :rtype: bool
        """
        return self.value < other.value

    def __le__(self, other):
        """
        Compare logging levels for ordering.

        :param other: The other logging level to compare with.
        :type other: LoggerLevel
        :return: True if this level is less than or equal to the other, False otherwise.
        :rtype: bool
        """
        return self.value <= other.value


@dataclass
class ConsoleOptions:
    """
    Configuration options for console-based logging.
    """

    level: LoggerLevel = LoggerLevel.INFO
    log_format: Optional[str] = None
    date_format: Optional[str] = None
    colorize: bool = False  # Whether to use colorized output


@dataclass
class FileOptions:
    """
    Configuration options for file-based logging.
    """

    """
    Whether to enable file logging.
    """
    enable: bool

    """
    Path to the log file.
    """
    path: str = os.path.join(os.getcwd(), "dubbo.log")

    """
    Minimum log level to write to file.
    """
    level: LoggerLevel = LoggerLevel.INFO

    """
    Log message format string.
    """
    log_format: Optional[str] = None

    """
    Datetime formatting string.
    """
    date_format: Optional[str] = None

    """
    Rotation strategy:
    - int (e.g., 10 * 1024 * 1024): size in bytes.
    - str (e.g., "1 day", "500 MB", "midnight"): loguru time/size rotation format.
    - Use heuristics in factory functions to map to `RotatingFileHandler` or `TimedRotatingFileHandler`.
    """
    rotation: Optional[Union[int, str]] = None

    """
    Retention policy:
    - int: max number of backups (for logging and loguru).
    - str: loguru-style time string like "10 days".
    """
    retention: Optional[Union[int, str]] = None


class DubboLogger(abc.ABC):
    """
    Abstract base class representing a logger for the Dubbo framework.

    Subclasses should implement the initialization and logging methods.
    """

    _console_options: ConsoleOptions
    _file_options: FileOptions

    def __init__(self, console_options: Optional[ConsoleOptions] = None, file_options: Optional[FileOptions] = None):
        """
        Initialize the logger with console and file options.

        :param console_options: Options for console logging.
        :type console_options: ConsoleOptions
        :param file_options: Options for file logging.
        :type file_options: FileOptions
        """
        self._console_options = self._merge_console_options(console_options)
        self._file_options = self._merge_file_options(file_options)

    @abc.abstractmethod
    def _merge_console_options(self, custom: Optional[ConsoleOptions]) -> ConsoleOptions:
        """
        Merge default console options with custom options.

        :param custom: Options for console logging.
        :type custom: ConsoleOptions
        :return: Merged console options.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def _merge_file_options(self, custom: Optional[FileOptions]) -> FileOptions:
        """
        Merge default file options with custom options.

        :param custom: Options for file logging.
        :type custom: FileOptions
        :return: Merged file options.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def log(self, level: LoggerLevel, msg: str, *args, **kwargs):
        """
        Log a message with the specified severity level.

        :param level: The severity level of the message.
        :type level: LoggerLevel
        :param msg: The log message.
        :type msg: str
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def debug(self, msg: str, *args, **kwargs):
        """
        Log a message with DEBUG level.

        :param msg: The debug message.
        :type msg: str
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def info(self, msg: str, *args, **kwargs):
        """
        Log a message with INFO level.

        :param msg: The informational message.
        :type msg: str
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def warning(self, msg: str, *args, **kwargs):
        """
        Log a message with WARNING level.

        :param msg: The warning message.
        :type msg: str
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def error(self, msg: str, *args, **kwargs):
        """
        Log a message with ERROR level.

        :param msg: The error message.
        :type msg: str
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def critical(self, msg: str, *args, **kwargs):
        """
        Log a message with CRITICAL level.

        :param msg: The critical message.
        :type msg: str
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def exception(self, msg: str, *args, **kwargs):
        """
        Log an ERROR level message with exception information.

        This is typically used within exception handlers to automatically include
        traceback information.

        :param msg: The error message.
        :type msg: str
        """
        raise NotImplementedError()
