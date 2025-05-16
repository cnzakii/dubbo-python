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
from collections.abc import Awaitable
from contextlib import AsyncExitStack
from typing import Callable, Optional, Union

import anyio
from anyio import abc as anyio_abc
from h2 import events as h2_events
from h2.config import H2Configuration
from h2.connection import H2Connection

from dubbo import logger
from dubbo.common.types import BytesLike
from dubbo.common.utils import common as common_utils
from dubbo.remoting.backend import AsyncNetworkStream
from dubbo.remoting.backend.exceptions import ReceiveError, ReceiveTimeout, SendError, SendTimeout
from dubbo.remoting.h2 import AsyncHttp2Connection, Http2ErrorCode, Http2SettingCode
from dubbo.remoting.h2.exceptions import H2ConnectionError, H2ConnectionTerminatedError, H2ProtocolError

from ._stream import AnyIOHttp2Stream
from ._tracker import SendTracker, dummy_tracker

__all__ = ["AnyIOHttp2Connection"]


_LOGGER = logger.get_instance()


class PingAckManager:
    """
    A resource manager for handling ping acknowledgment callbacks.

    """

    __slots__ = ("_ping_ack_callbacks",)

    _ping_ack_callbacks: dict[bytes, list[Callable[[], Awaitable[None]]]]

    def __init__(self) -> None:
        self._ping_ack_callbacks = {}

    def register_callback(self, payload: bytes, callback: Callable[[], Awaitable[None]]) -> None:
        """
        Registers a callback to be triggered when the ping with the specified payload
        is acknowledged.

        :param payload: The payload of the ping that the callback will be associated with.
        :param callback: The callback function to be executed upon acknowledgment.

        :raises TypeError: If the provided callback is not callable.
        """
        if not callable(callback):
            raise TypeError("The provided callback is not callable.")

        # Add the callback to the mapping of callbacks for the given payload
        self._ping_ack_callbacks.setdefault(payload, []).append(callback)

    async def ack_ping(self, payload: bytes) -> None:
        """
        Notifies the manager that the ping with the given payload has been acknowledged.

        This action triggers the first callback for the specified payload. After the
        callback is executed, it is removed from the list of callbacks for that payload.

        If no callbacks are registered for the given payload, no action is performed.

        :param payload: The payload of the ping that has been acknowledged.

        Logs any errors encountered while executing the callback.

        Note:
            - Only the first callback for the specified payload is executed when
              the acknowledgment is received.
            - The callback is removed after being executed to prevent future
              execution for the same ping acknowledgment.
        """
        callbacks = self._ping_ack_callbacks.pop(payload, [])

        if callbacks:
            # Process the first callback in the list
            callback = callbacks.pop(0)
            try:
                # Await the callback if it is async
                await callback()
            except Exception as e:
                _LOGGER.error("Error while executing callback for ping ack: {}", e)
            finally:
                # If there are still callbacks left, reassign them to the payload
                if callbacks:
                    self._ping_ack_callbacks[payload] = callbacks
        else:
            _LOGGER.debug("No callbacks registered for payload: {}", payload.hex())


async def _noop_stream_handler(stream: AnyIOHttp2Stream) -> None:
    """
    A no-operation stream handler.

    This function does nothing and is used as a default handler.
    """
    pass


