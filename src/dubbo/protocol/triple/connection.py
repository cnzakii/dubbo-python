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

"""The connections a Triple invoker calls one server over: HTTP/2, HTTP/3, or HTTP/2 until HTTP/3 is announced."""

import asyncio
import logging
from typing import Self

from dubbo.constants import CLIENT_KEY, HTTP3_ENABLED_KEY, HTTP3_KEY, HTTP3_NEGOTIATION_KEY, SSL_ENABLED_KEY
from dubbo.exceptions import RpcError, Status
from dubbo.extension import ExtensionError, Resource, extensions
from dubbo.transport.http.alt_svc import AltService, parse_alt_svc
from dubbo.transport.http.h2 import H2Client
from dubbo.transport.http.h3 import H3Client, H3ConnectionError
from dubbo.url import URL
from dubbo.utils.converters import boolean

from .streams import Headers, HttpClientStream

__all__ = ["HTTP3_ENABLED", "HTTP3_NEGOTIATION", "TripleClient", "http3_server_url", "http3_url"]

_logger = logging.getLogger(__name__)

_HTTP3_PREFIX = f"{HTTP3_KEY}."
# The parameters of a transport URL that turn HTTP/3 on and tell how a caller finds it.
HTTP3_ENABLED = _HTTP3_PREFIX + HTTP3_ENABLED_KEY
HTTP3_NEGOTIATION = _HTTP3_PREFIX + HTTP3_NEGOTIATION_KEY
# The ALPN protocol ID of HTTP/3, as alt-svc names it.
_H3 = "h3"
# Seconds HTTP/3 on a port is not tried after a failed handshake: 5 minutes, doubled for each failure in a row up to
# two days.
_BROKEN_DELAY = 300.0
_BROKEN_MAX_SHIFT = 18
_BROKEN_MAX_DELAY = 2 * 86400.0


