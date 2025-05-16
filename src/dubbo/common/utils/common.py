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
    """
    Convert various data types to bytes.

    :param data: Input value to convert to bytes
    :type data: str or bytes or bytearray or memoryview
    :param encoding: Encoding to use for string conversion (default: UTF-8)
    :type encoding: str
    :returns: Bytes representation of the input
    :rtype: bytes
    :raises TypeError: If input is not str, bytes, bytearray, or memoryview

    Converts the input data to bytes using the following rules:

    * str: encoded using specified encoding
    * bytes: returned as-is
    * bytearray: converted to bytes
    * memoryview: converted to bytes
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
    """
    Convert various data types to string.

    :param data: Input value to convert to string
    :type data: str or bytes or bytearray or memoryview
    :param encoding: Encoding to use for bytes conversion (default: UTF-8)
    :type encoding: str
    :param errors: How to handle encoding errors (default: 'strict')
                   Other options include 'ignore', 'replace', etc.
    :type errors: str
    :returns: String representation of the input
    :rtype: str
    :raises TypeError: If input is not str, bytes, bytearray, or memoryview
    :raises UnicodeDecodeError: If decoding fails and errors='strict'

    Converts the input data to string using the following rules:

    * str: returned as-is
    * bytes, bytearray, memoryview: decoded using specified encoding
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
