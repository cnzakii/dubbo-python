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

"""
Synchronous object stream utilities module

Provides thread-safe object stream implementation with blocking and non-blocking read/write operations.
"""

import queue
import threading
import time
from typing import Generic, Optional, TypeVar

__all__ = ["SyncObjectStream", "EndOfStream", "StreamFullError", "StreamEmptyError"]

_T = TypeVar("_T")


class StreamFullError(Exception):
    """Stream buffer full exception

    Raised when attempting to put an item into a full stream buffer in non-blocking mode.
    This is a normal flow control exception indicating the buffer has reached its capacity.
    """

    pass


class StreamEmptyError(Exception):
    """Stream buffer empty exception

    Raised when attempting to get an item from an empty stream buffer in non-blocking mode.
    This is a normal flow control exception indicating no items are available.
    """

    pass


class EndOfStream(Exception):
    """End of stream exception

    Raised when attempting to read from or write to a closed stream.
    This is a normal flow control exception indicating the stream has reached its end.
    """

    pass


class SyncObjectStream(Generic[_T]):
    """Thread-safe synchronous object stream

    This is a bounded buffer implementation that supports multithreaded concurrent access. It provides:
    - Thread-safe read and write operations
    - Blocking and non-blocking operation modes
    - Timeout control
    - Graceful stream closure mechanism

    Key features:
    - Supports generic types for type safety
    - Uses condition variables for efficient thread synchronization
    - Supports bounded buffer to prevent memory overflow
    - Provides rich status query methods

    Use cases:
    - Producer-consumer pattern
    - Inter-thread data exchange
    - Asynchronous processing pipelines
    """

    __slots__ = ("_buffer", "_condition", "_stopped", "_max_size")

    def __init__(self, max_size: int = 1):
        """Initialize synchronous object stream

        Args:
            max_size: Maximum buffer capacity, default is 1. Set to 0 for unbounded buffer.

        Raises:
            ValueError: When max_size is negative
        """
        if max_size < 0:
            raise ValueError("max_size must be non-negative")

        self._buffer = queue.Queue(maxsize=max_size)  # type: ignore[var-annotated]
        self._condition = threading.Condition()
        self._stopped = False
        self._max_size = max_size

    def put_nowait(self, item: _T) -> None:
        """Put an item into the stream in non-blocking mode

        Args:
            item: Item to put

        Raises:
            EndOfStream: If the stream is closed
            StreamFullError: If buffer is full and cannot put immediately
        """
        if self._stopped:
            raise EndOfStream()

        try:
            with self._condition:
                self._buffer.put_nowait(item)
                # Notify all waiting threads
                self._condition.notify_all()
        except queue.Full:
            raise StreamFullError()

    def put(self, item: _T, timeout: Optional[float] = None) -> None:
        """Put an item into the stream

        Args:
            item: Item to put
            timeout: Timeout in seconds. None means wait indefinitely, 0 means non-blocking

        Raises:
            EndOfStream: If the stream is closed
            StreamTimeoutError: If unable to put item within specified time
            ValueError: If timeout is negative
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")

        with self._condition:
            if self._stopped:
                raise EndOfStream()

            start_time = time.time()

            while True:
                # Check stream status again to prevent stream being closed while waiting
                if self._stopped:
                    raise EndOfStream()

                try:
                    self._buffer.put_nowait(item)
                    # Notify all waiting threads
                    self._condition.notify_all()
                    return
                except queue.Full:
                    # Buffer is full, need to wait
                    if timeout is not None:
                        elapsed = time.time() - start_time
                        remaining = timeout - elapsed
                        if remaining <= 0:
                            raise StreamFullError()
                        # Wait for the remaining time
                        self._condition.wait(remaining)
                    else:
                        # Wait indefinitely
                        self._condition.wait()

    def get_nowait(self) -> _T:
        """Get an item from the stream in non-blocking mode

        Returns:
            Item retrieved from the stream

        Raises:
            EndOfStream: If the stream is closed and buffer is empty
            StreamEmptyError: If buffer is empty and cannot get immediately
        """
        if self._stopped and self._buffer.empty():
            raise EndOfStream("No more items available in closed stream")

        try:
            with self._condition:
                item = self._buffer.get_nowait()
                # Notify all waiting threads
                self._condition.notify_all()
                return item
        except queue.Empty:
            raise StreamEmptyError()

    def get(self, timeout: Optional[float] = None) -> _T:
        """Get an item from the stream

        Args:
            timeout: Timeout in seconds. None means wait indefinitely, 0 means non-blocking

        Returns:
            Item retrieved from the stream

        Raises:
            EndOfStream: If the stream is closed and buffer is empty
            StreamTimeoutError: If unable to get item within specified time
            ValueError: If timeout is negative
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")

        with self._condition:
            start_time = time.time()

            while True:
                # If stream is stopped and buffer is empty, raise EndOfStream
                if self._stopped and self._buffer.empty():
                    raise EndOfStream()

                try:
                    item = self._buffer.get_nowait()
                    # Notify all waiting threads
                    self._condition.notify_all()
                    return item
                except queue.Empty:
                    # Buffer is empty, need to wait
                    if timeout is not None:
                        elapsed = time.time() - start_time
                        remaining = timeout - elapsed
                        if remaining <= 0:
                            raise StreamEmptyError()
                        # Wait for the remaining time
                        self._condition.wait(remaining)
                    else:
                        # Wait indefinitely
                        self._condition.wait()

    def stop(self) -> None:
        """Close the stream

        After closing the stream:
        - No new items will be accepted
        - Existing waiting operations will be awakened
        - get operations can still retrieve remaining items from buffer
        - get operations will raise EndOfStream exception after buffer is empty

        Note: This operation is idempotent, multiple calls will not cause side effects.
        """
        with self._condition:
            if not self._stopped:
                self._stopped = True
                # Notify all waiting threads
                self._condition.notify_all()

    def is_stopped(self) -> bool:
        """Check if the stream is closed

        Returns:
            True if the stream is closed, False otherwise
        """
        with self._condition:
            return self._stopped

    def empty(self) -> bool:
        """Check if the buffer is empty

        Returns:
            True if the buffer is empty, False otherwise
        """
        with self._condition:
            return self._buffer.empty()

    def full(self) -> bool:
        """Check if the buffer is full

        Returns:
            True if the buffer is full, False otherwise
            Always returns False for unbounded buffer
        """
        with self._condition:
            return self._buffer.full()

    def size(self) -> int:
        """Get the current number of items in the buffer

        Returns:
            Number of items in the buffer
        """
        with self._condition:
            return self._buffer.qsize()

    def max_size(self) -> int:
        """Get the maximum capacity of the buffer

        Returns:
            Maximum capacity of the buffer. 0 indicates unbounded buffer.
        """
        return self._max_size

    def clear(self) -> int:
        """Clear all items in the buffer

        Returns:
            Number of items cleared

        Raises:
            EndOfStream: If the stream is closed
        """
        with self._condition:
            if self._stopped:
                raise EndOfStream("Cannot clear a closed stream")

            cleared_count = 0
            while not self._buffer.empty():
                try:
                    self._buffer.get_nowait()
                    cleared_count += 1
                except queue.Empty:
                    break

            # Notify waiting put operations
            self._condition.notify_all()
            return cleared_count

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit, automatically close the stream"""
        self.stop()

    def __repr__(self) -> str:
        """String representation of the object"""
        with self._condition:
            status = "stopped" if self._stopped else "active"
            return f"SyncObjectStream(max_size={self._max_size}, current_size={self._buffer.qsize()}, status={status})"
