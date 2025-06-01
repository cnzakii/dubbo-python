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
from typing import Callable, Optional, TypeVar, Union

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from h2 import events as h2_events, exceptions as h2_exceptions
from h2.config import H2Configuration

from dubbo.remoting.backend import AsyncNetworkStream
from dubbo.remoting.backend.exceptions import ReceiveError, SendError
from dubbo.remoting.h2 import (
    AsyncHttp2StreamHandlerType,
    Http2ErrorCode,
)
from dubbo.remoting.h2.exceptions import (
    H2ConnectionError,
)

from .._connection import BaseHttp2Connection  # type: ignore[misc]
from .._tracker import AsyncSendTracker, dummy_tracker  # type: ignore[misc]

__all__ = ["AnyIOHttp2Connection"]

_EventT = TypeVar("_EventT", bound=h2_events.Event)
_EventDispatcher = dict[type[_EventT], Callable[[_EventT], Awaitable[None]]]


class AnyIOHttp2Connection(BaseHttp2Connection):
    """Asynchronous HTTP/2 connection handler based on AnyIO.

    This class implements the HTTP/2 protocol (RFC 7540) using anyio for asynchronous I/O.
    It handles HTTP/2 framing, data sending/receiving, ping management, settings negotiation,
    and stream lifecycle management in a fully async fashion.

    The connection maintains separate task groups for sending and receiving data,
    and properly handles protocol events like SETTINGS, PING, and GOAWAY frames.
    """

    __slots__ = (
        "_tg",
        "_h2_core",
        "_stream_manager",
        "_ping_manager",
        "_settings_manager",
        "_send_buffer",
        "_event_dispatcher",
        "_closed_event",
        "_conn_exc",
        "_net_stream",
        "_stack",
    )

    _net_stream: AsyncNetworkStream
    _stack: AsyncExitStack

    # send/receive buffer
    _send_buffer: tuple[MemoryObjectSendStream[AsyncSendTracker], MemoryObjectReceiveStream[AsyncSendTracker]]

    def __init__(
        self,
        net_stream: AsyncNetworkStream,
        h2_config: H2Configuration,
        stream_handler: Optional[AsyncHttp2StreamHandlerType] = None,
    ) -> None:
        super().__init__(task_group=anyio.create_task_group(), h2_config=h2_config, stream_handler=stream_handler)
        self._net_stream = net_stream
        self._stack = AsyncExitStack()

        # Create a zero-buffer stream for precise backpressure control
        sender, receiver = anyio.create_memory_object_stream[AsyncSendTracker](max_buffer_size=0)
        self._send_buffer = (sender, receiver)

    async def __aenter__(self):
        """Enter the connection context and initialize the connection.

        Returns:
            The connection instance.
        """
        await self._stack.__aenter__()
        # Register TaskGroup and initialize HTTP/2 connection
        await self._stack.enter_async_context(self._tg)
        self._stack.callback(self._tg.cancel_scope.cancel)
        await self._initialize()

        # Start background tasks
        self._tg.start_soon(self._send_loop)  # type: ignore[no-untyped-call]
        self._tg.start_soon(self._receive_loop)  # type: ignore[no-untyped-call]

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the connection context and clean up resources."""
        # Attempt graceful closure if not already closed
        if not self._closed_event.is_set():
            try:
                await self.aclose()
            except Exception:
                # Ignore errors during graceful closure in exit context
                pass
            finally:
                self._finalize(exc=exc_val)

        # Clean up network stream
        try:
            await self._net_stream.aclose()
        except Exception:
            # Ignore network cleanup errors
            pass

        # Ensure connection is marked as closed
        await self._ping_manager.aclose()
        await self._settings_manager.aclose()
        await self._stream_manager.aclose()

        # Let parent handle the rest
        return await self._stack.__aexit__(exc_type, exc_val, exc_tb)

    async def _initialize(self) -> None:
        """Initialize HTTP/2 connection protocol handshake.

        Sends the connection preface and initial SETTINGS frame as required
        by RFC 7540. This establishes the HTTP/2 protocol session.

        Raises:
            H2ConnectionError: If protocol initialization fails or network error occurs.
        """
        try:
            self._h2_core.initiate_connection()
            data_to_send = self._h2_core.data_to_send()
            if data_to_send:
                await self._net_stream.send(data_to_send)
        except Exception as e:
            raise H2ConnectionError(f"Connection initialization failed: {e}") from e

    # ----------- send/receive loops -----------

    async def _send_loop(self) -> None:
        """Internal send loop that pulls data from the send buffer and writes it to the network stream.

        This is a background task that continuously processes outgoing data requests.
        """
        tracker: Optional[AsyncSendTracker] = None
        raw_exc: Optional[Exception] = None
        try:
            receiver = self._send_buffer[1]
            async with receiver:
                async for next_tracker in receiver:
                    tracker = next_tracker
                    # Generate the data to send
                    tracker.trigger()
                    data_to_send = self._h2_core.data_to_send()
                    if data_to_send:
                        await self._net_stream.send(data_to_send)
                    # Notify the tracker that the data has been sent
                    tracker.complete()
        except Exception as e:
            if isinstance(e, SendError):
                exc = H2ConnectionError(f"Failed to send data over network stream (SendError): {e}", e)
            elif isinstance(e, anyio.get_cancelled_exc_class()):
                exc = H2ConnectionError("Send loop was cancelled (likely during shutdown)", e)
            elif isinstance(e, (anyio.BrokenResourceError, anyio.ClosedResourceError)):
                exc = H2ConnectionError("Send loop encountered a broken or closed resource", e)
            else:
                exc = H2ConnectionError(f"Unexpected error in send loop: {type(e).__name__}: {e}", e)

            if tracker:
                # If we have a tracker, mark it as complete with the exception
                tracker.complete(exc)

            if exc and not self._conn_exc:
                # Only set the connection exception if it hasn't been set yet
                self._conn_exc = exc
        finally:
            self._finalize(exc=raw_exc)

    async def _receive_loop(self) -> None:
        """Internal receive loop that pulls raw data from the network stream
        and dispatches parsed HTTP/2 events.

        This is a background task that continuously reads from the network connection.
        """
        raw_exc: Optional[Exception] = None
        try:
            while True:
                data = await self._net_stream.receive()
                try:
                    events = self._h2_core.receive_data(data)
                    if events:
                        await self.handle_events(events)
                except h2_exceptions.ProtocolError:
                    # terminate the connection on protocol errors
                    await self.send(dummy_tracker)
                    raise  # re-raise

        except Exception as e:
            raw_exc = e
            if isinstance(e, ReceiveError):
                exc = H2ConnectionError(f"Failed to receive data from network stream (ReceiveError): {e}", e)
            elif isinstance(e, anyio.get_cancelled_exc_class()):
                exc = H2ConnectionError("Receive loop was cancelled (likely during shutdown)", e)
            elif isinstance(e, h2_exceptions.ProtocolError):
                exc = H2ConnectionError(f"Protocol error in receive loop: {type(e).__name__}: {e}", e)
            else:
                exc = H2ConnectionError(f"Unexpected error in receive loop: {type(e).__name__}: {e}", e)

            if not self._conn_exc:
                # Only set the connection exception if it hasn't been set yet
                self._conn_exc = exc
        finally:
            self._finalize(exc=raw_exc)

    def _finalize(
        self, error_code: Union[Http2ErrorCode, int] = Http2ErrorCode.NO_ERROR, exc: Optional[Exception] = None
    ) -> None:
        super()._finalize(error_code=error_code, exc=exc)

        # close the send buffer to signal no more data will be sent
        sender, receiver = self._send_buffer
        sender.close()
        receiver.close()

    async def _do_send(self, tracker: AsyncSendTracker) -> None:
        try:
            await self._send_buffer[0].send(tracker)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError) as e:
            # If the send buffer is closed or broken, we should not raise an error
            # but rather handle it gracefully.
            tracker.complete(H2ConnectionError(f"Send buffer is closed or broken: {e}", e))
