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
from typing import Callable, Generic, Optional, TypeVar

__all__ = ["SendTracker", "dummy_tracker"]

T = TypeVar("T")


class SendTracker(Generic[T]):
    """
    A synchronous tracker for coordinating deferred send actions and confirming completion.

    `SendTracker` is useful in protocol implementations where actions like sending data
    are triggered separately from the confirmation of delivery (e.g., in sans-I/O cores).

    It wraps a send function, executes it via `trigger()`, and optionally allows a separate
    completion confirmation via `complete()`. Awaiting `result()` will block until the tracker
    is marked complete, and will raise any exception that occurred.

    Example usage (must use locking to ensure thread safety):

        tracker = SendTracker(lambda: h2_conn.send_headers(...), no_wait=False)

        # In a specific thread:
        ## begin:
        lock = threading.Lock()
        with lock:
            tracker.trigger()
            data = h2_conn.data_to_send()
            transport.send(data)
            tracker.complete()
        ## end

        tracker.result()  # or tracker.exception() if you want to ignore raised errors

    """

    __slots__ = ("_event", "_result", "_exc", "_send_func", "_no_wait")

    _event: threading.Event
    _result: Optional[T]
    _exc: Optional[Exception]
    _send_func: Callable[[], T]
    _no_wait: bool

    def __init__(self, send_func: Callable[[], T], no_wait: bool = False) -> None:
        self._event = threading.Event()
        self._result = None
        self._exc = None
        self._send_func = send_func
        self._no_wait = no_wait

    def trigger(self) -> None:
        """
        Invoke the send function and optionally complete the tracker.

        The result or exception is recorded. If `no_wait` is True or an exception occurs,
        the tracker is marked as complete immediately.

        NOTE: This method is not thread-safe by itself and should be used with external locking.
        """
        try:
            self._result = self._send_func()
        except Exception as e:
            self._exc = e
        finally:
            if self._no_wait or self._exc:
                self._event.set()

    def complete(self, exc: Optional[Exception] = None) -> None:
        """
        Mark the tracker as complete. Should be called after delivery confirmation.

        :param exc: Optional exception to record and raise during result retrieval.
        """
        self._exc = exc
        self._event.set()

    async def result(self, timeout: Optional[float] = None) -> Optional[T]:
        """
        Await completion of the tracker and return the result.

        :param timeout: Optional timeout in seconds.
        :raises Exception: If `complete()` or `trigger()` recorded an error.
        :return: The result of the send function.
        """
        self._event.wait(timeout=timeout)
        if self._exc:
            raise self._exc
        return self._result

    async def exception(self, timeout: Optional[float] = None) -> Optional[Exception]:
        """
        Await completion of the tracker and return the exception (if any).

        :param timeout: Optional timeout in seconds.
        :return: Exception if one was recorded, else None.
        """
        self._event.wait(timeout=timeout)
        return self._exc


# A pre-completed SendTracker that does nothing.
dummy_tracker = SendTracker(lambda: None, no_wait=True)
