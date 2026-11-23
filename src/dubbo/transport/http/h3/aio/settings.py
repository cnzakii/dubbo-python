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

"""The TLS and QUIC settings of an HTTP/3 client or server, read from its URL."""

import ssl

import attrs
import ngquic
from ngquic import h3

from dubbo.constants import (
    ACK_DELAY_EXPONENT_KEY,
    CA_CERT_FILE_KEY,
    CONGESTION_CONTROL_KEY,
    CONNECTION_INITIAL_WINDOW_SIZE_KEY,
    DEFAULT_CONNECTION_INITIAL_WINDOW_SIZE,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_INITIAL_WINDOW_SIZE,
    DEFAULT_MAX_CONCURRENT_STREAMS,
    DEFAULT_MAX_HEADER_LIST_SIZE,
    DISABLE_ACTIVE_MIGRATION_KEY,
    IDLE_TIMEOUT_KEY,
    INITIAL_UNI_WINDOW_SIZE_KEY,
    INITIAL_WINDOW_SIZE_KEY,
    KEEP_ALIVE_INTERVAL_KEY,
    MAX_ACK_DELAY_KEY,
    MAX_CONCURRENT_STREAMS_KEY,
    MAX_CONCURRENT_UNI_STREAMS_KEY,
    MAX_HEADER_LIST_SIZE_KEY,
    SSL_ENABLED_KEY,
    TLS_CERT_FILE_KEY,
    TLS_KEY_FILE_KEY,
)
from dubbo.transport.tls import server_hostname, ssl_handshake_timeout
from dubbo.url import SSL_CONTEXT, URL
from dubbo.utils.converters import boolean, milliseconds

__all__ = ["H3ConnectionConfig", "h3_settings"]

_ALPN = "h3"
# Upper bound of a stream receive window that grows with the measured bandwidth-delay product.
_MAX_STREAM_WINDOW = 16 * 1024 * 1024
_STREAM_SEND_BUFFER_SIZE_KEY = "stream-send-buffer-size"
_DEFAULT_STREAM_SEND_BUFFER_SIZE = 1024 * 1024
_QPACK_MAX_TABLE_CAPACITY_KEY = "qpack-max-table-capacity"
_DEFAULT_QPACK_MAX_TABLE_CAPACITY = 4096
_QPACK_BLOCKED_STREAMS_KEY = "qpack-blocked-streams"
_DEFAULT_QPACK_BLOCKED_STREAMS = 100
_H3_DATAGRAM_KEY = "h3-datagram"
_ENABLE_CONNECT_PROTOCOL_KEY = "enable-connect-protocol"
# HTTP/3 opens a control stream and two QPACK streams on each side, RFC 9114 section 6.2.
_MIN_UNI_STREAMS = 3
_DEFAULT_INITIAL_UNI_WINDOW_SIZE = 256 * 1024
_DEFAULT_MAX_ACK_DELAY = 0.025
# RFC 9000 section 18.2: max_ack_delay must be below 2^14 milliseconds and ack_delay_exponent at most 20.
_MAX_MAX_ACK_DELAY = 2**14 / 1000
_DEFAULT_ACK_DELAY_EXPONENT = 3
_MAX_ACK_DELAY_EXPONENT = 20
# The largest QUIC DATAGRAM frame accepted when HTTP datagrams are enabled; the path MTU bounds it first.
_MAX_DATAGRAM_FRAME_SIZE = 65535


@attrs.frozen
class H3ConnectionConfig:
    """What every connection of one client or server is made with.

    Attributes:
        tls: The TLS context, which offers or selects ``h3``.
        transport: The QUIC transport settings.
        h3: The HTTP/3 settings.
        server_name: The name a client checks the server certificate for.
    """

    tls: ngquic.TLSContext
    transport: ngquic.TransportConfig
    h3: h3.H3Config
    server_name: str | None


