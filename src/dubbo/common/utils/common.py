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
from dubbo.common import constant
from dubbo.common.types import StrOrBytes

__all__ = ["to_bytes"]


def to_bytes(data: StrOrBytes, encoding=constant.UTF_8) -> bytes:
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
