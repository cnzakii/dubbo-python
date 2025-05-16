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
    """
    The message encoding interface.
    """

    @classmethod
    @abc.abstractmethod
    def encoding(cls) -> str:
        """
        Get message encoding of current compression
        :return: The message encoding.
        :rtype: str
        """
        raise NotImplementedError()


class Compressor(MessageEncoding, abc.ABC):
    """
    The compression interface.
    """

    @abc.abstractmethod
    def compress(self, data: BytesLike) -> bytes:
        """
        Compress the data.
        :param data: The data to compress.
        :type data: BytesLike
        :return: The compressed data.
        :rtype: bytes
        """
        raise NotImplementedError()


class Decompressor(MessageEncoding, abc.ABC):
    """
    The decompressor interface.
    """

    @abc.abstractmethod
    def decompress(self, data: BytesLike) -> bytes:
        """
        Decompress the data.
        :param data: The data to decompress.
        :type data: bytes
        :return: The decompressed data.
        :rtype: bytes
        """
        raise NotImplementedError()