class TripleClient(Resource):
    """The connections to one server that an invoker opens the streams of its calls on.

    ``url`` is the URL of the HTTP/2 client, which `http3_url` turns into
    that of the HTTP/3 client; both are named by its ``client`` parameter.
    With ``http3.enabled`` false or absent, calls go over HTTP/2 alone. With
    it true and ``http3.negotiation`` false, they go over HTTP/3 alone.

    Otherwise calls go over HTTP/2 until a response announces HTTP/3 on the
    same host with ``alt-svc``, RFC 7838, which `alt_svc` takes over TLS.
    The HTTP/3 client then connects in the background, and each call goes
    over HTTP/3 while it is connected and the announcement is fresh, else
    over HTTP/2, as does a call whose HTTP/3 stream fails before its request
    headers go out. A call that finds HTTP/3 not connected, as after its
    idle timeout, has it connect in the background. A handshake that fails
    drops HTTP/3 on that port for 5 minutes, twice as long after each
    failure in a row up to two days, and one that succeeds forgets the
    failures. A call stays on the version its stream was opened on.

    Each announcement replaces the one before. One of another port moves
    HTTP/3 there; ``clear``, ``ma=0``, or one that names no HTTP/3 on the
    same host withdraws it; one that names nothing valid leaves the earlier
    one in place. HTTP/3 that is moved or withdrawn takes no new calls and
    closes once those on it finish.
    """

    def __init__(self, url: URL) -> None:
        self.url = url
        self._name = url.parameters.get(CLIENT_KEY)
        enabled = url.get_parameter(HTTP3_ENABLED, boolean, False)
        negotiation = url.get_parameter(HTTP3_NEGOTIATION, boolean, True)

        # The client every call falls back on: HTTP/3 when it is used alone, else HTTP/2.
        self._primary: H2Client | H3Client
        if enabled and not negotiation:
            self._primary = extensions.get(H3Client, self._name)(http3_url(url))
        else:
            self._primary = extensions.get(H2Client, self._name)(url)

        # Whether alt-svc may move calls to HTTP/3; it needs TLS, and stops for good once HTTP/3 cannot start.
        self._negotiating = enabled and negotiation and url.get_parameter(SSL_ENABLED_KEY, boolean, False)
        # The last alt-svc value, the alternative it announced, and the loop time that stops being fresh.
        self._alt_svc: bytes | None = None
        self._announced: AltService | None = None
        self._fresh_until = 0.0
        # The HTTP/3 client of the announced port, which calls use while it is connected, and the task connecting it.
        self._h3: H3Client | None = None
        self._h3_port = 0
        self._connecting: asyncio.Task[None] | None = None
        # Of each port whose handshake failed: the failures in a row, and the loop time HTTP/3 may be tried again.
        self._broken: dict[int, tuple[int, float]] = {}
        # HTTP/3 clients moved or withdrawn, each closing once its calls finish.
        self._draining: dict[H3Client, asyncio.Task[None]] = {}
        self._closed = False

    @property
    def available(self) -> bool:
        """Whether a connection to the server is established now."""
        h3 = self._h3
        return (h3 is not None and h3.available) or self._primary.available

    async def open_stream(self, headers: Headers, *, end_stream: bool = False) -> HttpClientStream:
        """Open a request stream over HTTP/3 while it may be used, else over the version the client starts with.

        Raises:
            RpcError: UNAVAILABLE, as the client closed while the stream waited for a connection.
            H2Error: The HTTP/2 stream could not be opened.
            H3Error: The HTTP/3 stream could not be opened.
        """
        try:
            return await self._open_stream(headers, end_stream)
        except RuntimeError:
            if not self._closed:
                raise
            raise RpcError(Status.UNAVAILABLE, f"the client of {self.url.address} closed") from None

    def alt_svc(self, value: bytes) -> None:
        """Take the ``alt-svc`` field of a response."""
        if not self._negotiating or self._closed:
            return

        # A value seen last time only renews the freshness of its announcement.
        if value != self._alt_svc:
            self._alt_svc = value
            alternatives = parse_alt_svc(value)
            # A value that names nothing valid replaces nothing.
            if alternatives != ():
                host = self.url.host or ""
                self._announced = next(
                    (
                        one
                        for one in alternatives or ()
                        if one.protocol == _H3 and one.host.lower() in ("", host) and one.max_age > 0
                    ),
                    None,
                )
                # The client of another port, or of a withdrawn announcement, takes no new calls.
                if self._announced is None or self._announced.port != self._h3_port:
                    self._drop_h3()
        if self._announced is None:
            return

        loop = asyncio.get_running_loop()
        self._fresh_until = loop.time() + self._announced.max_age
        port = self._announced.port
        if self._h3 is None and loop.time() >= self._broken.get(port, (0, 0.0))[1]:
            self._start_h3(port)

    async def aclose(self) -> None:
        self._closed = True
        connecting = self._connecting
        self._drop_h3()
        draining = tuple(self._draining.items())
        try:
            # Clients still draining get the grace of a close, as the calls on them end with the invoker.
            await asyncio.gather(
                *(h3.aclose() for h3, _ in draining),
                *(task for _, task in draining),
                *(() if connecting is None else (connecting,)),
                return_exceptions=True,
            )
        finally:
            await self._primary.aclose()

    async def __aenter__(self) -> Self:
        await self._primary.__aenter__()
        return self

    async def _open_stream(self, headers: Headers, end_stream: bool) -> HttpClientStream:
        loop = asyncio.get_running_loop()
        h3 = self._h3
        if h3 is not None and loop.time() < self._fresh_until:
            if not h3.connected:
                # The call goes over HTTP/2 rather than wait for the handshake, which goes on in the background.
                if self._connecting is None:
                    self._connecting = loop.create_task(self._connect(h3, entered=True))
            else:
                try:
                    return await h3.open_stream(headers, end_stream=end_stream)
                except RuntimeError:
                    # Withdrawn by an announcement before the request went out, so the request may go over HTTP/2.
                    if h3 is self._h3 or self._closed:
                        raise
                except H3ConnectionError as error:
                    # The request headers did not go out, so the request may go over HTTP/2.
                    _logger.debug("A call to %s leaves HTTP/3 for HTTP/2: %s", self.url.address, error)
        return await self._primary.open_stream(headers, end_stream=end_stream)

    def _start_h3(self, port: int) -> None:
        """Make the HTTP/3 client of ``port`` and have it connect in the background."""
        try:
            h3 = extensions.get(H3Client, self._name)(http3_url(self.url).replace(port=port))
        except (ExtensionError, ValueError, OSError) as error:
            self._negotiating = False
            _logger.warning("Calls to %s stay on HTTP/2, as HTTP/3 cannot be used: %s", self.url.address, error)
            return

        # Kept before it is entered, so that dropping it closes it too.
        self._h3, self._h3_port = h3, port
        self._connecting = asyncio.get_running_loop().create_task(self._connect(h3, entered=False))

    async def _connect(self, h3: H3Client, *, entered: bool) -> None:
        """Enter ``h3`` unless ``entered``, and connect it in the background.

        A client that cannot start ends negotiating for good; a handshake
        that fails drops HTTP/3 on its port for a while.
        """
        try:
            if not entered:
                try:
                    await h3.__aenter__()
                except Exception as error:
                    # A client dropped meanwhile is already draining.
                    if h3 is self._h3:
                        self._negotiating = False
                        self._drop_h3()
                        _logger.warning(
                            "Calls to %s stay on HTTP/2, as HTTP/3 cannot start: %s", self.url.address, error
                        )
                    return
            try:
                await h3.connect()
            except RuntimeError:
                # Closed meanwhile, by a drop or by the client closing.
                return
            except H3ConnectionError as error:
                if h3 is self._h3:
                    self._mark_broken(error)
                return
        finally:
            if self._connecting is asyncio.current_task():
                self._connecting = None

        if h3 is self._h3:
            self._broken.pop(self._h3_port, None)
            # A client's first connection is news; connecting again, as after the idle timeout, is routine.
            (_logger.debug if entered else _logger.info)("Calls to %s move to HTTP/3", self.url.address)

    def _mark_broken(self, error: Exception) -> None:
        """Drop HTTP/3, whose handshake failed with ``error``, for twice as long as last time on its port."""
        port = self._h3_port
        failures = self._broken.get(port, (0, 0.0))[0]
        delay = min(_BROKEN_DELAY * (1 << min(failures, _BROKEN_MAX_SHIFT)), _BROKEN_MAX_DELAY)
        self._broken[port] = (failures + 1, asyncio.get_running_loop().time() + delay)
        self._drop_h3()
        _logger.warning("Calls to %s go back to HTTP/2 for %d seconds: %s", self.url.address, delay, error)

    def _drop_h3(self) -> None:
        """Stop using HTTP/3 and drain its client in the background, letting the calls on it finish."""
        h3, self._h3, self._h3_port = self._h3, None, 0
        connecting, self._connecting = self._connecting, None
        # _connect itself drops the client it failed to connect, and must not cancel itself.
        if connecting is not None and connecting is not asyncio.current_task():
            connecting.cancel()
        if h3 is not None:
            draining = asyncio.get_running_loop().create_task(h3.drain())
            self._draining[h3] = draining
            draining.add_done_callback(lambda task: self._drained(h3, task))

    def _drained(self, h3: H3Client, task: asyncio.Task[None]) -> None:
        """Forget ``h3`` once its drain ``task`` ends, logging how it failed, if it did."""
        self._draining.pop(h3, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            _logger.debug("The HTTP/3 client of %s failed to close: %s", self.url.address, error)


def http3_url(url: URL) -> URL:
    """Return the transport URL ``url`` with each HTTP/3 setting, ``http3.<key>``, in place of ``<key>``."""
    parameters = {key: value for key, value in url.parameters.items() if not key.startswith(_HTTP3_PREFIX)}
    parameters |= {key.removeprefix(_HTTP3_PREFIX): value for key, value in _http3_settings(url)}
    return url.replace(parameters=parameters)


def http3_server_url(url: URL) -> URL:
    """Return the transport URL ``url`` without the HTTP/3 switches, its settings ``http3.<key>`` left to the HTTP server."""
    return url.replace(
        parameters={
            key: value for key, value in url.parameters.items() if key not in (HTTP3_ENABLED, HTTP3_NEGOTIATION)
        }
    )


def _http3_settings(url: URL) -> list[tuple[str, str]]:
    """Return the HTTP/3 settings of ``url`` that its transport reads, those other than its switches."""
    return [
        (key, value)
        for key, value in url.parameters.items()
        if key.startswith(_HTTP3_PREFIX) and key not in (HTTP3_ENABLED, HTTP3_NEGOTIATION)
    ]
