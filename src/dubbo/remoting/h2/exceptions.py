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


class Http2Exception(Exception):
    """
    Base class for all HTTP/2 exceptions.
    """

    pass


# Connection exceptions
class ConnectionException(Http2Exception):
    """
    Base class for all HTTP/2 connection exceptions.
    """

    pass


# Stream exceptions
class StreamException(Http2Exception):
    """
    Base class for all HTTP/2 stream exceptions.
    """

    def __init__(self, stream_id: int, err_msg: str):
        self.stream_id = stream_id
        self.err_msg = err_msg

    def __str__(self):
        return f"Stream ID {self.stream_id}: {self.err_msg}"


class StreamClosedException(StreamException):
    """
    Raised when an operation is attempted on a closed stream.
    """

    def __init__(self, stream_id: int, local_side: bool):
        self.local_side = local_side
        super().__init__(stream_id, f"Stream closed on {'local' if local_side else 'remote'} side")


class StreamResetException(StreamException):
    """
    Raised when a stream is reset.
    """

    def __init__(self, stream_id: int, error_code: int, local_side: bool):
        self.error_code = error_code
        self.local_side = local_side
        super().__init__(
            stream_id, f"Stream reset with error code {error_code} on {'local' if local_side else 'remote'} side"
        )
