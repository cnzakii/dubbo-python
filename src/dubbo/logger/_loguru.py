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
from typing import Optional

from loguru import logger as loguru_logger

from ._base import ConsoleOptions, DubboLogger, FileOptions, LoggerLevel

__all__ = ["LoguruLogger"]

_DEFAULT_LOG_FORMAT = "{time} | {level} | {module}:{function}:{line} | [Dubbo] {message}"
_DEFAULT_COLOR_LOG_FORMAT = (
    "<green>{time}</green> "
    "<red>|</red> "
    "<level>{level}</level> "
    "<red>|</red> "
    "<cyan>{module}:{function}:{line}</cyan> "
    "<red>-</red> "
    "<magenta>[Dubbo]</magenta> "
    "<level>{message}</level>"
)

_DEFAULT_DATE_FORMAT = "YYYY-MM-DD HH:mm:ss"

_DEFAULT_CONSOLE_OPTIONS = ConsoleOptions(
    level=LoggerLevel.INFO,
    log_format=_DEFAULT_COLOR_LOG_FORMAT,
    date_format=_DEFAULT_DATE_FORMAT,
    colorize=True,
)

_DEFAULT_FILE_OPTIONS = FileOptions(
    enable=False,
    log_format=_DEFAULT_LOG_FORMAT,
    date_format=_DEFAULT_DATE_FORMAT,
    rotation="10 MB",  # default to size-based rotation
    retention="7 days",
)


class LoguruLogger(DubboLogger):
    """
    A logger implementation using the loguru library, supporting both console and file output.
    """

    def __init__(self, console_options: Optional[ConsoleOptions] = None, file_options: Optional[FileOptions] = None):
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

    def _initialize(self):
        # Clear existing handlers
        loguru_logger.remove()

        # Build loguru format
        log_format = self._console_options.log_format
        date_format = self._console_options.date_format
        loguru_format = log_format.replace("{time}", f"{{time:{date_format}}}")

        # Console handler
        loguru_logger.add(
            sys.stdout,
            level=self._console_options.level.name,
            colorize=self._console_options.colorize,
            format=loguru_format,
        )

        # File handler
        if self._file_options.enable:
            loguru_logger.add(
                self._file_options.path,
                level=self._file_options.level.name,
                format=self._file_options.log_format.replace("{time}", f"{{time:{self._file_options.date_format}}}"),
                rotation=self._file_options.rotation,  # str like "10 MB" or "1 week", or int (bytes)
                retention=self._file_options.retention,  # str or int
                encoding="utf-8",
                enqueue=True,  # Thread-safe
                backtrace=True,
                diagnose=True,
            )

    def log(self, level: LoggerLevel, msg: str, *args, **kwargs):
        loguru_logger.opt(depth=1).log(level.name, msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        loguru_logger.opt(depth=1).debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        loguru_logger.opt(depth=1).info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        loguru_logger.opt(depth=1).warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        loguru_logger.opt(depth=1).error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        loguru_logger.opt(depth=1).critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        loguru_logger.opt(depth=1).exception(msg, *args, **kwargs)
