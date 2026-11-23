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

"""The settings of the Triple protocol: HTTP/3, plain HTTP calls, their CORS and their OpenAPI document."""

from typing import Any

import attrs
from attrs import converters, validators

from dubbo.constants import (
    ACK_DELAY_EXPONENT_KEY,
    COMPRESSOR_KEY,
    CONGESTION_CONTROL_KEY,
    CONNECT_TIMEOUT_KEY,
    CONNECTION_INITIAL_WINDOW_SIZE_KEY,
    CORS_ALLOW_CREDENTIALS_KEY,
    CORS_ALLOWED_HEADERS_KEY,
    CORS_ALLOWED_METHODS_KEY,
    CORS_ALLOWED_ORIGINS_KEY,
    CORS_EXPOSED_HEADERS_KEY,
    CORS_KEY,
    CORS_MAX_AGE_KEY,
    DISABLE_ACTIVE_MIGRATION_KEY,
    HEALTH_ENABLED_KEY,
    HTTP3_ENABLED_KEY,
    HTTP3_KEY,
    HTTP3_NEGOTIATION_KEY,
    IDLE_TIMEOUT_KEY,
    INITIAL_UNI_WINDOW_SIZE_KEY,
    INITIAL_WINDOW_SIZE_KEY,
    KEEP_ALIVE_INTERVAL_KEY,
    KEEP_ALIVE_TIMEOUT_KEY,
    MAX_ACK_DELAY_KEY,
    MAX_CONCURRENT_STREAMS_KEY,
    MAX_CONCURRENT_UNI_STREAMS_KEY,
    MAX_HEADER_LIST_SIZE_KEY,
    MAX_RECEIVE_MESSAGE_SIZE_KEY,
    OPENAPI_ENABLED_KEY,
    OPENAPI_INFO_DESCRIPTION_KEY,
    OPENAPI_INFO_TITLE_KEY,
    OPENAPI_INFO_VERSION_KEY,
    OPENAPI_KEY,
    OPENAPI_PATH_KEY,
    OPENAPI_SECURITY_KEY,
    OPENAPI_SECURITY_SCHEMES_KEY,
    OPENAPI_SERVERS_KEY,
    OPENAPI_SETTINGS_KEY,
    REFLECTION_ENABLED_KEY,
    REST_DEFAULT_MAPPING_KEY,
    REST_ENABLED_KEY,
    REST_KEY,
)
from dubbo.utils.converters import to_milliseconds
from dubbo.utils.json import dump_json

from .fields import check_url_keys, non_negative, positive, string_map, url_nested, url_parameter

__all__ = ["CorsConfig", "Http3Config", "OpenAPIConfig", "RestConfig", "TripleConfig"]


