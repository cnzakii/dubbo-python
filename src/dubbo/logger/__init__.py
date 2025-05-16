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
import threading
from typing import Optional

from ._base import ConsoleOptions, DubboLogger, FileOptions, LoggerLevel

__all__ = ["get_instance", "set_instance", "LoggerLevel", "ConsoleOptions", "FileOptions", "DubboLogger"]

_instance: Optional[DubboLogger] = None
_instance_lock = threading.RLock()


def get_instance() -> DubboLogger:
    """
    Get the singleton logger instance. Creates a default instance if one doesn't exist.

    This function uses double-checking locking pattern to avoid race conditions
    when creating the logger instance.

    :return: The default logger instance.
    :rtype: DubboLogger
    """
    global _instance
    # Fast path: check if instance exists without acquiring lock
    if _instance is not None:
        return _instance

    # Slow path: acquire lock and create instance if needed
    with _instance_lock:
        if _instance is None:
            # Lazy import to avoid circular dependency
            from ._logging import LoggingLogger

            _instance = LoggingLogger()

        return _instance


def set_instance(logger: DubboLogger) -> None:
    """
    Set the default logger instance.

    This function is thread-safe and will replace the current logger instance.

    :param logger: The new logger instance to set.
    :type logger: DubboLogger
    """
    if not isinstance(logger, DubboLogger):
        raise TypeError("Logger must be an instance of DubboLogger")

    global _instance
    with _instance_lock:
        _instance = logger
