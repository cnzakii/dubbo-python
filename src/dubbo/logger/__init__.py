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

from ._base import ConsoleOptions, DubboLogger, FileOptions, LoggerLevel
from ._logging import LoggingLogger
from ._loguru import LoguruLogger

# Default logger instance
_instance: DubboLogger = LoggingLogger()


def get_instance() -> DubboLogger:
    """
    Get the logger instance.

    :return: The default logger instance.
    :rtype: DubboLogger
    """
    return _instance


def set_instance(logger: DubboLogger) -> None:
    """
    Set the default logger instance.

    :param logger: The new logger instance to set.
    :type logger: DubboLogger
    """
    global _instance
    _instance = logger