def h3_settings(url: URL, *, server: bool) -> H3ConnectionConfig:
    """Return the settings the parameters of ``url`` give, as `H3Client` and `dubbo.transport.http.HttpServer` describe them.

    Raises:
        ValueError: TLS is not enabled, an ``ssl.SSLContext`` is given, a
            certificate is missing, or a parameter is invalid.
        OSError: A TLS file cannot be loaded.
    """
    # 1. TLS, which QUIC always runs over, from files alone.
    parameters = url.parameters
    if not url.get_parameter(SSL_ENABLED_KEY, boolean, False):
        raise ValueError(f"HTTP/3 runs over TLS: set {SSL_ENABLED_KEY}=true")
    if url.attributes.get(SSL_CONTEXT) is not None:
        raise ValueError("an ssl.SSLContext cannot configure QUIC; give the TLS files as URL parameters")

    tls = _tls_context(
        server=server,
        ca_file=parameters.get(CA_CERT_FILE_KEY),
        cert_file=parameters.get(TLS_CERT_FILE_KEY),
        key_file=parameters.get(TLS_KEY_FILE_KEY),
    )

    # 2. Read every parameter, then check each against its range.
    fixed_stream_window = url.get_parameter(INITIAL_WINDOW_SIZE_KEY, int, None)
    stream_window = DEFAULT_INITIAL_WINDOW_SIZE if fixed_stream_window is None else fixed_stream_window
    connection_window = url.get_parameter(
        CONNECTION_INITIAL_WINDOW_SIZE_KEY, int, DEFAULT_CONNECTION_INITIAL_WINDOW_SIZE
    )
    max_streams = url.get_parameter(MAX_CONCURRENT_STREAMS_KEY, int, DEFAULT_MAX_CONCURRENT_STREAMS)
    max_header_list = url.get_parameter(MAX_HEADER_LIST_SIZE_KEY, int, DEFAULT_MAX_HEADER_LIST_SIZE)
    idle_timeout = url.get_parameter(IDLE_TIMEOUT_KEY, milliseconds, DEFAULT_IDLE_TIMEOUT)
    keep_alive = url.get_parameter(KEEP_ALIVE_INTERVAL_KEY, milliseconds, None)
    send_buffer = url.get_parameter(_STREAM_SEND_BUFFER_SIZE_KEY, int, None)
    qpack_capacity = url.get_parameter(_QPACK_MAX_TABLE_CAPACITY_KEY, int, _DEFAULT_QPACK_MAX_TABLE_CAPACITY)
    qpack_blocked = url.get_parameter(_QPACK_BLOCKED_STREAMS_KEY, int, _DEFAULT_QPACK_BLOCKED_STREAMS)
    datagrams = url.get_parameter(_H3_DATAGRAM_KEY, boolean, False)
    connect_protocol = url.get_parameter(_ENABLE_CONNECT_PROTOCOL_KEY, boolean, False)
    uni_streams = url.get_parameter(MAX_CONCURRENT_UNI_STREAMS_KEY, int, _MIN_UNI_STREAMS)
    uni_window = url.get_parameter(INITIAL_UNI_WINDOW_SIZE_KEY, int, _DEFAULT_INITIAL_UNI_WINDOW_SIZE)
    max_ack_delay = url.get_parameter(MAX_ACK_DELAY_KEY, milliseconds, _DEFAULT_MAX_ACK_DELAY)
    ack_delay_exponent = url.get_parameter(ACK_DELAY_EXPONENT_KEY, int, _DEFAULT_ACK_DELAY_EXPONENT)
    disable_migration = url.get_parameter(DISABLE_ACTIVE_MIGRATION_KEY, boolean, False)
    congestion_control = _congestion_control(parameters.get(CONGESTION_CONTROL_KEY))
    for key, value in (
        (INITIAL_WINDOW_SIZE_KEY, stream_window),
        (CONNECTION_INITIAL_WINDOW_SIZE_KEY, connection_window),
        (MAX_CONCURRENT_STREAMS_KEY, max_streams),
        (MAX_HEADER_LIST_SIZE_KEY, max_header_list),
        (_STREAM_SEND_BUFFER_SIZE_KEY, send_buffer),
        (KEEP_ALIVE_INTERVAL_KEY, keep_alive),
        (INITIAL_UNI_WINDOW_SIZE_KEY, uni_window),
    ):
        if value is not None and not value > 0:
            raise ValueError(f"URL parameter {key!r} must be positive")
    for key, value in (
        (IDLE_TIMEOUT_KEY, idle_timeout),
        (_QPACK_MAX_TABLE_CAPACITY_KEY, qpack_capacity),
        (_QPACK_BLOCKED_STREAMS_KEY, qpack_blocked),
    ):
        if value < 0:
            raise ValueError(f"URL parameter {key!r} must not be negative")
    if uni_streams < _MIN_UNI_STREAMS:
        raise ValueError(f"URL parameter {MAX_CONCURRENT_UNI_STREAMS_KEY!r} must be at least {_MIN_UNI_STREAMS}")
    if max_ack_delay >= _MAX_MAX_ACK_DELAY:
        raise ValueError(f"URL parameter {MAX_ACK_DELAY_KEY!r} must be below 16384")
    if not 0 <= ack_delay_exponent <= _MAX_ACK_DELAY_EXPONENT:
        raise ValueError(f"URL parameter {ACK_DELAY_EXPONENT_KEY!r} must be from 0 to {_MAX_ACK_DELAY_EXPONENT}")

    # 3. The QUIC transport and the HTTP/3 settings.
    transport = ngquic.TransportConfig(
        max_data=connection_window,
        # The connection window stays as set; each stream window grows unless it is set.
        max_window=None,
        max_stream_data_bidi_local=stream_window,
        max_stream_data_bidi_remote=stream_window,
        max_stream_window=_MAX_STREAM_WINDOW if fixed_stream_window is None else None,
        max_streams_bidi=max_streams,
        max_stream_data_uni=uni_window,
        max_streams_uni=uni_streams,
        auto_flow_control=False,
        stream_write_buffer_high=_DEFAULT_STREAM_SEND_BUFFER_SIZE if send_buffer is None else send_buffer,
        idle_timeout=idle_timeout or None,
        handshake_timeout=ssl_handshake_timeout(url) if server else None,
        keep_alive_interval=keep_alive,
        max_ack_delay=max_ack_delay,
        ack_delay_exponent=ack_delay_exponent,
        disable_active_migration=disable_migration,
        congestion_control=congestion_control,
        # A client's socket is connected to the address it dialed, so the connection cannot move.
        use_preferred_address=False,
        max_datagram_frame_size=_MAX_DATAGRAM_FRAME_SIZE if datagrams else None,
    )

    return H3ConnectionConfig(
        tls=tls,
        transport=transport,
        h3=h3.H3Config(
            max_field_section_size=max_header_list,
            qpack_max_dtable_capacity=qpack_capacity,
            qpack_blocked_streams=qpack_blocked,
            h3_datagram=datagrams,
            enable_connect_protocol=connect_protocol and server,
        ),
        server_name=None if server else server_hostname(url),
    )