@attrs.define(frozen=True, kw_only=True)
class Http3Config:
    """Serve and call Triple over HTTP/3 too, and tune its QUIC connections.

    HTTP/3 runs over TLS alone. A server that enables it serves it on the UDP
    port of its TCP port's number, and its HTTP/2 responses announce it with
    ``alt-svc``. A caller that enables it starts on HTTP/2 and moves its new
    calls to HTTP/3 once a response announces it and HTTP/3 has connected in
    the background. A call that finds HTTP/3 not connected, as after its
    idle timeout, goes over HTTP/2 while it connects again; a failed
    handshake leaves HTTP/3 for 5 minutes, twice as long after each failure
    in a row. ``None`` leaves a value to the protocol, or for a
    reference, to the consumer; a setting that Triple also has, such as
    ``max_concurrent_streams``, then takes the value of Triple's.

    Attributes:
        enabled: Whether a server serves HTTP/3 and a caller uses it;
            ``None`` means no.
        negotiation: Whether a caller waits for a server to announce HTTP/3,
            on HTTP/2; ``False`` has it call over HTTP/3 alone. ``None``
            means yes.
        max_concurrent_streams: Calls the other side may run at once on one
            connection.
        initial_window_size: Bytes the other side may send on one call
            before it waits for more credit.
        connection_initial_window_size: Bytes the other side may send on one
            connection before it waits for more credit.
        max_concurrent_uni_streams: Unidirectional streams the other side
            may open at once, at least 3; ``None`` means 3.
        initial_uni_window_size: Bytes the other side may send on one
            unidirectional stream; ``None`` means 256 KiB.
        idle_timeout: Seconds without traffic before a connection closes,
            ``0`` for no limit; ``None`` means 30.
        max_ack_delay: Seconds this side may delay an acknowledgement, below
            16.384; ``None`` means 0.025.
        ack_delay_exponent: Exponent of the ACK delays this side encodes,
            from 0 to 20; ``None`` means 3.
        disable_active_migration: Whether the other side must keep its
            address for the connection's life; ``None`` means no.
        congestion_control: How this side paces what it sends: ``cubic``,
            ``reno`` or ``bbr``; ``None`` means ``cubic``.

    The settings become URL parameters after ``http3.``, as in
    ``triple.http3.enabled``.
    """

    enabled: bool | None = attrs.field(default=None, metadata=url_parameter(HTTP3_ENABLED_KEY))
    negotiation: bool | None = attrs.field(default=None, metadata=url_parameter(HTTP3_NEGOTIATION_KEY))
    max_concurrent_streams: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(MAX_CONCURRENT_STREAMS_KEY)
    )
    initial_window_size: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(INITIAL_WINDOW_SIZE_KEY)
    )
    connection_initial_window_size: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(CONNECTION_INITIAL_WINDOW_SIZE_KEY)
    )
    max_concurrent_uni_streams: int | None = attrs.field(
        default=None,
        validator=validators.optional(validators.ge(3)),
        metadata=url_parameter(MAX_CONCURRENT_UNI_STREAMS_KEY),
    )
    initial_uni_window_size: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(INITIAL_UNI_WINDOW_SIZE_KEY)
    )
    idle_timeout: float | None = attrs.field(
        default=None, validator=non_negative, metadata=url_parameter(IDLE_TIMEOUT_KEY, to_milliseconds)
    )
    max_ack_delay: float | None = attrs.field(
        default=None,
        validator=validators.optional([validators.ge(0), validators.lt(16.384)]),
        metadata=url_parameter(MAX_ACK_DELAY_KEY, to_milliseconds),
    )
    ack_delay_exponent: int | None = attrs.field(
        default=None,
        validator=validators.optional([validators.ge(0), validators.le(20)]),
        metadata=url_parameter(ACK_DELAY_EXPONENT_KEY),
    )
    disable_active_migration: bool | None = attrs.field(
        default=None, metadata=url_parameter(DISABLE_ACTIVE_MIGRATION_KEY)
    )
    congestion_control: str | None = attrs.field(
        default=None,
        validator=validators.optional(validators.in_(("cubic", "reno", "bbr"))),
        metadata=url_parameter(CONGESTION_CONTROL_KEY),
    )


@attrs.define(frozen=True, kw_only=True)
class CorsConfig:
    """Let web pages of other origins call the methods served over plain HTTP, as CORS allows.

    CORS is on once ``allowed_origins`` names an origin. ``None`` leaves a
    value to the protocol.

    Attributes:
        allowed_origins: The origins that may call, such as
            ``https://example.com``, or ``*`` for any; ``None`` turns CORS
            off.
        allowed_methods: The HTTP methods they may call with, or ``*`` for
            any; ``None`` means ``GET``, ``HEAD`` and ``POST``.
        allowed_headers: The request header fields they may send, or ``*``
            for any; ``None`` means any.
        exposed_headers: The response header fields their scripts may read
            besides the safelisted ones, or ``*`` for any.
        allow_credentials: Whether they may send cookies and other
            credentials; ``None`` means no. It cannot go with ``*`` origins.
        max_age: Seconds a browser may keep a preflight response; ``None``
            means 1800.

    The settings become URL parameters after ``cors.``, as in
    ``triple.rest.cors.allowed-origins``.
    """

    allowed_origins: tuple[str, ...] | None = attrs.field(
        default=None, metadata=url_parameter(CORS_ALLOWED_ORIGINS_KEY, ",".join)
    )
    allowed_methods: tuple[str, ...] | None = attrs.field(
        default=None, metadata=url_parameter(CORS_ALLOWED_METHODS_KEY, ",".join)
    )
    allowed_headers: tuple[str, ...] | None = attrs.field(
        default=None, metadata=url_parameter(CORS_ALLOWED_HEADERS_KEY, ",".join)
    )
    exposed_headers: tuple[str, ...] | None = attrs.field(
        default=None, metadata=url_parameter(CORS_EXPOSED_HEADERS_KEY, ",".join)
    )
    allow_credentials: bool | None = attrs.field(default=None, metadata=url_parameter(CORS_ALLOW_CREDENTIALS_KEY))
    max_age: int | None = attrs.field(default=None, validator=non_negative, metadata=url_parameter(CORS_MAX_AGE_KEY))


