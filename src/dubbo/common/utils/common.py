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
from dubbo.common import constants
from dubbo.common.types import StrOrBytes

__all__ = ["to_bytes", "to_str"]


def to_bytes(data: StrOrBytes, encoding=constants.UTF_8) -> bytes:
    """Convert various data types to bytes.

    Args:
        data: Input value to convert (str, bytes, bytearray, or memoryview).
        encoding: Character encoding for string conversion. Defaults to UTF-8.

    Returns:
        Bytes representation of the input data.

    Raises:
        TypeError: If input type is not supported.

    Example:
        result = to_bytes("hello")
        result = to_bytes(bytearray(b"data"))
    """
    if isinstance(data, str):
        return data.encode(encoding)
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, memoryview):
        return data.tobytes()

    raise TypeError(f"Expected str, bytes, bytearray, or memoryview, got {type(data).__name__}")


def to_str(data: StrOrBytes, encoding=constants.UTF_8, errors="strict") -> str:
    """Convert various data types to string.

    Args:
        data: Input value to convert (str, bytes, bytearray, or memoryview).
        encoding: Character encoding for bytes decoding. Defaults to UTF-8.
        errors: Error handling strategy for decoding failures. Defaults to 'strict'.

    Returns:
        String representation of the input data.

    Raises:
        TypeError: If input type is not supported.
        UnicodeDecodeError: If decoding fails with errors='strict'.

    Example:
        result = to_str(b"hello")
        result = to_str(bytearray(b"data"), errors="ignore")
    """
    if isinstance(data, str):
        return data
    if isinstance(data, bytes):
        return data.decode(encoding, errors=errors)
    if isinstance(data, bytearray):
        return bytes(data).decode(encoding, errors=errors)
    if isinstance(data, memoryview):
        return data.tobytes().decode(encoding, errors=errors)

    raise TypeError(f"Expected str, bytes, bytearray, or memoryview, got {type(data).__name__}")
