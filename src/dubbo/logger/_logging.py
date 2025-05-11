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
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from typing import Optional

from dubbo.common import constant

from ._base import ConsoleOptions, DubboLogger, FileOptions, LoggerLevel

__all__ = ["LoggingLogger"]


# A mapping from Dubbo's LoggerLevel to Python's logging module levels.
_DUBBO_TO_LOGGING = {
    LoggerLevel.DEBUG: logging.DEBUG,
    LoggerLevel.INFO: logging.INFO,
    LoggerLevel.WARNING: logging.WARNING,
    LoggerLevel.ERROR: logging.ERROR,
    LoggerLevel.CRITICAL: logging.CRITICAL,
}

_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(module)s:%(funcName)s:%(lineno)d | %(message)s"
# The default log format for colorized output (implement using the `colorlog` library)
_DEFAULT_COLOR_LOG_FORMAT = (
    "%(green)s%(asctime)s "
    "%(red)s| "
    "%(log_color)s%(levelname)s "
    "%(red)s| "
    "%(cyan)s%(module)s:%(funcName)s:%(lineno)d "
    "%(red)s- "
    "%(purple)s[Dubbo] "
    "%(log_color)s%(message)s%(reset)s"
)

_DEFAULT_LOG_COLORS = {
    "DEBUG": "blue",
    "INFO": "white",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold_red",
}


_DEFAULT_CONSOLE_OPTIONS = ConsoleOptions(
    level=LoggerLevel.INFO, log_format=_DEFAULT_LOG_FORMAT, date_format=_DEFAULT_DATE_FORMAT, colorize=False
)

_DEFAULT_FILE_OPTIONS = FileOptions(
    enable=False,
    log_format=_DEFAULT_LOG_FORMAT,
    date_format=_DEFAULT_DATE_FORMAT,
)


class LoggingLogger(DubboLogger):
    """
    A logger class that integrates with the Python `logging` module, supporting both console and file-based logging.

    This class handles the initialization of both console and file handlers based on the provided configuration.
    It supports rotation, retention, and colorized output.
    """

    __slots__ = ("_console_options", "_file_options", "_instance")

    _instance: logging.Logger

    def __init__(self, console_options: Optional[ConsoleOptions] = None, file_options: Optional[FileOptions] = None):
        """
        Initializes the logger with the given console and file options.

        :param console_options: Configuration for console logging.
        :param file_options: Configuration for file logging.
        """
        super().__init__(console_options, file_options)
        self._initialize()

    def _merge_console_options(self, custom: Optional[ConsoleOptions]) -> ConsoleOptions:
        if not custom:
            return _DEFAULT_CONSOLE_OPTIONS

        # Set default log format based on colorization
        default_log_format = _DEFAULT_COLOR_LOG_FORMAT if custom.colorize else _DEFAULT_LOG_FORMAT

        return ConsoleOptions(
            level=custom.level or _DEFAULT_CONSOLE_OPTIONS.level,
            log_format=custom.log_format or default_log_format,
            date_format=custom.date_format or _DEFAULT_CONSOLE_OPTIONS.date_format,
            colorize=custom.colorize,
        )

    def _merge_file_options(self, custom: Optional[FileOptions]) -> FileOptions:
        if not custom:
            return _DEFAULT_FILE_OPTIONS

        return FileOptions(
            enable=custom.enable,
            path=custom.path or _DEFAULT_FILE_OPTIONS.path,
            level=custom.level or _DEFAULT_FILE_OPTIONS.level,
            log_format=custom.log_format or _DEFAULT_FILE_OPTIONS.log_format,
            date_format=custom.date_format or _DEFAULT_FILE_OPTIONS.date_format,
            rotation=custom.rotation if custom.rotation is not None else _DEFAULT_FILE_OPTIONS.rotation,
            retention=custom.retention if custom.retention is not None else _DEFAULT_FILE_OPTIONS.retention,
        )

    def _initialize(self) -> None:
        """
        Initializes the logging instance by merging the default and custom options, and sets up the console
        and file handlers accordingly.
        """
        self._instance = logging.getLogger(constant.DUBBO)

        # Ensure the logger processes all messages needed by any handler
        min_level = min(self._console_options.level, self._file_options.level)
        self._instance.setLevel(_DUBBO_TO_LOGGING[min_level])

        # Clear all existing handlers to avoid duplication
        for handler in self._instance.handlers[:]:
            self._instance.removeHandler(handler)

        # Add a new handler for console output
        if self._console_options.colorize:
            self._set_color_console_handler()
        else:
            self._set_console_handler()

        # Add file handler if enabled
        if self._file_options.enable:
            self._set_file_handler()

    def _set_console_handler(self) -> None:
        """
        Sets up the console handler for the logger with the provided options.

        The console handler will display logs in a plain format without colorization.
        """
        # Create a console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(_DUBBO_TO_LOGGING[self._console_options.level])
        formatter = logging.Formatter(fmt=self._console_options.log_format, datefmt=self._console_options.date_format)
        console_handler.setFormatter(formatter)
        self._instance.addHandler(console_handler)

    def _set_color_console_handler(self) -> None:
        """
        Sets up a colorized console handler for the logger.

        This handler uses the `colorlog` library to add colors to the log levels for better readability.
        """
        try:
            import colorlog
        except ImportError:
            raise ImportError(
                "The 'colorlog' package is required for colorized logging. "
                "Please install it using 'pip install colorlog'."
            )

        # Create a color console handler
        console_handler = colorlog.StreamHandler()
        console_handler.setLevel(_DUBBO_TO_LOGGING[self._console_options.level])

        formatter = colorlog.ColoredFormatter(
            fmt=self._console_options.log_format,
            datefmt=self._console_options.date_format,
            log_colors=_DEFAULT_LOG_COLORS,
            reset=True,
            style="%",
        )
        console_handler.setFormatter(formatter)
        self._instance.addHandler(console_handler)

    def _set_file_handler(self) -> None:
        """
        Sets up the file handler for the logger with the specified file options.

        Depending on the rotation configuration, it will either use a regular file handler,
        a size-based rotation handler, or a time-based rotation handler.
        """
        if isinstance(self._file_options.rotation, int):
            # Size-based rotation
            handler = RotatingFileHandler(
                filename=self._file_options.path,
                maxBytes=self._file_options.rotation,
                backupCount=int(self._file_options.retention or 0),
            )
        elif isinstance(self._file_options.rotation, str):
            # Time-based rotation (e.g., "midnight", "H")
            handler = TimedRotatingFileHandler(
                filename=self._file_options.path,
                when=self._file_options.rotation,
                interval=1,
                backupCount=int(self._file_options.retention or 0),
                encoding=constant.UTF_8,
            )  # type: ignore
        else:
            # No rotation
            handler = logging.FileHandler(filename=self._file_options.path, encoding=constant.UTF_8)  # type: ignore

        handler.setLevel(self._file_options.level.value)
        formatter = logging.Formatter(
            fmt=self._file_options.log_format,
            datefmt=self._file_options.date_format,
        )
        handler.setFormatter(formatter)

        self._instance.addHandler(handler)

    def log(self, level: LoggerLevel, msg: str, *args, **kwargs):
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 0) + 2
        self._instance.log(_DUBBO_TO_LOGGING[level], msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 0) + 2
        self._instance.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 0) + 2
        self._instance.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 0) + 2
        self._instance.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 0) + 2
        self._instance.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 0) + 2
        self._instance.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 0) + 2
        self._instance.exception(msg, *args, **kwargs)
