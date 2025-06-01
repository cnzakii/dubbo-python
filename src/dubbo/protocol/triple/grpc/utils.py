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
import base64
import re
import struct
from collections import OrderedDict
from collections.abc import Collection, Mapping
from typing import Any, Union
from urllib import parse as url_parse

from dubbo.common import constants
from dubbo.common.types import BytesLike
from dubbo.common.utils import common as common_utils

__all__ = [
    "encode_timeout",
    "decode_timeout",
    "encode_bin_value",
    "decode_bin_value",
    "encode_grpc_message",
    "decode_grpc_message",
    "encode_custom_metadata",
    "encode_frame",
    "parse_frame_header",
]


# Time unit mapping
_TIME_UNITS = OrderedDict(
    [
        ("n", 1),  # nanoseconds
        ("u", 1_000),  # microseconds
        ("m", 1_000_000),  # milliseconds
        ("S", 1_000_000_000),  # seconds
        ("M", 60 * 1_000_000_000),  # minutes
        ("H", 3600 * 1_000_000_000),  # hours
    ]
)

# Precompiled regex pattern to match valid timeout strings (e.g., '100m', '2S')
_TIMEOUT_RE = re.compile(r"^(\d+)([numSMH])$")


def encode_timeout(timeout_seconds: Union[int, float]) -> str:
    """Encodes a timeout duration (in seconds) into an ASCII string with a time unit suffix.

    Args:
        timeout_seconds: Timeout duration in seconds (must be >= 0).

    Returns:
        str: Encoded ASCII string (e.g., "100m", "2S", "5H").

    Raises:
        ValueError: If the timeout is too small or too large.
    """
    if timeout_seconds < 0:
        raise ValueError("Timeout too small")

    timeout_nanos = int(timeout_seconds * 1_000_000_000)
    cutoff = 100_000_000

    for suffix, factor in _TIME_UNITS.items():
        timeout = timeout_nanos // factor
        if timeout < cutoff:
            return f"{timeout}{suffix}"

    raise ValueError("Timeout too large")


def decode_timeout(timeout_str: str) -> float:
    """Decodes an ASCII timeout string into a float duration in seconds.

    Args:
        timeout_str: ASCII timeout string with a suffix (e.g., "100m", "2S").

    Returns:
        float: Timeout duration in seconds.

    Raises:
        ValueError: If the input string is empty, too long, or uses an invalid suffix.
    """
    match = _TIMEOUT_RE.match(timeout_str)
    if match is None:
        raise ValueError(f"Invalid timeout string: {timeout_str}")

    value_str, suffix = match.groups()
    if suffix not in _TIME_UNITS:
        raise ValueError(f"Invalid suffix in timeout string: {timeout_str}")

    nanos = int(value_str) * _TIME_UNITS[suffix]
    # Convert back to seconds
    return nanos / 1_000_000_000


def encode_bin_value(data: BytesLike) -> str:
    """Encodes binary data to base64 string without padding for gRPC headers.

    Padding (`=`) is stripped as per gRPC requirements for binary headers.

    Args:
        data: The input data to encode.

    Returns:
        str: Base64-encoded binary string without padding, as a regular string.

    Raises:
        TypeError: If the input cannot be converted to bytes.
    """
    data_b = common_utils.to_bytes(data)
    # Perform base64 encoding and return as an ASCII string
    return base64.b64encode(data_b).rstrip(b"=").decode(constants.US_ASCII)


def decode_bin_value(encoded_data: str) -> bytes:
    """Decodes a gRPC `-bin` header value from base64-encoded string to raw bytes.

    Automatically restores padding if needed before decoding.

    Args:
        encoded_data: Base64-encoded string representing binary data (without padding).

    Returns:
        bytes: Decoded binary data.

    Raises:
        binascii.Error: If the input is not a valid base64-encoded string.
    """
    # Add padding back if it's missing
    missing_padding = len(encoded_data) % 4
    if missing_padding:
        encoded_data += "=" * (4 - missing_padding)
    # Perform base64 decoding on the string input
    return base64.b64decode(encoded_data)


