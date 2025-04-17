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
import functools
from contextlib import asynccontextmanager
from inspect import iscoroutinefunction
from typing import Any, Callable, Optional

import anyio
from anyio import to_thread
from anyio.abc import TaskGroup
from h2 import events as h2_events
from h2.config import H2Configuration
from h2.connection import H2Connection

from ...backend import AnyIOTCPStream, AnyIOBackend
from ..exceptions import ConnectionException
from .stream import AnyIOHttp2Stream

MAX_FRAME_SIZE = 2**24 - 1


async def _receive_loop(conn: "AnyIOHttp2Connection") -> None:
    while True:
        data = await conn.net_stream.receive(MAX_FRAME_SIZE)
        if not data:
            break
        event_list = conn.h2_state.receive_data(data)
        for event in event_list:
            await conn.handle_event(event)


class AnyIOHttp2Connection(abc.ABC):
    """
    This class represents an HTTP/2 connection using the AnyIO backend.
    It is responsible for managing the connection state and handling
    communication over the network stream.
    """

    _tg: TaskGroup
    _net_stream: Optional[AnyIOTCPStream]
    _h2_state: H2Connection
    _send_lock: anyio.Lock

    _ping_callbacks: dict[bytes, Callable]

    _streams: dict[int, AnyIOHttp2Stream]
    _highest_allowable_stream_id: int
    _allow_create_stream: bool

    def __init__(self, *args, **kwargs):
        """
        Initialize the AnyIOHttp2Connection.
        """
        self._net_stream = None
        self._tg = anyio.create_task_group()
        self._h2_state = H2Connection(H2Configuration(client_side=True))
        self._send_lock = anyio.Lock()
        self._streams = {}
        self._highest_allowable_stream_id = self._h2_state.HIGHEST_ALLOWED_STREAM_ID
        self._allow_create_stream = True

    @property
    def net_stream(self):
        """
        Get the network stream.
        """
        return self._net_stream

    @property
    def h2_state(self):
        """
        Get the H2 connection state.
        """
        return self._h2_state

    def register_stream(self, stream) -> None:
        """
        Register a new stream with the connection.
        """
        if not self._allow_create_stream:
            raise ConnectionException("Cannot create new streams after connection termination.")
        if stream.id < 0:
            stream.id = self._h2_state.get_next_available_stream_id()

        self._streams[stream.id] = stream

    async def unregister_stream(self, stream) -> None:
        """
        Unregister a stream from the connection.
        """
        if stream.id in self._streams:
            del self._streams[stream.id]
            await self._try_close()

    async def ping(self, ping_data: bytes, ack_callback: Optional[Callable] = None) -> None:
        """
        Send a ping frame to the server.
        """
        await self.send(self._h2_state.ping, ping_data)
        if ack_callback:
            self._ping_callbacks[ping_data] = ack_callback

    async def close(
        self, error_code: int = 0, additional_data: Optional[bytes] = None, last_stream_id: Optional[int] = None
    ) -> None:
        """
        Send a GOAWAY frame to the server.
        :param error_code: The error code for the GOAWAY frame.
        :param additional_data: Optional additional data to include in the GOAWAY frame.
        :param last_stream_id: The last stream ID to include in the GOAWAY frame.
        """
        # Send GOAWAY frame
        await self.send(self._h2_state.close_connection, error_code, additional_data, last_stream_id)
        # Handle the GOAWAY frame
        await self._handle_goaway(error_code, additional_data, last_stream_id)

    async def send(self, fn, *args, **kwargs):
        async with self._send_lock:
            fn(*args, **kwargs)
            data_to_send = self._h2_state.data_to_send()
            if data_to_send:
                await self._net_stream.send(data_to_send)

    async def handle_event(self, event: h2_events.Event):
        """
        handle incoming events from the H2 connection.
        """
        stream_id = getattr(event, "stream_id", 0)
        # Stream level events
        if stream_id in self._streams:
            stream = self._streams[stream_id]
            await stream.handle_event(event)
        else:
            # Connection level events
            if isinstance(event, h2_events.PingAckReceived):
                await self._handle_ping_ack(event.ping_data)
            elif isinstance(event, h2_events.WindowUpdated):
                await self._handle_window_update(event)
            elif isinstance(event, h2_events.ConnectionTerminated):
                await self._handle_goaway(event.error_code, event.additional_data, event.last_stream_id)

        # Some events have been handled by the HTTP/2 state machine,
        # so we need to call the send method to send any pending data.
        await self.send(lambda *args, **kwargs: None)

    async def _handle_ping_ack(self, event: h2_events.PingAckReceived) -> None:
        """
        Handle ping ack event. (PING Frame)
        """
        ping_data = event.ping_data
        if ping_data in self._ping_callbacks:
            callback = self._ping_callbacks.pop(ping_data)
            await callback(event)

    async def _handle_window_update(self, event: h2_events.WindowUpdated) -> None:
        """
        Handle connection window update event. (WINDOW_UPDATE Frame)
        """
        for stream in self._streams.values():
            await stream.handle_event(event)

    async def _handle_goaway(
        self, error_code: int = 0, additional_data: Optional[bytes] = None, last_stream_id: Optional[int] = None
    ):
        """
        Handle connection terminated event.
        Applicable for sending and receiving GOAWAY frames.
        :param error_code: The error code for the GOAWAY frame.
        """
        # Build ConnectionTerminated event
        event = h2_events.ConnectionTerminated()
        event.error_code = error_code
        event.additional_data = additional_data
        event.last_stream_id = last_stream_id

        # Disable stream creation
        self._allow_create_stream = False
        self._highest_allowable_stream_id = event.last_stream_id

        # Spread the event
        remain_streams = {}
        for stream_id, stream in self._streams.items():
            if stream_id > event.last_stream_id:
                await stream.handle_event(event)
            else:
                remain_streams[stream_id] = stream

        self._streams = remain_streams

    async def _try_close(self) -> None:
        """
        Attempt to close the connection gracefully.
        """
        if not self._allow_create_stream and len(self._streams) == 0:
            self.h2_state.clear_outbound_data_buffer()
            await self._net_stream.aclose()


class AnyIOHttp2Client(AnyIOHttp2Connection):
    def create_stream(self) -> Any:
        if not self._allow_create_stream:
            raise ConnectionException("Cannot create new streams after connection termination.")
        return AnyIOHttp2Stream(self)

    async def connect(self, *args, **kwargs) -> None:
        backend = AnyIOBackend()
        self._net_stream = await backend.connect_tcp(*args, **kwargs)
        self._tg = anyio.create_task_group()


class AnyIOHttp2Server(AnyIOHttp2Connection):
    def __init__(self, callback):
        super().__init__()

        if iscoroutinefunction(callback):
            self._stream_callback = callback
        else:

            async def _call_back_wrapper(_callback, _stream, _conn):
                await to_thread.run_sync(_callback, _stream, _conn)

            self._stream_callback = functools.partial(_call_back_wrapper, callback)

    async def handle_event(self, event: h2_events.Event):
        if isinstance(event, h2_events.RequestReceived):
            stream = AnyIOHttp2Stream(self, event.stream_id)
            self.register_stream(stream)
            # TODO: Handle stream creation
            self._tg.start_soon(self._stream_callback, stream, self)

        # Handle other events
        await super().handle_event(event)