def _congestion_control(name: str | None) -> ngquic.CongestionControl:
    if name is None:
        return ngquic.CongestionControl.CUBIC
    try:
        return ngquic.CongestionControl[name.upper()]
    except KeyError:
        choices = ", ".join(member.name.lower() for member in ngquic.CongestionControl)
        raise ValueError(f"URL parameter {CONGESTION_CONTROL_KEY!r} must be one of {choices}, not {name!r}") from None


def _tls_context(
    *, server: bool, ca_file: str | None, cert_file: str | None, key_file: str | None
) -> ngquic.TLSContext:
    if cert_file is None and (server or key_file is not None):
        raise ValueError(f"URL parameter {TLS_CERT_FILE_KEY!r} is required by a server and by {TLS_KEY_FILE_KEY!r}")

    tls = ngquic.TLSContext(ngquic.Role.SERVER if server else ngquic.Role.CLIENT)
    if ca_file is not None:
        tls.load_verify_locations(cafile=ca_file)
        if server:
            tls.verify_mode = ssl.CERT_REQUIRED
    elif not server:
        tls.load_default_certs()
    if cert_file is not None:
        # With an empty password, an encrypted key fails at once, with no prompt on the terminal.
        tls.load_cert_chain(cert_file, key_file, password="")
    tls.set_alpn_protocols([_ALPN])
    return tls