class StreamManager:
    """
    Manages active HTTP/2 streams in a connection.

    This class provides mechanisms to register, unregister, and dispatch
    events to individual streams. It also supports pruning stale streams.
    """

    __slots__ = ("_conn", "_streams", "_count", "_stream_handler", "_count_monitor")

    _conn: "AnyIOHttp2Connection"
    _streams: dict[int, AnyIOHttp2Stream]
    _stream_handler: Callable[[AnyIOHttp2Stream], Awaitable[None]]
    _count_monitor: Callable[[int], None]

    def __init__(
        self,
        conn: "AnyIOHttp2Connection",
        stream_handler: Optional[Callable[[AnyIOHttp2Stream], Awaitable[None]]] = None,
        count_monitor: Optional[Callable[[int], None]] = None,
    ):
        self._conn = conn
        self._streams = {}
        self._count = 0
        self._stream_handler = stream_handler or _noop_stream_handler
        self._count_monitor = count_monitor or (lambda _: None)

    @property
    def count(self) -> int:
        """
        :returns: The number of active streams.
        """
        return self._count

    @count.setter
    def count(self, count: int) -> None:
        """
        Set the number of active streams and trigger the change callback.
        :param count: The new count of active streams.
        """
        self._count = count
        self._count_monitor(count)

    def register(self, stream: AnyIOHttp2Stream) -> None:
        """
        Register a stream with the manager.

        :param stream: The stream instance to register.
        :raises H2ConnectionError: If the stream has an invalid ID (<= 0).
        """
        if stream.stream_id <= 0:
            raise H2ConnectionError("Cannot register stream: uninitialized stream_id.")
        self._streams[stream.stream_id] = stream
        self.count += 1
        _LOGGER.debug("[HTTP/2] Stream {} registered.", stream.stream_id)
        # Start the stream handler in the task group
        self._conn.task_group.start_soon(self._stream_handler, stream)

    def unregister(self, stream: AnyIOHttp2Stream) -> None:
        """
        Unregister a stream from the manager.

        :param stream: The stream instance to remove.
        :raises H2ConnectionError: If the stream is not currently registered.
        """
        try:
            del self._streams[stream.stream_id]
            self.count -= 1
            _LOGGER.debug("Stream {} unregistered.", stream.stream_id)
        except KeyError:
            raise H2ConnectionError("Stream {} is not registered.", stream.stream_id)

    def get_stream(self, stream_id: int) -> Optional[AnyIOHttp2Stream]:
        """
        Retrieve a stream by its ID.

        :param stream_id: The ID of the stream.
        :return: The stream instance, or None if not found.
        """
        return self._streams.get(stream_id)

    def remove_stream(self, highest_stream_id: int) -> None:
        """
        Remove all streams with stream IDs higher than the given ID.

        :param highest_stream_id: Upper limit for stream retention.
        """
        removed = []
        for stream_id in list(self._streams.keys()):
            if stream_id > highest_stream_id:
                del self._streams[stream_id]
                removed.append(stream_id)
        if removed:
            _LOGGER.debug("[HTTP/2]Removed streams with IDs: {}", removed)
            self.count -= len(removed)

    async def dispatch_event(self, event: h2_events.Event) -> None:
        """
        Dispatch an HTTP/2 event to the corresponding stream.

        :param event: An instance of h2.events.Event.
        """
        if isinstance(event, h2_events.WindowUpdated) and event.stream_id == 0:
            # Broadcast to all streams
            for stream in self._streams.values():
                await stream.handle_event(event)
            _LOGGER.debug("[HTTP/2] Dispatched connection-level WindowUpdated to all streams.")
        elif isinstance(event, h2_events.ConnectionTerminated):
            # Handle connection termination
            last_stream_id = event.last_stream_id or 0
            for stream in self._streams.values():
                if stream.stream_id > last_stream_id:
                    await stream.handle_event(event)
            _LOGGER.debug(
                "[HTTP/2] Dispatched connection-level ConnectionTerminated to streams.(ID > {})", last_stream_id
            )
            self.remove_stream(last_stream_id)
        elif getattr(event, "stream_id", 0) > 0:
            # Handle stream-level events
            stream_id: int = event.stream_id  # type: ignore[attr-defined]
            if isinstance(event, h2_events.RequestReceived):
                # Create a new stream for the request
                stream = self._conn.create_stream(stream_id)
                self.register(stream)
            stream = self.get_stream(stream_id)  # type: ignore[assignment]
            if stream:
                await stream.handle_event(event)
            else:
                _LOGGER.warning("[HTTP/2] Stream {} not found for event: {}", stream_id, event)


