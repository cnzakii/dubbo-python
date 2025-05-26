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
from collections.abc import Collection
from dataclasses import dataclass
from typing import Optional, Union

from h2 import events as h2_events
from hpack import HeaderTuple

from dubbo.common.utils import common as common_utils

from .registries import Http2SettingCode

# Type aliases for HTTP/2 protocol
HeadersType = Collection[tuple[str, str]]
"""Collection of HTTP/2 headers as (name, value) string tuples."""

Http2SettingsType = dict[Union[Http2SettingCode, int], int]
"""Mapping of HTTP/2 setting codes to their integer values."""


@dataclass(frozen=True)
class Http2ChangedSetting:
    """Represents a changed HTTP/2 setting with before/after values.

    This immutable dataclass tracks changes to HTTP/2 connection settings,
    providing both the original and new values for proper change handling.

    Attributes:
        setting_code: The HTTP/2 setting code that was changed.
        original_value: The value before the change, None if previously unset.
        new_value: The value after the change.
    """

    setting_code: int
    original_value: Optional[int]
    new_value: int


Http2ChangedSettingsType = dict[int, Http2ChangedSetting]
"""Mapping of setting codes to their change information."""


# HTTP/2 event classifications for processing strategy

AUTO_PROCESS_EVENTS = (
    h2_events.RemoteSettingsChanged,  # Automatic ACK by h2 library
    h2_events.PingReceived,  # Automatic PONG response by h2 library
)
"""Events automatically handled by the h2 library requiring no user intervention."""


STREAM_EVENTS = (
    h2_events.ResponseReceived,  # Response headers received
    h2_events.TrailersReceived,  # Trailing headers received (after END_STREAM)
    h2_events.InformationalResponseReceived,  # 1xx informational response (e.g., 100 Continue)
    h2_events.DataReceived,  # Data frame payload
    h2_events.StreamEnded,  # Stream closed cleanly (END_STREAM)
    h2_events.StreamReset,  # Stream was reset (RST_STREAM)
    h2_events.WindowUpdated,  # Flow control window update (per stream)
)
"""Events that require stream-level processing and user handling.
Note:
- h2_events.RequestReceived is excluded because it is treated at the connection level
    to allow dynamic stream instantiation for new incoming requests.
"""


def parse_headers(raw_headers: Optional[list[HeaderTuple]]) -> HeadersType:
    """Convert HTTP/2 HeaderTuple list to string header tuples.

    Transforms HPACK HeaderTuple objects (bytes, bytes) into standard
    HTTP header format (str, str) using proper string decoding.

    Args:
        raw_headers: List of HeaderTuple objects from h2/hpack library.
            Each tuple contains (header_name, header_value) as bytes.
            Can be None for empty header sets.

    Returns:
        Collection of headers as (str, str) tuples. Returns empty list
        if raw_headers is None or empty.

    Example:
        raw = [(b':method', b'GET'), (b':path', b'/api')]
        headers = parse_headers(raw)
        # Result: [(':method', 'GET'), (':path', '/api')]

    Raises:
        ValueError: If raw_headers cannot be parsed due to encoding issues.
    """
    if not raw_headers:
        return []

    try:
        return [(common_utils.to_str(name), common_utils.to_str(value)) for name, value in raw_headers]
    except (UnicodeDecodeError, AttributeError) as e:
        raise ValueError(f"Failed to parse headers: {e}") from e


class ReceivedData:
    """Memory-efficient HTTP/2 DATA frame handler with flow control.

    Provides zero-copy data access using memoryview and tracks flow control
    acknowledgment requirements. Designed for incremental data consumption
    while maintaining HTTP/2 flow control semantics.

    The class ensures that:
    - Data is accessed without unnecessary copying
    - Flow control acknowledgments are sent exactly once
    - Partial data consumption is supported efficiently

    Example:
        received = ReceivedData.from_h2(data_event)
        while not received.is_empty():
            chunk = received.get_data(1024)
            process_chunk(bytes(chunk))

        # Send flow control ACK
        ack_size = received.ack_size
        if ack_size > 0:
            connection.acknowledge(ack_size)
    """

    __slots__ = ("_data_view", "_ack_size")

    def __init__(self, data_view: memoryview, ack_size: int) -> None:
        """Initialize ReceivedData with data view and acknowledgment size.

        Args:
            data_view: Memory view of the received data for zero-copy access.
            ack_size: Flow control acknowledgment size required by HTTP/2.

        Raises:
            ValueError: If ack_size is negative.
        """
        if ack_size < 0:
            raise ValueError(f"ack_size must be non-negative, got {ack_size}")

        self._data_view = data_view
        self._ack_size = ack_size

    @property
    def ack_size(self) -> int:
        """Get and reset the flow control acknowledgment size.

        This is a one-shot property that returns the current acknowledgment
        size and immediately resets it to zero. This ensures flow control
        ACKs are sent exactly once per DATA frame.

        Returns:
            The acknowledgment size in bytes, then resets to 0.
        """
        size = self._ack_size
        self._ack_size = 0
        return size

    def is_empty(self) -> bool:
        """Check if all data has been consumed.

        Returns:
            True if no data remains, False if data is available.
        """
        return len(self._data_view) == 0

    def get_data(self, max_bytes: int = -1) -> memoryview:
        """Extract data chunk and advance the internal position.

        Provides efficient data access without copying. The returned
        memoryview shares memory with the original data until consumed.

        Args:
            max_bytes: Maximum bytes to extract. Use -1 for all remaining data.
                Must be positive or -1.

        Returns:
            Memory view of the extracted data chunk.

        Raises:
            ValueError: If max_bytes is invalid (negative except -1).

        Example:
            data = received.get_data(1024)  # Get up to 1KB
            all_data = received.get_data(-1)  # Get all remaining
        """
        if max_bytes < -1 or max_bytes == 0:
            raise ValueError(f"max_bytes must be positive or -1, got {max_bytes}")

        current_view = self._data_view

        if max_bytes == -1 or max_bytes >= len(current_view):
            # Return all remaining data
            chunk = current_view
            self._data_view = memoryview(b"")
        else:
            # Return partial data and update view
            chunk = current_view[:max_bytes]
            self._data_view = current_view[max_bytes:]

        return chunk

    @classmethod
    def from_h2(cls, event: h2_events.DataReceived) -> "ReceivedData":
        """Create ReceivedData from HTTP/2 DataReceived event.

        Factory method that properly extracts data and flow control
        information from h2 library events.

        Args:
            event: HTTP/2 DataReceived event from h2 library.

        Returns:
            New ReceivedData instance with event data and flow control info.

        Example:
            def on_data_received(event):
                received = ReceivedData.from_h2(event)
                process_data(received)
        """
        data_view = memoryview(event.data) if event.data else memoryview(b"")
        ack_size = event.flow_controlled_length or 0
        return cls(data_view, ack_size)