def encode_grpc_message(message: str) -> str:
    """Percent-encodes a message string for use in the `grpc-message` header.

    Uses URL encoding to ensure compliance with the gRPC protocol specification.

    Args:
        message: The original message string to encode.

    Returns:
        str: The percent-encoded message string safe for use in `grpc-message`.
    """
    return url_parse.quote(message, encoding=constants.UTF_8)


def decode_grpc_message(value: str) -> str:
    """Decodes a percent-encoded `grpc-message` string back to its original form.

    Reverses URL encoding, converting percent-encoded sequences into Unicode characters.

    Args:
        value: The encoded `grpc-message` string.

    Returns:
        str: The decoded original message string.
    """
    return url_parse.unquote(value, encoding=constants.UTF_8)


_PRESERVE_KEYS = [
    "content-type",
    "user-agent",
    "te",
]


def encode_custom_metadata(
    metadata: Union[Mapping[str, Any], Collection[tuple[str, Any]]], lowercase_keys: bool = True
) -> list[tuple[str, str]]:
    """Encodes custom metadata for gRPC headers.

    Ensures metadata keys and values are safe for use in gRPC headers,
    handling both binary and non-binary metadata.

    Args:
        metadata: The metadata dictionary or collection of key-value pairs to encode.
        lowercase_keys: Whether to convert metadata keys to lowercase (recommended by gRPC spec).

    Returns:
        list[tuple[str, str]]: The encoded metadata as a list of key-value tuples.

    Raises:
        KeyError: If a key is invalid (starts with ':' or 'grpc-' or is in _PRESERVE_KEYS).
        TypeError: If a binary value is not of type bytes.
    """
    if isinstance(metadata, Mapping):
        metadata = metadata.items()

    encoded_metadata = []
    for item in metadata:
        k = item[0].lower() if lowercase_keys else item[0]
        v = item[1]
        if k.startswith(":") or k.startswith("grpc-") or k in _PRESERVE_KEYS:
            raise KeyError(f"Invalid metadata key: {k}")
        elif k.endswith("-bin"):
            if not isinstance(v, bytes):
                raise TypeError(f"Invalid metadata value type, bytes expected: {v}")
            encoded_metadata.append((k, encode_bin_value(v)))
        else:
            encoded_metadata.append((k, str(v)))
    return encoded_metadata


def encode_frame(message_bytes: bytes, compressed: bool = False) -> bytes:
    """Encodes a gRPC message frame with a 5-byte header followed by the payload.

    The gRPC frame header format (5 bytes total):
        - 1 byte: compressed flag (0 = uncompressed, 1 = compressed)
        - 4 bytes: big-endian unsigned int representing the length of the message payload

    Args:
        message_bytes: The message payload as bytes.
        compressed: Whether the message payload is compressed. Defaults to False.

    Returns:
        bytes: The encoded gRPC frame consisting of the header and the message payload.

    Raises:
        TypeError: If message_bytes is not of type bytes.
    """
    if not isinstance(message_bytes, bytes):
        raise TypeError("message_bytes must be of type bytes")

    compressed_flag = 1 if compressed else 0
    length = len(message_bytes)

    header = struct.pack(">BI", compressed_flag, length)
    return header + message_bytes


def parse_frame_header(frame_header: bytes) -> tuple[bool, int]:
    """Parses the 5-byte gRPC frame header into its components.

    Args:
        frame_header: Exactly 5 bytes representing the frame header.

    Returns:
        tuple[bool, int]: A tuple containing:
            - compressed_flag: bool indicating if the payload is compressed.
            - message_length: int length of the message payload.

    Raises:
        ValueError: If frame_header is not exactly 5 bytes or if the compressed flag is invalid.
    """
    if len(frame_header) != 5:
        raise ValueError("Frame header must be exactly 5 bytes")

    compressed_flag, message_length = struct.unpack(">BI", frame_header)
    if compressed_flag not in (0, 1):
        raise ValueError(f"Invalid compressed flag value: {compressed_flag}, expected 0 or 1")

    return bool(compressed_flag), message_length
