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
import time
from typing import Callable, Generic, Optional, TypeVar, overload

__all__ = ["Deadline", "SendTracker", "dummy_tracker"]


class Deadline:
    """Represents a deadline with a start time and an optional timeout."""

    __slots__ = ("_start_time", "_timeout")

    _start_time: float
    _timeout: Optional[float]

    def __init__(self, timeout: Optional[float] = None, start_time: Optional[float] = None) -> None:
        """
        Initialize the Deadline object.

        Args:
            timeout: Optional timeout in seconds. If None, no deadline is set.
            start_time: Optional start time in seconds. If None, uses the current monotonic time.

        """
        self._start_time = start_time if start_time is not None else time.monotonic()
        self._timeout = timeout

    def expired(self) -> bool:
        """
        Check whether the deadline has passed.

        Returns:
            True if the deadline has expired, False otherwise.

        """
        if self._timeout is None:
            return False
        return (time.monotonic() - self._start_time) >= self._timeout

    def remaining(self) -> Optional[float]:
        """
        Return the remaining time before the deadline expires.

        Returns:
            The remaining time in seconds, or None if no deadline is set.
            If the deadline has already expired, returns 0.0.

        """
        if self._timeout is None:
            return None
        elapsed = time.monotonic() - self._start_time
        remaining = self._timeout - elapsed
        return max(0.0, remaining)


_Result = TypeVar("_Result")


class SendTracker(Generic[_Result]):
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

    __slots__ = ("_event", "_result", "_exc", "_send_func", "_no_wait", "_deadline")

    _event: threading.Event
    _result: Optional[_Result]
    _exc: Optional[Exception]
    _send_func: Callable[[], _Result]
    _no_wait: bool
    _deadline: Deadline

    @overload
    def __init__(self, send_func: Callable[[], _Result], *, deadline: Deadline, no_wait: bool = ...) -> None: ...

    @overload
    def __init__(
        self,
        send_func: Callable[[], _Result],
        *,
        start_time: Optional[float] = ...,
        timeout: Optional[float] = ...,
        no_wait: bool = ...,
    ) -> None: ...

    def __init__(
        self,
        send_func: Callable[[], _Result],
        *,
        deadline: Optional[Deadline] = None,
        start_time: Optional[float] = None,
        timeout: Optional[float] = None,
        no_wait: bool = False,
    ) -> None:
        """
        Initialize a SendTracker instance.

        Args:
            send_func: Callable function to be executed when trigger() is called.
            deadline: Optional Deadline object for setting time limits.
            start_time: Optional start time in seconds. Used if deadline is not provided.
            timeout: Optional timeout in seconds. Used if deadline is not provided.
            no_wait: If True, the tracker completes immediately after triggering.

        Raises:
            ValueError: If both deadline and (start_time or timeout) are provided.
        """
        if deadline and (start_time is not None or timeout is not None):
            raise ValueError("Provide either `deadline` or (`start_time` and `timeout`), not both.")
        if not deadline:
            deadline = Deadline(timeout=timeout, start_time=start_time)

        self._event = threading.Event()
        self._result = None
        self._exc = None
        self._send_func = send_func
        self._no_wait = no_wait
        self._deadline = deadline

    @property
    def deadline(self) -> Deadline:
        """
        Get the deadline associated with this tracker.

        Returns:
            Deadline object representing the time limit for this tracker.

        """
        return self._deadline

    @property
    def remaining_time(self) -> Optional[float]:
        """
        Get the remaining time before the tracker's deadline expires.

        Returns:
            The remaining time in seconds, or None if no deadline is set.
        """
        return self._deadline.remaining()

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

        Args:
            exc: Optional exception to record. If provided, it will be raised when `result()` is called.
        """
        self._exc = exc
        self._event.set()

    def result(self) -> Optional[_Result]:
        """
        Await completion of the tracker and return the result.

        Returns:
            The result of the send function if successful.

        Raises:
            Exception: If the send function raised an exception during execution.
        """
        self._event.wait(timeout=self.remaining_time)
        if self._exc:
            raise self._exc
        return self._result

    def exception(self) -> Optional[Exception]:
        """
        Await completion of the tracker and return the exception (if any).

        Returns:
            The exception raised during execution, or None if no exception occurred.
        """
        self._event.wait(timeout=self.remaining_time)
        return self._exc


# A pre-completed SendTracker that does nothing.
dummy_tracker = SendTracker(lambda: None, no_wait=True)
