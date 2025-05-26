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
from collections.abc import AsyncIterator, Collection, Mapping
from contextlib import asynccontextmanager
from http import HTTPMethod
from typing import Any, Generic, Optional, TypeVar, Union

from dubbo.common import URL, constants
from dubbo.protocol.exceptions import EndOfStream, TripleError
from dubbo.protocol.triple.grpc import utils as grpc_utils
from dubbo.protocol.triple.metadata import TriMetadataKeys
from dubbo.remoting.h2 import AsyncHttp2Stream, Http2ErrorCode, PseudoHeaderName
from dubbo.remoting.h2.exceptions import H2StreamClosedError, H2StreamResetError

_SendType = TypeVar("_SendType")
_RecvType = TypeVar("_RecvType")
_MetadataLike = Union[Mapping[str, Any], Collection[tuple[str, Any]]]


class AsyncTripleClientStream(AsyncIterator[_RecvType], Generic[_SendType, _RecvType]):
    _url: URL
    _stream: AsyncHttp2Stream

    # metadata to send
    _metadata: _MetadataLike
    _timeout: Optional[float]

    # some flags
    _init_metadata_sent: bool

    def __init__(
        self,
        url: URL,
        stream: AsyncHttp2Stream,
        metadata: Optional[_MetadataLike] = None,
        timeout: Optional[float] = None,
    ):
        self._url = url
        self._stream = stream

        self._metadata = metadata or []
        self._timeout = timeout

        # some flags
        exc = TripleError("Request was not started, please call `start_request()` first")
        self._local_exc: Optional[Exception] = exc
        self._remote_exc: Optional[Exception] = exc
        self._recv_message_exc: Optional[Exception] = None

    async def start_request(
        self,
        end_stream: bool = False,
    ) -> None:
        """
        Start a new request on the stream.

        """
        if self._init_metadata_sent:
            # raise error if metadata already sent
            raise TripleError("request already started, cannot start again")

        # Set the request headers
        headers: list[tuple[str, str]] = [
            (PseudoHeaderName.METHOD, HTTPMethod.POST),
            (PseudoHeaderName.PATH, self._url.path),
            (PseudoHeaderName.SCHEME, self._url.protocol),
            (PseudoHeaderName.AUTHORITY, self._url.host),
            ("te", "trailers"),
            ("user-agent", constants.USER_AGENT),
        ]

        # TODO: Add timeout and content type to headers
        if self._timeout:
            headers.append((TriMetadataKeys.GRPC_TIMEOUT, grpc_utils.encode_timeout(self._timeout)))

        # Add metadata to headers
        headers.extend(grpc_utils.encode_custom_metadata(self._metadata))

        # Send the headers
        async with self._handle_exceptions():
            await self._stream.send_headers(headers=headers, end_stream=end_stream)

        # update the flags
        self._init_metadata_sent = True
        self._local_exc = self._remote_exc = None

    async def send_message(self, message: _SendType, end_stream: bool = False) -> None:
        """
        Send a message on the stream.
        """
        if not self._init_metadata_sent:
            await self.start_request()

        if self._local_exc:
            raise self._local_exc

        # TODO: 1.Serialize the message
        serialized_message = message

        # TODO: 2.Compress the message if needed
        compressed_message = serialized_message

        # TODO: 3.Encode the gRPC frame
        frame = grpc_utils.encode_frame(compressed_message, compressed=False)  # type: ignore[arg-type]

        # Send the message frame
        async with self._handle_exceptions():
            await self._stream.send_data(frame, end_stream=end_stream)

    async def end_request(self) -> None:
        """
        End the request on the stream.
        This method should be called when the request is complete and no more messages will be sent.
        """
        if self._local_exc:
            raise self._local_exc
        # Send an empty message to indicate the end of the stream
        async with self._handle_exceptions():
            await self._stream.send_data(b"", end_stream=True)

    async def cancel(self) -> None:
        """
        Send a RESET_STREAM frame to cancel the stream.
        """
        if self._local_exc and self._remote_exc:
            # If the stream is already closed
            raise self._local_exc

        # Send a RESET_STREAM frame to cancel the stream
        async with self._handle_exceptions():
            await self._stream.reset(error_code=Http2ErrorCode.CANCEL)

    async def receive_initial_metadata(self) -> _MetadataLike:
        """
        Receive the initial metadata from the stream.
        This method should be called to get the initial metadata after the request is started.
        """
        if self._remote_exc:
            raise self._remote_exc

        # Receive the initial metadata
        async with self._handle_exceptions():
            headers = await self._stream.receive_headers()
            # TODO parse the headers to get the metadata

        return headers

    async def receive_message(self) -> _RecvType:
        """
        Receive a message from the stream.
        This method should be called to get the next message in the stream.
        """
        if self._remote_exc:
            raise self._remote_exc

        # Receive the message
        try:
            frame_header = await self._stream.receive_data(max_bytes=5, strict=True)
            compressed_flag, message_length = grpc_utils.parse_frame_header(frame_header)

            # Receive the message body
            message_body = await self._stream.receive_data(max_bytes=message_length, strict=True)

            # decompress the message if needed
            if compressed_flag:
                raise NotImplementedError("Compression not implemented")

            # TODO: deserialize the message

            return message_body  # type: ignore[return-value]
        except (H2StreamClosedError, H2StreamResetError):
            # TODO: handle the error

            raise EndOfStream()

    async def receive_trailing_metadata(self) -> _MetadataLike:
        """
        Receive the trailing metadata from the stream.
        This method should be called to get the trailing metadata after the request is completed.
        """
        if self._remote_exc:
            raise self._remote_exc

        # Receive the trailing metadata
        async with self._handle_exceptions():
            trailers = await self._stream.receive_trailers()
            # TODO parse the trailers to get the metadata

        return trailers

    @asynccontextmanager
    async def _handle_exceptions(self):
        """
        Handle exceptions that may occur during the stream operations.
        This context manager should be used to wrap the stream operations.
        """
        try:
            yield
        except Exception as e:
            if isinstance(e, TripleError):
                raise e
            elif isinstance(e, H2StreamClosedError):
                exc = TripleError(
                    f"Stream closed. Local side: {e.local_side}, Remote side: {e.remote_side}. "
                    f"Attempted operation on a closed stream."
                )
                self._local_exc = exc if e.local_side else self._local_exc
                self._remote_exc = exc if e.remote_side else self._remote_exc
                raise exc
            elif isinstance(e, H2StreamResetError):
                reset_side = "local" if not e.remote_reset else "remote"
                exc = TripleError(f"Stream reset by {reset_side}. Cannot proceed with operations on a reset stream.")
                self._local_exc = self._remote_exc = exc
                raise exc
            else:
                raise TripleError(e) from e

    def __aiter__(self):
        return self

    async def __anext__(self) -> _RecvType:
        """
        Receive the next message from the stream.
        This method should be called to get the next message in the stream.
        """
        try:
            return await self.receive_message()
        except EndOfStream:
            raise StopAsyncIteration()
