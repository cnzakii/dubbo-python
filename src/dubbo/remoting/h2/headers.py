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
from collections import OrderedDict

from hpack import HeaderTuple

from dubbo.common import constant
from dubbo.common.types import StrOrBytes
from dubbo.common.utils import common as common_utils

__all__ = ["Http2Headers", "PseudoHeaderName"]


class PseudoHeaderName(enum.Enum):
    """
    Pseudo header names for HTTP/2.
    See: https://datatracker.ietf.org/doc/html/rfc7540#section-8.1.2.1

    """

    # ":method" - includes the HTTP method
    METHOD = b":method"

    # ":scheme" - includes the scheme portion of the target URI
    SCHEME = b":scheme"

    # ":authority" - includes the authority portion of the target URI
    AUTHORITY = b":authority"

    # ":path" - includes the path and query parts of the target URI
    PATH = b":path"

    # ":status" - includes the response status code
    STATUS = b":status"


_PSEUDO_HEADERS = [
    PseudoHeaderName.METHOD.value,
    PseudoHeaderName.SCHEME.value,
    PseudoHeaderName.AUTHORITY.value,
    PseudoHeaderName.PATH.value,
    PseudoHeaderName.STATUS.value,
]


class Http2Headers:
    """
    A class representing HTTP/2 headers, including support for
    pseudo-headers and normal headers.

    Ensures proper ordering and legality of pseudo-header fields.
    See: https://datatracker.ietf.org/doc/html/rfc7540#section-8.1.2
    """

    __slots__ = ("_pseudo_headers", "_other_headers")

    _pseudo_headers: OrderedDict[bytes, bytes]
    _other_headers: OrderedDict[bytes, bytes]

    def __init__(self):
        self._pseudo_headers = OrderedDict()
        self._other_headers = OrderedDict()

    @property
    def method(self) -> bytes:
        """
        Get the HTTP method.

        :return: The HTTP method.
        :rtype: bytes
        """
        return self._pseudo_headers.get(PseudoHeaderName.METHOD.value, b"")

    @method.setter
    def method(self, value: StrOrBytes) -> None:
        """
        Set the HTTP method.

        :param value: The HTTP method.
        :type value: StrOrBytes
        """
        self._pseudo_headers[PseudoHeaderName.METHOD.value] = common_utils.to_bytes(value)

    @property
    def scheme(self) -> bytes:
        """
        Get the HTTP scheme.

        :return: The HTTP scheme.
        :rtype: bytes
        """
        return self._pseudo_headers.get(PseudoHeaderName.SCHEME.value, b"")

    @scheme.setter
    def scheme(self, value: StrOrBytes) -> None:
        """
        Set the HTTP scheme.

        :param value: The HTTP scheme.
        :type value: StrOrBytes
        """
        self._pseudo_headers[PseudoHeaderName.SCHEME.value] = common_utils.to_bytes(value)

    @property
    def authority(self) -> bytes:
        """
        Get the HTTP authority.

        :return: The HTTP authority.
        :rtype: bytes
        """
        return self._pseudo_headers.get(PseudoHeaderName.AUTHORITY.value, b"")

    @authority.setter
    def authority(self, value: StrOrBytes) -> None:
        """
        Set the HTTP authority.

        :param value: The HTTP authority.
        :type value: StrOrBytes
        """
        self._pseudo_headers[PseudoHeaderName.AUTHORITY.value] = common_utils.to_bytes(value)

    @property
    def path(self) -> bytes:
        """
        Get the HTTP path.

        :return: The HTTP path.
        :rtype: bytes
        """
        return self._pseudo_headers.get(PseudoHeaderName.PATH.value, b"")

    @path.setter
    def path(self, value: StrOrBytes) -> None:
        """
        Set the HTTP path.

        :param value: The HTTP path.
        :type value: StrOrBytes
        """
        self._pseudo_headers[PseudoHeaderName.PATH.value] = common_utils.to_bytes(value)

    @property
    def status(self) -> bytes:
        """
        Get the HTTP status.

        :return: The HTTP status.
        :rtype: bytes
        """
        return self._pseudo_headers.get(PseudoHeaderName.STATUS.value, b"")

    @status.setter
    def status(self, value: StrOrBytes) -> None:
        """
        Set the HTTP status.

        :param value: The HTTP status.
        :type value: StrOrBytes
        """
        self._pseudo_headers[PseudoHeaderName.STATUS.value] = common_utils.to_bytes(value)

    def add_param(self, key: StrOrBytes, value: StrOrBytes) -> None:
        """
        Add a parameter to the headers. Pseudo-headers will dispatch to their corresponding setters.

        :param key: The parameter key.
        :param value: The parameter value.
        """
        key = common_utils.to_bytes(key)
        value = common_utils.to_bytes(value)

        if key.startswith(b":"):
            # Handle pseudo-headers
            attr_name = key.decode(constant.UTF_8).split(":", 1)[-1]
            if hasattr(type(self), attr_name):
                setattr(self, attr_name, value)
            else:
                raise ValueError(f"Invalid or unsupported pseudo header: :{attr_name}")
        else:
            self._other_headers[key] = value

    def remove_param(self, key: StrOrBytes) -> None:
        """
        Remove a parameter from the headers.

        :param key: The parameter key.
        :type key: StrOrBytes
        """
        key = common_utils.to_bytes(key)
        if key.startswith(b":"):
            self._pseudo_headers.pop(key, None)
        else:
            del self._other_headers[common_utils.to_bytes(key)]

    def get_param(self, key: StrOrBytes) -> bytes:
        """
        Get a parameter from the headers.

        :param key: The parameter key.
        :type key: StrOrBytes
        :return: The parameter value.
        :rtype: bytes
        """
        key = common_utils.to_bytes(key)
        if key.startswith(b":"):
            return self._pseudo_headers.get(key, b"")
        else:
            return self._other_headers.get(key, b"")

    def clear_params(self) -> None:
        """
        Clear all parameters from the headers.
        """
        self._pseudo_headers.clear()
        self._other_headers.clear()

    def _iter_filtered_headers(self, strip_empty: bool = True):
        for k, v in self._pseudo_headers.items():
            if not strip_empty or (k and v):
                yield k, v
        for k, v in self._other_headers.items():
            if not strip_empty or (k and v):
                yield k, v

    def to_dict(self, strip_empty: bool = True) -> OrderedDict[bytes, bytes]:
        return OrderedDict(self._iter_filtered_headers(strip_empty=strip_empty))

    def to_list(self, strip_empty: bool = True) -> list[tuple[bytes, bytes]]:
        return list(self._iter_filtered_headers(strip_empty=strip_empty))

    def to_hpack(self, strip_empty: bool = True) -> list[HeaderTuple]:
        return [HeaderTuple(k, v) for k, v in self._iter_filtered_headers(strip_empty=strip_empty)]

    @classmethod
    def from_list(cls, headers: list[tuple[StrOrBytes, StrOrBytes]]) -> "Http2Headers":
        instance = cls()
        for k, v in headers:
            instance.add_param(k, v)
        return instance

    @classmethod
    def from_hpack(cls, headers: list[HeaderTuple]) -> "Http2Headers":
        instance = cls()
        for k, v in headers:
            instance.add_param(k, v)
        return instance