@attrs.define(frozen=True, kw_only=True)
class OpenAPIConfig:
    """Serve an OpenAPI 3.1 document of the methods served over plain HTTP, and Swagger UI and ReDoc to read it.

    ``None`` leaves a value to the protocol.

    Attributes:
        enabled: Whether the document is served; ``None`` means no.
        path: The path the document and its viewers are served under;
            ``None`` means ``/dubbo/openapi``.
        info_title: The title of the document; ``None`` means
            ``Dubbo OpenAPI``.
        info_version: The version of the document; ``None`` means ``1.0.0``.
        info_description: What the document describes.
        servers: The URLs the API is reached at, which Swagger UI calls,
            such as that of a proxy before a path prefix; ``None`` means
            the URL the document is read from.
        security_schemes: The security schemes of the document by name, as
            OpenAPI security scheme objects, such as
            ``{"token": {"type": "apiKey", "in": "header", "name": "token"}}``.
            They describe the API; a filter, such as the token filter,
            checks the calls.
        security: The security requirements of every operation, as OpenAPI
            security requirement objects, such as ``[{"token": []}]``.
        settings: Further settings by name: ``swagger-ui.cdn`` and
            ``redoc.cdn``, where Swagger UI and ReDoc are loaded from, by
            default ``https://cdn.jsdelivr.net/npm/swagger-ui-dist@5`` and
            ``https://cdn.jsdelivr.net/npm/redoc@2/bundles``; and any other
            ``swagger-ui.<name>``, a parameter of Swagger UI, such as
            ``swagger-ui.persistAuthorization``, whose value is JSON, or
            text where it is no JSON.

    The settings become URL parameters after ``openapi.``, as in
    ``triple.rest.openapi.enabled``.
    """

    enabled: bool | None = attrs.field(default=None, metadata=url_parameter(OPENAPI_ENABLED_KEY))
    path: str | None = attrs.field(default=None, metadata=url_parameter(OPENAPI_PATH_KEY))
    info_title: str | None = attrs.field(default=None, metadata=url_parameter(OPENAPI_INFO_TITLE_KEY))
    info_version: str | None = attrs.field(default=None, metadata=url_parameter(OPENAPI_INFO_VERSION_KEY))
    info_description: str | None = attrs.field(default=None, metadata=url_parameter(OPENAPI_INFO_DESCRIPTION_KEY))
    servers: tuple[str, ...] | None = attrs.field(
        default=None, converter=converters.optional(tuple), metadata=url_parameter(OPENAPI_SERVERS_KEY, ",".join)
    )
    security_schemes: dict[str, dict[str, Any]] | None = attrs.field(
        default=None, metadata=url_parameter(OPENAPI_SECURITY_SCHEMES_KEY, dump_json)
    )
    security: tuple[dict[str, tuple[str, ...]], ...] | None = attrs.field(
        default=None, converter=converters.optional(tuple), metadata=url_parameter(OPENAPI_SECURITY_KEY, dump_json)
    )
    settings: dict[str, str] = attrs.field(factory=dict, metadata=url_nested(OPENAPI_SETTINGS_KEY))


@attrs.define(frozen=True, kw_only=True)
class RestConfig:
    """Serve the unary methods of Triple over plain HTTP, with JSON.

    ``None`` leaves a value to the protocol.

    Attributes:
        enabled: Whether a server serves methods over plain HTTP; ``None``
            means no.
        enable_default_mapping: Whether a method without a route is served
            on ``/{interface}/{method}``; ``None`` means yes.
        cors: Which web pages of other origins may call the methods.
        openapi: Whether an OpenAPI document of the methods is served.

    The settings become URL parameters after ``rest.``, as in
    ``triple.rest.enabled``.
    """

    enabled: bool | None = attrs.field(default=None, metadata=url_parameter(REST_ENABLED_KEY))
    enable_default_mapping: bool | None = attrs.field(default=None, metadata=url_parameter(REST_DEFAULT_MAPPING_KEY))
    cors: CorsConfig = attrs.field(factory=CorsConfig, metadata=url_nested(CORS_KEY))
    openapi: OpenAPIConfig = attrs.field(factory=OpenAPIConfig, metadata=url_nested(OPENAPI_KEY))


