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

from dubbo.common.types import BytesLike

__all__ = ["MessageEncoding", "Compressor", "Decompressor"]


class MessageEncoding(abc.ABC):
    """Base interface for message encoding identification.

    Defines the contract for objects that need to identify
    their encoding type (compression algorithm).
    """

    @classmethod
    @abc.abstractmethod
    def encoding(cls) -> str:
        """Get the encoding identifier for this compression type.

        Returns:
            A string identifying the encoding algorithm.
        """
        raise NotImplementedError()


class Compressor(MessageEncoding, abc.ABC):
    """Interface for data compression operations.

    Implementers must provide a method to compress data and
    identify the encoding algorithm used.
    """

    @abc.abstractmethod
    def compress(self, data: BytesLike) -> bytes:
        """Compress the provided data.

        Args:
            data: Raw data to compress (bytes, bytearray, or memoryview).

        Returns:
            Compressed data as bytes.
        """
        raise NotImplementedError()


class Decompressor(MessageEncoding, abc.ABC):
    """Interface for data decompression operations.

    Implementers must provide a method to decompress data and
    identify the encoding algorithm used.
    """

    @abc.abstractmethod
    def decompress(self, data: BytesLike) -> bytes:
        """Decompress the provided data.

        Args:
            data: Compressed data to decompress (bytes, bytearray, or memoryview).

        Returns:
            Original uncompressed data as bytes.
        """
        raise NotImplementedError()
