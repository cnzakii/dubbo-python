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

import bz2

from dubbo.common.classes import SingletonBase
from dubbo.common.types import BytesLike
from dubbo.common.utils import common as common_utils

from .base import Compressor, Decompressor

__all__ = ["Bzip2"]


class Bzip2(Compressor, Decompressor, SingletonBase):
    """
    The BZIP2 compression and decompressor.
    """

    _ENCODING = "bzip2"

    @classmethod
    def encoding(cls) -> str:
        return cls._ENCODING

    def compress(self, data: BytesLike) -> bytes:
        data = common_utils.to_bytes(data)
        return bz2.compress(data)

    def decompress(self, data: BytesLike) -> bytes:
        data = common_utils.to_bytes(data)
        return bz2.decompress(data)
