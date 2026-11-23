# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""The settings every HTTP/2 connection of one client or server is made with, read from its URL once."""

from collections.abc import Mapping

import attrs
import ngh2

from dubbo.constants import (
    CONNECTION_INITIAL_WINDOW_SIZE_KEY,
    DEFAULT_CONNECTION_INITIAL_WINDOW_SIZE,
    DEFAULT_INITIAL_WINDOW_SIZE,
    DEFAULT_KEEP_ALIVE_TIMEOUT,
    DEFAULT_MAX_CONCURRENT_STREAMS,
    DEFAULT_MAX_HEADER_LIST_SIZE,
    INITIAL_WINDOW_SIZE_KEY,
    KEEP_ALIVE_INTERVAL_KEY,
    KEEP_ALIVE_TIMEOUT_KEY,
    MAX_CONCURRENT_STREAMS_KEY,
    MAX_HEADER_LIST_SIZE_KEY,
)
from dubbo.url import URL
from dubbo.utils.converters import boolean, milliseconds

__all__ = ["PROTOCOL_CONNECTION_WINDOW_SIZE", "H2ConnectionConfig", "h2_settings"]

# The connection window every HTTP/2 connection starts with.
PROTOCOL_CONNECTION_WINDOW_SIZE = 64 * 1024 - 1
_STREAM_SEND_BUFFER_SIZE_KEY = "stream-send-buffer-size"
_DEFAULT_STREAM_SEND_BUFFER_SIZE = 256 * 1024
_ENABLE_CONNECT_PROTOCOL_KEY = "enable-connect-protocol"


@attrs.frozen
class H2ConnectionConfig:
    """What every connection of one client or server is made with.

    Attributes:
        role: The side the connections are.
        engine: The configuration of their ngh2 engines.
        settings: The SETTINGS they start with.
        connection_window: Bytes of the connection receive window.
        stream_window: Bytes of the receive window each stream starts with.
        window_fixed: Whether the stream window stays as set; otherwise it
            grows with the bandwidth-delay product.
        keep_alive_interval: Seconds between PINGs; ``None`` sends none.
        keep_alive_timeout: Seconds a PING may go unanswered.
        send_buffer_size: Bytes of body a stream queues before a send waits.
    """

    role: ngh2.Role
    engine: ngh2.Configuration
    settings: Mapping[ngh2.Setting, int]
    connection_window: int
    stream_window: int
    window_fixed: bool
    keep_alive_interval: float | None
    keep_alive_timeout: float
    send_buffer_size: int


def h2_settings(url: URL, *, server: bool) -> H2ConnectionConfig:
    """Return the settings the parameters of ``url`` give, as `H2Client` and `dubbo.transport.http.HttpServer` describe them.

    Raises:
        ValueError: A parameter is invalid, or a value is one HTTP/2 does not allow.
    """
    role = ngh2.Role.SERVER if server else ngh2.Role.CLIENT
    max_streams = url.get_parameter(MAX_CONCURRENT_STREAMS_KEY, int, DEFAULT_MAX_CONCURRENT_STREAMS)
    max_header_list = url.get_parameter(MAX_HEADER_LIST_SIZE_KEY, int, DEFAULT_MAX_HEADER_LIST_SIZE)
    fixed_stream_window = url.get_parameter(INITIAL_WINDOW_SIZE_KEY, int, None)
    stream_window = DEFAULT_INITIAL_WINDOW_SIZE if fixed_stream_window is None else fixed_stream_window
    connection_window = url.get_parameter(
        CONNECTION_INITIAL_WINDOW_SIZE_KEY, int, DEFAULT_CONNECTION_INITIAL_WINDOW_SIZE
    )
    send_buffer_size = url.get_parameter(_STREAM_SEND_BUFFER_SIZE_KEY, int, _DEFAULT_STREAM_SEND_BUFFER_SIZE)
    if send_buffer_size <= 0:
        raise ValueError(f"URL parameter {_STREAM_SEND_BUFFER_SIZE_KEY!r} must be positive")

    engine = ngh2.Configuration(
        auto_window_update=False,
        initial_peer_max_concurrent_streams=max_streams,
        max_inbound_header_list_size=max_header_list,
    )
    settings = {
        ngh2.Setting.MAX_CONCURRENT_STREAMS: max_streams,
        ngh2.Setting.INITIAL_WINDOW_SIZE: stream_window,
        ngh2.Setting.MAX_HEADER_LIST_SIZE: max_header_list,
    }
    if not server:
        settings[ngh2.Setting.ENABLE_PUSH] = 0
    elif url.get_parameter(_ENABLE_CONNECT_PROTOCOL_KEY, boolean, False):
        settings[ngh2.Setting.ENABLE_CONNECT_PROTOCOL] = 1

    # ngh2 checks each value against HTTP/2 as a connection takes it; this one checks them once, here.
    check = ngh2.Connection(role, engine)
    check.initiate_connection(settings)
    if connection_window != PROTOCOL_CONNECTION_WINDOW_SIZE:
        check.set_local_window_size(connection_window)

    return H2ConnectionConfig(
        role=role,
        engine=engine,
        settings=settings,
        connection_window=connection_window,
        stream_window=stream_window,
        window_fixed=fixed_stream_window is not None,
        keep_alive_interval=url.get_parameter(KEEP_ALIVE_INTERVAL_KEY, milliseconds, None),
        keep_alive_timeout=url.get_parameter(KEEP_ALIVE_TIMEOUT_KEY, milliseconds, DEFAULT_KEEP_ALIVE_TIMEOUT),
        send_buffer_size=send_buffer_size,
    )
