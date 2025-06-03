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
import typing
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Callable, Optional, Union

from h2 import events as h2_events
from hpack import HeaderTuple

from ..registries import Http2SettingCode

if typing.TYPE_CHECKING:
    from .connections import AsyncHttp2Connection, Http2Connection
    from .streams import AsyncHttp2Stream, Http2Stream

__all__ = [
    "HeadersType",
    "Http2SettingsType",
    "Http2ChangedSetting",
    "Http2ChangedSettingsType",
    "Http2StreamHandlerType",
    "Http2ConnectionHandlerType",
    "PingAckHandlerType",
    "SettingsAckHandlerType",
    "AsyncHttp2StreamHandlerType",
    "AsyncHttp2ConnectionHandlerType",
    "AsyncPingAckHandlerType",
    "AsyncSettingsAckHandlerType",
    "AUTO_PROCESS_EVENTS",
    "STREAM_EVENTS",
    "parse_headers",
]

# HTTP/2 headers
HeadersType = list[tuple[str, str]]

# HTTP/2 settings
Http2SettingsType = dict[Union[Http2SettingCode, int], int]


@dataclass(frozen=True)
class Http2ChangedSetting:
    """Represents a changed HTTP/2 setting with before/after values.

    Attributes:
        setting_code: The HTTP/2 setting code that was changed.
        original_value: The value before the change, None if previously unset.
        new_value: The value after the change.
    """

    setting_code: int
    original_value: Optional[int]
    new_value: int


# Mapping of setting codes to their change information
Http2ChangedSettingsType = dict[int, Http2ChangedSetting]

# Synchronous HTTP/2 handler type definitions
Http2StreamHandlerType = Callable[["Http2Stream"], None]
Http2ConnectionHandlerType = Callable[["Http2Connection"], None]
PingAckHandlerType = Callable[[bytes], None]
SettingsAckHandlerType = Callable[[Http2ChangedSettingsType], None]

# Asynchronous versions of the handler types
AsyncHttp2StreamHandlerType = Callable[["AsyncHttp2Stream"], Awaitable[None]]
AsyncHttp2ConnectionHandlerType = Callable[["AsyncHttp2Connection"], Awaitable[None]]
AsyncPingAckHandlerType = Callable[[bytes], Awaitable[None]]
AsyncSettingsAckHandlerType = Callable[[Http2ChangedSettingsType], Awaitable[None]]


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
    """Convert HTTP/2 HeaderTuple list to string header tuples."""
    if not raw_headers:
        return []

    try:
        return [(str(name), str(value)) for name, value in raw_headers]
    except (UnicodeDecodeError, AttributeError) as e:
        raise ValueError(f"Failed to parse headers: {e}") from e
