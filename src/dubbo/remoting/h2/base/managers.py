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
from typing import Generic, Optional, TypeVar

from h2 import events as h2_events

from ..exceptions import H2ConnectionError

__all__ = ["CallbackManager", "StreamManager"]

_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")
_CallbackT = TypeVar("_CallbackT")


class CallbackManager(abc.ABC, Generic[_KeyT, _ValueT, _CallbackT]):
    """Base class for managing callbacks associated with keys."""

    @abc.abstractmethod
    def register_callback(self, key: _KeyT, callback: _CallbackT) -> None:
        """Register a callback for a specific key."""
        raise NotImplementedError()

    @abc.abstractmethod
    def unregister_callback(self, key: _KeyT, callback: _CallbackT) -> None:
        """Unregister a callback for a specific key."""
        raise NotImplementedError()

    @abc.abstractmethod
    def dispatch_one(self, key: _KeyT, value: _ValueT) -> None:
        """Dispatch a single value to the callback associated with the key."""
        raise NotImplementedError()

    @abc.abstractmethod
    def close(self) -> None:
        """Close the callback manager and clean up resources."""
        raise NotImplementedError()


_StreamT = TypeVar("_StreamT")


class StreamManager(abc.ABC, Generic[_StreamT]):
    """Base class for managing HTTP/2 streams."""

    @property
    @abc.abstractmethod
    def num_active_streams(self) -> int:
        """Get the count of active streams."""
        raise NotImplementedError()

    @abc.abstractmethod
    def register_stream(self, stream: _StreamT) -> None:
        """Register a new stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def unregister_stream(self, stream: _StreamT) -> None:
        """Unregister a stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def get_stream(self, stream_id: int) -> Optional[_StreamT]:
        """Get a stream by its ID."""
        raise NotImplementedError()

    @abc.abstractmethod
    def dispatch_event(self, event: h2_events.Event) -> None:
        """Dispatch an H2 event to the appropriate stream."""
        raise NotImplementedError()

    @abc.abstractmethod
    def dispatch_connection_error(self, error: H2ConnectionError) -> None:
        """Dispatch a connection error to all streams."""
        raise NotImplementedError()