class AnyIOHttp2Connection(AsyncExitStack, AsyncHttp2Connection):
    """
    Asynchronous HTTP/2 connection handler based on AnyIO.

    This class handles HTTP/2 framing, sending/receiving, ping management,
    and stream lifecycle management in a fully async fashion.
    """

    _tg: anyio_abc.TaskGroup
    _h2_core: H2Connection
    _net_stream: AsyncNetworkStream
    _ping_ack_manager: PingAckManager
    _stream_manager: StreamManager
    _send_buffer: tuple[anyio_abc.ObjectSendStream[SendTracker], anyio_abc.ObjectReceiveStream[SendTracker]]

    # some flags
    _closed_event: anyio_abc.Event
    _conn_exc: Optional[Exception] = None

    def __init__(
        self,
        net_stream: AsyncNetworkStream,
        h2_config: H2Configuration,
        stream_handler: Optional[Callable[[AnyIOHttp2Stream], Awaitable[None]]] = None,
    ) -> None:
        super().__init__()
        self._tg = anyio.create_task_group()
        self._h2_core = H2Connection(h2_config)
        self._net_stream = net_stream
        self._ping_ack_manager = PingAckManager()
        self._stream_manager = StreamManager(self, stream_handler, self._streams_monitor)
        sender, receiver = anyio.create_memory_object_stream[SendTracker](max_buffer_size=0)
        self._send_buffer = (sender, receiver)
        self._closed_event = anyio.Event()

    async def __aenter__(self):
        await super().__aenter__()
        # Register TaskGroup and bind cancel_scope
        self._tg = await self.enter_async_context(anyio.create_task_group())
        # Initialize h2 connection
        await self._initialize()
        # Start sending and receiving tasks
        self._tg.start_soon(self._send_loop)  # type: ignore[no-untyped-call]
        self._tg.start_soon(self._receive_loop)  # type: ignore[no-untyped-call]
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)
        try:
            # Wait for the connection to close
            await self.aclose()
        finally:
            await self._net_stream.aclose()
            self._tg.cancel_scope.cancel()

    @property
    def h2_core(self) -> H2Connection:
        """
        :returns: The internal h2 connection core.
        """
        return self._h2_core

    @property
    def stream_manager(self) -> StreamManager:
        """
        :returns: The stream manager used for routing events.
        """
        return self._stream_manager

    @property
    def task_group(self) -> anyio_abc.TaskGroup:
        """
        :returns: The task group managing the connection.
        """
        return self._tg

    @property
    def connected(self) -> bool:
        """
        :returns: True if the connection is active, False otherwise.
        """
        return not self._closed_event.is_set()

    def create_stream(self, stream_id: int = -1) -> AnyIOHttp2Stream:
        if self._conn_exc:
            raise self._conn_exc
        return AnyIOHttp2Stream(self, stream_id)

    async def _send_loop(self) -> None:
        """
        Internal send loop that pulls data from the send buffer and writes it to the network stream.
        """
        try:
            _, receiver = self._send_buffer
            async with receiver:
                async for tracker in receiver:
                    # generate the data to send
                    tracker.trigger()
                    data_to_send = self._h2_core.data_to_send()
                    if data_to_send:
                        await self._net_stream.send(data_to_send)
                    # notify the tracker that the data has been sent
                    tracker.complete()
        except (SendTimeout, SendError, anyio.get_cancelled_exc_class()) as e:
            if not self._conn_exc:
                self._conn_exc = H2ConnectionError("Send error occurred", e)
            self._closed_event.set()

    async def _initialize(self) -> None:
        self._h2_core.initiate_connection()
        data_to_send = self._h2_core.data_to_send()
        if data_to_send:
            await self._net_stream.send(data_to_send)

    async def update_settings(self, settings: dict[Union[Http2SettingCode, int], int]) -> None:
        tracker = SendTracker(lambda: self._h2_core.update_settings(settings))
        await self.send(tracker)
        await tracker.result()

    async def ping(self, payload: BytesLike, ack_callback: Callable[[], Awaitable[None]]) -> None:
        payload_b = common_utils.to_bytes(payload)
        if len(payload_b) != 8:
            raise H2ProtocolError(f"Ping payload must be 8 bytes, got {len(payload_b)}")

        tracker = SendTracker(lambda: self._h2_core.ping(opaque_data=payload))
        self._ping_ack_manager.register_callback(payload_b, ack_callback)

        await self.send(tracker)
        await tracker.result()

    async def aclose(
        self,
        error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR,
        last_stream_id: Optional[int] = None,
        additional_data: Optional[BytesLike] = None,
    ) -> None:
        """
        Close the HTTP/2 connection with an optional error code and additional data.
        :param error_code: Error code to send with the connection close.
        :type error_code: Http2ErrorCode or int
        :param last_stream_id: ID of the last stream to close.
        :type last_stream_id: Optional[int]
        :param additional_data: Optional additional data to send with the close.
        :type additional_data: Optional[BytesLike]
        """
        if self._closed_event.is_set():
            # Already closed
            return

        additional_data = common_utils.to_bytes(additional_data) if additional_data else None
        tracker = SendTracker(
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
        # build a ConnectionTerminated event to notify the relevant streams
        event = h2_events.ConnectionTerminated()
        event.error_code = error_code
        event.last_stream_id = last_stream_id or 0
        event.additional_data = additional_data
        await self.handle_events([event], False)

    async def send(self, tracker: SendTracker) -> None:
        """
        Enqueue data to be sent by the connection.

        NOTE: This method should not be called by end-users directly.

        :param tracker: A send tracker containing send logic.
        :raises H2ConnectionError: If the connection is closed or an error occurs.
        """
        exc = self._conn_exc
        if exc and not (isinstance(exc, H2ConnectionTerminatedError) and exc.remote_termination):
            # Only suppress the exception if the connection was gracefully closed
            # by the remote peer using a GOAWAY frame. In all other cases—such as
            # local termination, protocol errors, or unexpected disconnects—re-raise
            # the stored connection exception.
            raise exc

        sender, _ = self._send_buffer
        await sender.send(tracker)

    async def _receive_loop(self) -> None:
        """
        Internal receive loop that pulls raw data from the network stream
        and dispatches parsed HTTP/2 events.
        """
        try:
            while True:
                data = await self._net_stream.receive()
                if data:
                    events = self._h2_core.receive_data(data)
                    await self.handle_events(events, True)
        except (ReceiveError, ReceiveTimeout, anyio.get_cancelled_exc_class()) as e:
            if not self._conn_exc:
                self._conn_exc = H2ConnectionError("Receive error occurred", e)
            self._closed_event.set()

    async def handle_events(self, events: list[h2_events.Event], need_flush: bool) -> None:
        """
        Dispatch incoming HTTP/2 events to appropriate handlers.

        :param events: A list of parsed H2 events.
        :type events: list[h2_events.Event]
        :param need_flush: Whether to flush the send buffer after handling events.
        :type need_flush: bool
        """
        for event in events:
            # Handle the connection-level event
            if isinstance(event, h2_events.PingAckReceived):
                # Handle the ping acknowledgment
                await self._ping_ack_manager.ack_ping(event.ping_data or b"")
                return

            if not self._conn_exc and isinstance(event, h2_events.ConnectionTerminated):
                self._conn_exc = H2ConnectionTerminatedError(
                    error_code=event.error_code,  # type: ignore[arg-type]
                    last_stream_id=event.last_stream_id,  # type: ignore[arg-type]
                    additional_data=event.additional_data,
                    remote_termination=True,  # Remote termination
                )

            # dispatch the event to the stream manager
            await self.stream_manager.dispatch_event(event)

        # Send a dummy tracker to send the outgoing data of `h2_core`.
        if need_flush and not self._conn_exc:
            self._tg.start_soon(self.send, dummy_tracker)

    def _streams_monitor(self, active_streams: int) -> None:
        """
        Monitor the number of active streams.

        :param active_streams: The number of active streams.
        """
        if not self._conn_exc or active_streams > 0:
            return
        # If the connection is closed and there are no active streams, set the closed event
        self._closed_event.set()

    async def wait_until_closed(self) -> None:
        """
        Wait until the connection is closed.
        """
        # Conditions for unblocking
        # 1. An abnormal network connection has occurred.
        # 2. Sent or received a GOAWAY frame, and all streams have been unregistered.
        await self._closed_event.wait()
