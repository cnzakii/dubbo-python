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
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from typing import Callable, Optional, TypeVar, Union

import anyio
from anyio import abc as anyio_abc
from h2 import events as h2_events, exceptions as h2_exceptions
from h2.config import H2Configuration
from h2.connection import H2Connection

from dubbo.common.types import BytesLike
from dubbo.common.utils import common as common_utils
from dubbo.logger import logger
from dubbo.remoting.h2 import (
    AsyncHttp2Connection,
    AsyncHttp2Stream,
    AsyncHttp2StreamHandlerType,
    AsyncPingAckHandlerType,
    AsyncSettingsAckHandlerType,
    Http2ErrorCode,
    Http2SettingsType,
)
from dubbo.remoting.h2.common import AUTO_PROCESS_EVENTS, STREAM_EVENTS, Http2ChangedSetting, Http2ChangedSettingsType
from dubbo.remoting.h2.exceptions import (
    H2ConnectionError,
    H2ConnectionTerminatedError,
    H2ProtocolError,
    convert_h2_exception,
)

from ._managers import CallbackManager, StreamManager
from ._stream import AnyIOHttp2Stream
from ._tracker import AsyncSendTracker, dummy_tracker

__all__ = ["BaseHttp2Connection"]

from ...backend.exceptions import ConnectError

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], Awaitable[None]]]


