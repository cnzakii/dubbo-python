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
import enum

__all__ = ["PseudoHeaderName", "Http2ErrorCode", "Http2SettingCode"]


class PseudoHeaderName(enum.StrEnum):
    """
    Pseudo header names for HTTP/2.
    See: https://datatracker.ietf.org/doc/html/rfc7540#section-8.1.2.1

    """

    # ":method" - includes the HTTP method
    METHOD = ":method"

    # ":scheme" - includes the scheme portion of the target URI
    SCHEME = ":scheme"

    # ":authority" - includes the authority portion of the target URI
    AUTHORITY = ":authority"

    # ":path" - includes the path and query parts of the target URI
    PATH = ":path"

    # ":status" - includes the response status code
    STATUS = ":status"


class Http2ErrorCode(enum.IntEnum):
    """
    Error codes are 32-bit fields that are used in RST_STREAM and GOAWAY frames
    to convey the reasons for the stream or connection error.

    see: https://datatracker.ietf.org/doc/html/rfc7540#section-11.4
    """

    # The associated condition is not a result of an error.
    NO_ERROR = 0x0

    # The endpoint detected an unspecific protocol error.
    PROTOCOL_ERROR = 0x1

    # The endpoint encountered an unexpected internal error.
    INTERNAL_ERROR = 0x2

    # The endpoint detected that its peer violated the flow-control protocol.
    FLOW_CONTROL_ERROR = 0x3

    # The endpoint sent a SETTINGS frame but did not receive a response in a timely manner.
    SETTINGS_TIMEOUT = 0x4

    # The endpoint received a frame after a stream was half-closed.
    STREAM_CLOSED = 0x5

    # The endpoint received a frame with an invalid size.
    FRAME_SIZE_ERROR = 0x6

    # The endpoint refused the stream prior to performing any application processing
    REFUSED_STREAM = 0x7

    # Used by the endpoint to indicate that the stream is no longer needed.
    CANCEL = 0x8

    # The endpoint is unable to maintain the header compression context for the connection.
    COMPRESSION_ERROR = 0x9

    # The connection established in response to a CONNECT request (Section 8.3) was reset or abnormally closed.
    CONNECT_ERROR = 0xA

    # The endpoint detected that its peer is exhibiting a behavior that might be generating excessive load.
    ENHANCE_YOUR_CALM = 0xB

    # The underlying transport has properties that do not meet minimum security requirements (see Section 9.2).
    INADEQUATE_SECURITY = 0xC

    # The endpoint requires that HTTP/1.1 be used instead of HTTP/2.
    HTTP_1_1_REQUIRED = 0xD


class Http2SettingCode(enum.IntEnum):
    """
    The settings are used to communicate configuration parameters that affect how endpoints communicate.
    See: https://datatracker.ietf.org/doc/html/rfc7540#section-11.3
    """

    # Allows the sender to inform the remote endpoint of the maximum size
    # of the header compression table used to decode header blocks, in octets.
    HEADER_TABLE_SIZE = 0x1

    # This setting can be used to disable server push (Section 8.2).
    ENABLE_PUSH = 0x2

    # Indicates the maximum number of concurrent streams that the sender will allow.
    MAX_CONCURRENT_STREAMS = 0x3

    # Indicates the sender's initial window size (in octets) for stream-level flow control.
    # This setting affects the window size of all streams
    INITIAL_WINDOW_SIZE = 0x4

    # Indicates the size of the largest frame payload that the sender is willing to receive, in octets.
    MAX_FRAME_SIZE = 0x5

    # This advisory setting informs a peer of the maximum size of header list
    # that the sender is prepared to accept, in octets.
    MAX_HEADER_LIST_SIZE = 0x6