@attrs.define(frozen=True, kw_only=True)
class TripleConfig:
    """Tune the Triple protocol, on the side whose configuration holds it.

    A protocol's settings tune its server; the consumer's and a reference's
    tune the calls they make. ``None`` leaves a value to the protocol, or for
    a reference, to the consumer.

    Attributes:
        max_receive_message_size: Bytes of the largest message received;
            ``None`` means 4 MiB.
        compressor: Name of the compression of the messages sent, such as
            ``gzip``; ``None`` sends them uncompressed. A server compresses
            only for callers that accept it.
        max_concurrent_streams: Calls the other side may run at once on one
            connection; ``None`` means 100.
        initial_window_size: Bytes the other side may send on one call
            before it waits for more credit; ``None`` means a window that
            starts at 1 MiB and grows with the bandwidth-delay product
            measured on the connection, up to 16 MiB.
        connection_initial_window_size: Bytes the other side may send on one
            connection before it waits for more credit; ``None`` means 16 MiB.
        max_header_list_size: Bytes of the largest header block received;
            ``None`` means 64 KiB.
        keep_alive_interval: Seconds without traffic before a PING checks
            the connection; ``None`` sends no PING.
        keep_alive_timeout: Seconds to wait for the PING acknowledgement
            before the connection closes; ``None`` means 20.
        connect_timeout: Seconds a caller waits for a connection to be
            established before the call fails as unavailable; ``None``
            means 3.
        health_enabled: Whether a server also serves
            ``grpc.health.v1.Health``; ``None`` means no.
        reflection_enabled: Whether a server also serves
            ``grpc.reflection.v1.ServerReflection`` and its earlier
            ``grpc.reflection.v1alpha``; ``None`` means no.
        http3: Whether to serve and call over HTTP/3 too, and the settings
            of its connections.
        rest: Whether a server serves its methods over plain HTTP too.
        parameters: Further options of the protocol and its transport, as
            text, such as ``stream-send-buffer-size``. A reference's merge key
            by key over the consumer's.

    The settings become URL parameters after ``triple.``, as in
    ``triple.compressor``.

    Raises:
        ValueError: A parameter is one that a field sets.
    """

    max_receive_message_size: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(MAX_RECEIVE_MESSAGE_SIZE_KEY)
    )
    compressor: str | None = attrs.field(default=None, metadata=url_parameter(COMPRESSOR_KEY))
    max_concurrent_streams: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(MAX_CONCURRENT_STREAMS_KEY)
    )
    initial_window_size: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(INITIAL_WINDOW_SIZE_KEY)
    )
    connection_initial_window_size: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(CONNECTION_INITIAL_WINDOW_SIZE_KEY)
    )
    max_header_list_size: int | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(MAX_HEADER_LIST_SIZE_KEY)
    )
    keep_alive_interval: float | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(KEEP_ALIVE_INTERVAL_KEY, to_milliseconds)
    )
    keep_alive_timeout: float | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(KEEP_ALIVE_TIMEOUT_KEY, to_milliseconds)
    )
    connect_timeout: float | None = attrs.field(
        default=None, validator=positive, metadata=url_parameter(CONNECT_TIMEOUT_KEY, to_milliseconds)
    )
    health_enabled: bool | None = attrs.field(default=None, metadata=url_parameter(HEALTH_ENABLED_KEY))
    reflection_enabled: bool | None = attrs.field(default=None, metadata=url_parameter(REFLECTION_ENABLED_KEY))
    http3: Http3Config = attrs.field(factory=Http3Config, metadata=url_nested(HTTP3_KEY))
    rest: RestConfig = attrs.field(factory=RestConfig, metadata=url_nested(REST_KEY))
    parameters: dict[str, str] = attrs.field(factory=dict, validator=string_map, metadata=url_nested())

    def __attrs_post_init__(self) -> None:
        check_url_keys(self, self.parameters)