class BaseHttp2Connection(AsyncHttp2Connection, abc.ABC):
    """Base class for HTTP/2 connections using AnyIO."""

    _tg: anyio_abc.TaskGroup
    _h2_core: H2Connection

    # some managers
    _stream_manager: StreamManager
    _ping_manager: CallbackManager[bytes, bytes]
    _settings_manager: CallbackManager[str, Http2ChangedSettingsType]

    # event dispatcher
    _event_dispatcher: _EventDispatcher

    # some flags
    _closed_event: anyio_abc.Event
    _conn_exc: Optional[Exception]

    def __init__(
        self,
        h2_config: H2Configuration,
        stream_handler: Optional[AsyncHttp2StreamHandlerType] = None,
        task_group: Optional[anyio_abc.TaskGroup] = None,
    ) -> None:
        """Initialize a new HTTP/2 connection.

        Args:
            h2_config: Configuration options for the HTTP/2 protocol.
            stream_handler: Optional callback for handling new streams.
        """
        super().__init__()
        self._tg = task_group or anyio.create_task_group()
        self._h2_core = H2Connection(h2_config)

        self._stream_manager = StreamManager(self._tg, stream_handler, self._streams_monitor)
        self._ping_manager: CallbackManager[bytes, bytes] = CallbackManager(self._tg)
        self._settings_manager: CallbackManager[str, Http2ChangedSettingsType] = CallbackManager(self._tg)

        self._closed_event = anyio.Event()
        self._conn_exc = None

        # Map HTTP/2 event types to their handler methods
        self._event_dispatcher: _EventDispatcher = {
            h2_events.RequestReceived: self._handle_request_received,
            h2_events.ConnectionTerminated: self._handle_connection_terminated,
            h2_events.PingAckReceived: self._handle_ping_ack,
            h2_events.SettingsAcknowledged: self._handle_settings_ack,
            h2_events.UnknownFrameReceived: self._handle_unknown_event,
        }

    # ----------- Properties -----------
    @property
    def h2_core(self) -> H2Connection:
        """Get the internal h2 connection instance.

        Returns:
            The underlying H2Connection object from the h2 library.
        """
        return self._h2_core

    @property
    def task_group(self) -> anyio_abc.TaskGroup:
        """Get the task group managing this connection.

        Returns:
            The TaskGroup instance used for managing background tasks.
        """
        return self._tg

    @property
    def stream_manager(self) -> StreamManager:
        """Get the stream manager.

        Returns:
            The StreamManager instance used for routing HTTP/2 stream events.
        """
        return self._stream_manager

    @property
    def connected(self) -> bool:
        """Checks if the connection is currently active.

        Returns:
            bool: True if the connection is active, False otherwise.
        """
        return not self._closed_event.is_set() and not self._conn_exc

    # ----------- Internal API for sending data -----------

    async def send(self, tracker: AsyncSendTracker) -> None:
        """Initiates sending data over the HTTP/2 connection.

        This method is an internal API used by HTTP/2 streams and should not
        be called by end-users directly.

        Args:
            tracker: A send tracker containing send logic.
        """
        exc = self._conn_exc
        if exc and not (isinstance(exc, H2ConnectionTerminatedError) and exc.remote_termination):
            # Only suppress the exception if the connection was gracefully closed
            # by the remote peer using a GOAWAY frame. In all other cases—such as
            # local termination, protocol errors, or unexpected disconnects—re-raise
            # the stored connection exception.
            tracker.complete(exc)
            return

        await self._do_send(tracker)

    @abc.abstractmethod
    async def _do_send(self, tracker: AsyncSendTracker) -> None:
        """Actually perform the send operation."""
        raise NotImplementedError()

    @asynccontextmanager
    async def _send_guard(self):
        """Context manager to handle exceptions during send operations.

        Converts H2 library exceptions to our custom exception classes and ensures
        proper error propagation.
        """
        try:
            yield
        except (H2ProtocolError, H2ConnectionError):
            # already handled by the stream, just re-raise
            raise
        except h2_exceptions.H2Error as e:
            inner_exc = convert_h2_exception(e)
            raise inner_exc from e
        except Exception as e:
            # Convert any other exception to H2ConnectionError
            inner_exc = H2ConnectionError(f"Error during send operation: {type(e).__name__}: {e}", e)
            raise inner_exc from e

    # ----------- Public API for HTTP/2 operations -----------

    async def create_stream(self, stream_id: int = -1) -> AsyncHttp2Stream:
        if self._conn_exc:
            raise self._conn_exc
        return AnyIOHttp2Stream(self, stream_id)

    async def update_settings(
        self, settings: Http2SettingsType, handler: Optional[AsyncSettingsAckHandlerType] = None
    ) -> None:
        async with self._send_guard():
            tracker = AsyncSendTracker(lambda: self._h2_core.update_settings(settings))

            if handler:
                # Only support one handler for settings acknowledgment
                self._settings_manager.register("settings", handler)

            try:
                await self.send(tracker)
                await tracker.result()
            except Exception as e:
                if handler:
                    # If the settings update fails, unregister the callback
                    self._settings_manager.unregister("settings", handler)
                raise e

    async def ping(self, payload: BytesLike, handler: Optional[AsyncPingAckHandlerType] = None) -> None:
        async with self._send_guard():
            payload_b = common_utils.to_bytes(payload)
            if len(payload_b) != 8:
                raise H2ProtocolError(f"PING payload must be exactly 8 bytes, got {len(payload_b)}")

            tracker = AsyncSendTracker(lambda: self._h2_core.ping(opaque_data=payload_b))

            if handler:
                self._ping_manager.register(payload_b, handler)

            try:
                await self.send(tracker)
                await tracker.result()
            except Exception as e:
                # If the ping fails, unregister the callback
                if handler:
                    self._ping_manager.unregister(payload_b, handler)
                raise e

    async def aclose(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[BytesLike] = None,
    ) -> None:
        async with self._send_guard():
            if self._closed_event.is_set():
                logger.debug("Connection already closed, ignoring aclose request")
                return

            additional_data = common_utils.to_bytes(additional_data) if additional_data else None

            tracker = AsyncSendTracker(
                lambda: self._h2_core.close_connection(
                    error_code=error_code,
                    last_stream_id=last_stream_id,
                    additional_data=additional_data,
                )
            )

            await self.send(tracker)
            await tracker.result()
            self._conn_exc = H2ConnectionTerminatedError(
                error_code=error_code,
                last_stream_id=last_stream_id or 0,
                additional_data=additional_data,
                remote_termination=False,  # Local termination
            )

            # Build a ConnectionTerminated event to notify the relevant streams
            event = h2_events.ConnectionTerminated()
            event.error_code = error_code
            event.last_stream_id = last_stream_id or 0
            event.additional_data = additional_data
            await self.stream_manager.dispatch_event(event)

    # ----------- Event Handlers -----------
    async def handle_events(self, events: list[h2_events.Event]) -> None:
        """
        Dispatch incoming HTTP/2 events to appropriate handlers.

        Args:
            events: List of HTTP/2 events to process.
        """
        for event in events:
            event_type = type(event)
            if event_type in STREAM_EVENTS:
                # Dispatch stream-level events to the stream manager
                await self.stream_manager.dispatch_event(event)
                continue

            # If the event is not a stream-level event, check if it has a custom handler
            handler = self._event_dispatcher.get(event_type, self._handle_ignored_events)
            await handler(event)

            if event_type in AUTO_PROCESS_EVENTS and not self._conn_exc:
                # Automatically process certain events that don't require user intervention
                # For example, RemoteSettingsChanged and PingReceived
                # we need to send a dummy tracker to flush the h2_core
                self._tg.start_soon(self.send, dummy_tracker)

    async def _handle_request_received(self, event: h2_events.RequestReceived) -> None:
        """Handle a new incoming request.

        This method is called when a new request is received on the connection.
        It creates a new stream for the request and notifies the stream manager.

        Args:
            event: RequestReceived event from the h2 library.
        """
        # Create and register a new stream for the request
        assert event.stream_id is not None, "RequestReceived event must contain stream_id"
        stream = await self.create_stream(event.stream_id)
        self.stream_manager.register(stream)
        # Dispatch the event to the stream manager
        await self.stream_manager.dispatch_event(event)

    async def _handle_connection_terminated(self, event: h2_events.ConnectionTerminated) -> None:
        """Handle a GOAWAY frame from the peer.

        Args:
            event: ConnectionTerminated event from the h2 library.
        """
        error_code = event.error_code
        if error_code is None:
            error_code = Http2ErrorCode.NO_ERROR

        self._conn_exc = H2ConnectionTerminatedError(
            error_code=error_code,
            last_stream_id=event.last_stream_id or 0,
            additional_data=event.additional_data,
            remote_termination=True,
        )
        # Notify the stream manager about the connection termination
        await self._stream_manager.dispatch_event(event)

    async def _handle_ping_ack(self, event: h2_events.PingAckReceived) -> None:
        """Handle a PING ACK frame from the peer.

        Args:
            event: PingAckReceived event from the h2 library.
        """
        assert event.ping_data is not None, "PingAckReceived event must contain ping_data"
        await self._ping_manager.dispatch_one(event.ping_data, event.ping_data)

    async def _handle_settings_ack(self, event: h2_events.SettingsAcknowledged) -> None:
        """Handle a SETTINGS ACK frame from the peer.

        Args:
            event: SettingsAcknowledged event from the h2 library.
        """
        converted_settings: Http2ChangedSettingsType = {
            int(k): Http2ChangedSetting(
                setting_code=int(v.setting),
                original_value=v.original_value,
                new_value=v.new_value,
            )
            for k, v in event.changed_settings.items()
        }
        # Notify the settings manager about the acknowledged settings
        await self._settings_manager.dispatch_one("settings", converted_settings)

    async def _handle_ignored_events(self, event: h2_events.Event) -> None:
        """Handle events that don't require special processing.

        These events are typically auto-processed by the h2 library.

        Args:
            event: An HTTP/2 event from the h2 library.
        """
        # Logging ignored events
        logger.debug("Ignored event: %s", type(event).__name__)

    async def _handle_unknown_event(self, event: h2_events.UnknownFrameReceived) -> None:
        """Handle unknown frame types.

        Args:
            event: UnknownFrameReceived event from the h2 library.
        """
        logger.error("Unknown event: %s", event)

    def _streams_monitor(self, active_streams: int) -> None:
        """Monitor the number of active streams and handle connection closure.

        This callback is invoked by the StreamManager whenever the number of active
        streams changes. When there are no active streams left on a connection that
        has received a GOAWAY, the connection can be fully closed.

        Args:
            active_streams: The current number of active streams.
        """
        if not self._conn_exc or active_streams > 0:
            return
        logger.debug("No active streams left on terminated connection, closing")
        self._finalize()

    def _finalize(
        self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR, exc: Optional[Exception] = None
    ) -> None:
        if self._closed_event.is_set():
            return

        if self.stream_manager.count > 0:
            if not error_code:
                if isinstance(exc, ConnectError):
                    error_code = Http2ErrorCode.CONNECT_ERROR
                elif isinstance(exc, h2_exceptions.ProtocolError):
                    error_code = exc.error_code
                else:
                    error_code = Http2ErrorCode.INTERNAL_ERROR

            # Build a ConnectionTerminated event to notify the relevant streams
            event = h2_events.ConnectionTerminated()
            event.error_code = error_code
            event.last_stream_id = 0

            try:
                self.task_group.start_soon(self.stream_manager.dispatch_event, event)
            except Exception as e:
                logger.error("Failed to dispatch event: %s", e)
                pass

        # Finally set the closed event
        self._closed_event.set()

    async def wait_until_closed(self) -> None:
        """Wait until the connection is closed.

        This method blocks until the connection is fully closed, which happens when:
        1. An abnormal network condition has occurred.
        2. A GOAWAY frame was sent or received, and all streams have been completed.

        Returns:
            None
        """
        await self._closed_event.wait()
