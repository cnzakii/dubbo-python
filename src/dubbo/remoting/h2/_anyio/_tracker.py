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
from typing import Callable, Generic, Optional, TypeVar

import anyio

__all__ = ["SendTracker", "DummySendTracker"]

T = TypeVar("T")


class SendTracker(Generic[T]):
    """
    A tracker for coordinating deferred sending actions, such as pushing data
    to a sans-io protocol core (e.g., hyper-h2), with optional wait for confirmation.

    Unlike a general-purpose future, `SendTracker` controls result generation via
    `trigger()` and optionally delays completion until the caller confirms delivery.

    Example usage:

        tracker = SendTracker(lambda: h2_conn.send_headers(...), no_wait=False)
        tracker.trigger()
        data = h2_conn.data_to_send()
        await transport.send(data)
        tracker.complete(exc=None)  # or tracker.complete(exc=Exception("Error")) if an error occurred
        await tracker.result()  # or await tracker.exception() if you do not want an exception to be raised
    """

    __slots__ = ("_event", "_result", "_exc", "_send_func", "_no_wait")

    _event: anyio.Event
    _result: Optional[T]
    _exc: Optional[Exception]
    _send_func: Callable[[], T]
    _no_wait: bool

    def __init__(self, send_func: Callable[[], T], no_wait: bool = False) -> None:
        self._event = anyio.Event()
        self._result = None
        self._exc = None
        self._send_func = send_func
        self._no_wait = no_wait

    def trigger(self) -> None:
        """
        Invoke the send function and optionally complete the future.

        Stores the result returned by the function.
        If `no_wait` is True or an exception occurs, the future is marked as complete immediately.
        """
        try:
            self._result = self._send_func()
        except Exception as e:
            self._exc = e
        finally:
            # If `no_wait` is True or an exception occurred, set the event immediately.
            if self._no_wait or self._exc:
                self._event.set()

    def complete(self, exc: Optional[Exception] = None) -> None:
        """
        Mark the future as complete, used when `no_wait=False` and caller wants
        to confirm data has been sent.
        """
        self._exc = exc
        if not self._event.is_set():
            self._event.set()

    async def result(self, timeout: Optional[float] = None) -> Optional[T]:
        """
        Wait for the tracker to complete and return the result.
        Raises the stored exception if one was set.
        """
        if not self._event.is_set():
            with anyio.fail_after(timeout):
                await self._event.wait()
        if self._exc:
            raise self._exc
        return self._result

    async def exception(self, timeout: Optional[float] = None) -> Optional[Exception]:
        """
        Wait for the tracker to complete and return the exception, if any.
        """
        if not self._event.is_set():
            with anyio.fail_after(timeout):
                await self._event.wait()
        return self._exc


class DummySendTracker(SendTracker):
    """
    A dummy send tracker that does not perform any actual sending or waiting.
    It is used for testing purposes or when no real sending is needed.
    """

    def __init__(self) -> None:
        super().__init__(lambda: None, no_wait=True)
