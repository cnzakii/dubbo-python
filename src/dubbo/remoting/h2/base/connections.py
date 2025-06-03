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
import abc
import typing
from types import TracebackType
from typing import Any, Optional, Union

from h2 import events as h2_events
from h2.connection import H2Connection

from ..registries import Http2ErrorCode
from .common import (
    AsyncPingAckHandlerType,
    AsyncSettingsAckHandlerType,
    Http2SettingsType,
    PingAckHandlerType,
    SettingsAckHandlerType,
)

if typing.TYPE_CHECKING:
    from .managers import StreamManager
    from .streams import AsyncHttp2Stream, Http2Stream

__all__ = ["Http2Connection", "AsyncHttp2Connection"]


class BaseHttp2Connection(abc.ABC):
    """Base class for HTTP/2 connections."""

    # ------ Properties ------
    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        """Check if the connection is currently established."""
        raise NotImplementedError()

    @abc.abstractmethod
    def get_extra_info(self, name: str) -> Any:
        """Get extra information about the connection."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def h2_core(self) -> H2Connection:
        """Get the underlying H2Connection instance."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def stream_manager(self) -> "StreamManager":
        """Get the stream manager for this connection."""
        raise NotImplementedError()

    # ------ Methods ------

    @abc.abstractmethod
    def handle_events(self, events: list[h2_events.Event]) -> None:
        """Handle a list of H2 events."""
        raise NotImplementedError()


class Http2Connection(BaseHttp2Connection, abc.ABC):
    """Synchronous base class for HTTP/2 connections."""

    @abc.abstractmethod
    def __enter__(self) -> "Http2Connection":
        """Enter the synchronous context manager."""
        raise NotImplementedError()

    @abc.abstractmethod
    def __exit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        """Exit the synchronous context manager."""
        raise NotImplementedError()

    @abc.abstractmethod
    def create_stream(self, stream_id: int = -1) -> "Http2Stream":
        """Create a new stream with the given ID."""
        raise NotImplementedError()

    # ------ Sending Methods ------
    @abc.abstractmethod
    def update_settings(
        self,
        settings: Http2SettingsType,
        handler: Optional[SettingsAckHandlerType] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Update HTTP/2 settings and optionally register a handler for acknowledgment."""
        raise NotImplementedError()

    @abc.abstractmethod
    def ping(
        self, payload: bytes, handler: Optional[PingAckHandlerType] = None, timeout: Optional[float] = None
    ) -> None:
        """Send a PING frame with the given payload and optionally register a handler for acknowledgment."""
        raise NotImplementedError()

    @abc.abstractmethod
    def goaway(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Send a GOAWAY frame to the peer."""
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """Close the connection synchronously."""
        raise NotImplementedError()


class AsyncHttp2Connection(BaseHttp2Connection, abc.ABC):
    """Asynchronous base class for HTTP/2 connections."""

    @abc.abstractmethod
    async def __aenter__(self) -> "AsyncHttp2Connection":
        """Enter the asynchronous context manager."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def __aexit__(
        self, exc_type: Optional[type], exc_value: Optional[BaseException], traceback: Optional[TracebackType]
    ) -> None:
        """Exit the asynchronous context manager."""
        raise NotImplementedError()

    @abc.abstractmethod
    def create_stream(self, stream_id: int = -1) -> "AsyncHttp2Stream":
        """Create a new stream with the given ID."""
        raise NotImplementedError()

    # ------ Sending Methods ------
    @abc.abstractmethod
    async def update_settings(
        self, settings: Http2SettingsType, handler: Optional[AsyncSettingsAckHandlerType] = None
    ) -> None:
        """Update HTTP/2 settings and optionally register a handler for acknowledgment."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def ping(self, payload: bytes, handler: Optional[AsyncPingAckHandlerType] = None) -> None:
        """Send a PING frame with the given payload and optionally register a handler for acknowledgment."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def goaway(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[bytes] = None,
    ) -> None:
        """Send a GOAWAY frame to the peer."""
        raise NotImplementedError()

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Close the connection asynchronously."""
        raise NotImplementedError()
