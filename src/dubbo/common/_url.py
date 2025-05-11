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
from typing import Any
from urllib import parse as urlparse

from dubbo.common import constant

from .types import IPAddressType

__all__ = ["URL", "URLBuilder"]


def _regularize_path(raw: str) -> str:
    """Normalize a URL path to ensure consistent formatting.

    Ensures the path:
    - Starts with a single '/'
    - Has no trailing slashes
    - Returns '/' for empty or root paths

    :param raw: The raw path string.
    :return: A normalized path.
    """
    path = raw.strip("/")
    return "/" + path if path else "/"


class URL:
    """
    Represents a Uniform Resource Locator (URL).

    The structure follows:
        scheme://[username[:password]@]host:port/path?key=value&...
    """

    __slots__ = ("_protocol", "_username", "_password", "_host", "_port", "_path", "_params", "_attributes")

    _protocol: str
    _username: str
    _password: str
    _host: str
    _port: int
    _path: str
    _params: dict[str, str]
    _attributes: dict[str, Any]

    def __init__(
        self,
        protocol: str,
        username: str,
        password: str,
        host: str,
        port: int,
        path: str,
        params: dict[str, str],
        attributes: dict[str, Any],
    ):
        """
        Initialize a URL object.
        :param protocol: Protocol (e.g., 'http', 'https')
        :param username: Username (optional)
        :param password: Password (optional)
        :param host: Hostname or IP address
        :param port: Port number (0 for default)
        :param path: Path component of the URL
        :param params: Query parameters as a dictionary
        :param attributes: User-defined attributes (not part of URL string)
        """
        self.protocol = protocol
        self.username = username
        self.password = password
        self.host = host
        self.port = port
        self.path = _regularize_path(path)
        self._params = params
        self._attributes = attributes

    # ---------------------- Properties ----------------------
    @property
    def protocol(self) -> str:
        """Protocol (scheme), e.g., 'http'."""
        return self._protocol

    @protocol.setter
    def protocol(self, value: str) -> None:
        self._protocol = value

    @property
    def username(self) -> str:
        """Username used in the URL (optional)."""
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        self._username = value

    @property
    def password(self) -> str:
        """Password used in the URL (optional)."""
        return self._password

    @password.setter
    def password(self, value: str) -> None:
        self._password = value

    @property
    def userinfo(self) -> str:
        """
        Returns the user info portion of the URL.

        :return: "username:password" or just "username", or empty if not set.
        """
        if self._username:
            return f"{self._username}:{self._password}" if self._password else self._username
        return ""

    @property
    def host(self) -> str:
        """Host component of the URL."""
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        self._host = value

    @property
    def port(self) -> int:
        """Port component of the URL."""
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        self._port = max(0, value)  # Ensure port is non-negative

    @property
    def location(self) -> str:
        """
        Returns the host:port combination.

        :return: Host and port in 'host:port' format.
        """
        return f"{self._host}:{self._port}"

    @property
    def path(self) -> str:
        """Normalized path portion of the URL."""
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        self._path = _regularize_path(value)

    # ---------------------- Parameter Methods ----------------------

    def add_param(self, key: str, value: str) -> None:
        """
        Add or update a query parameter.

        :param key: Parameter key
        :param value: Parameter value
        """
        self._params[key] = value

    def get_param(self, key: str, default: str = "") -> str:
        """
        Retrieve a query parameter value.

        :param key: Parameter key
        :param default: Default value if key not found
        :return: Value if present, else default
        """
        return self._params.get(key, default)

    def get_param_int(self, key: str, default: int = 0) -> int:
        """
        Retrieve a query parameter as an integer.

        :param key: Parameter key
        :param default: Default value if key not found
        :return: Value as integer, or default if not found.
        """
        value = self.get_param(key)
        return int(value) if value else default

    def get_param_float(self, key: str, default: float = 0.0) -> float:
        """
        Retrieve a query parameter as a float.

        :param key: Parameter key
        :param default: Default value if key not found
        :return: Value as float, or default if not found.
        """
        value = self._params.get(key)
        return float(value) if value else default

    def get_param_bool(self, key: str, default: bool = False) -> bool:
        """
        Retrieve a query parameter as a boolean.

        :param key: Parameter key
        :param default: Default value if key not found
        :return: Value as boolean, or default if not found.
        """
        value = self._params.get(key)
        return value.lower() in ("true", "1", "yes", "on") if value else default

    def remove_param(self, key: str) -> None:
        """
        Remove a query parameter.

        :param key: Parameter key to remove.
        """
        self._params.pop(key, None)

    def clear_params(self) -> None:
        """Clear all query parameters."""
        self._params.clear()

    def get_raw_parm(self, key: str) -> str:
        """
        Get a raw parameter or core URL field based on key.

        :param key: Logical field name
        :return: Corresponding value as string
        """
        if key == constant.PROTOCOL_KEY:
            return self.protocol
        elif key == constant.USERNAME_KEY:
            return self.username
        elif key == constant.PASSWORD_KEY:
            return self.password
        elif key == constant.HOST_KEY:
            return self.host
        elif key == constant.PORT_KEY:
            return str(self.port)
        elif key == constant.PATH_KEY:
            return self.path
        return self.get_param(key)

    # ---------------------- Attribute Methods ----------------------

    def add_attribute(self, key: str, value: Any) -> None:
        """
        Add a user-defined attribute (not part of URL string).

        :param key: Attribute key
        :param value: Attribute value
        """
        self._attributes[key] = value

    def get_attribute(self, key: str, default: Any = None) -> Any:
        """
        Get a user-defined attribute.

        :param key: Attribute key
        :param default: Fallback value if key not found
        :return: Attribute value or default
        """
        return self._attributes.get(key, default)

    def remove_attribute(self, key: str) -> None:
        """Remove a custom attribute."""
        self._attributes.pop(key, None)

    def clear_attributes(self) -> None:
        """Remove all user-defined attributes."""
        self._attributes.clear()

    # ---------------------- Serialization Methods ----------------------

    def to_dict(self) -> dict[str, str]:
        """
        Convert the URL object to a dictionary (excluding attributes).

        :return: Dictionary representation of the URL.
        """
        return {
            constant.PROTOCOL_KEY: self.protocol,
            constant.USERNAME_KEY: self.username,
            constant.PASSWORD_KEY: self.password,
            constant.HOST_KEY: self.host,
            constant.PORT_KEY: str(self.port),
            constant.PATH_KEY: self.path,
            **self._params,
        }

    def to_str(self, encode: bool = False) -> str:
        """
        Convert the URL to a string representation.

        :param encode: Whether to percent-encode the entire URL.
        :return: Full URL string.
        """
        netloc = f"{self.userinfo}@{self.location}" if self.username else self.location
        query = urlparse.urlencode(self._params, encoding=constant.UTF_8)
        url = urlparse.urlunparse((self.protocol, netloc, self.path, "", query, ""))
        return urlparse.quote(url, encoding=constant.UTF_8) if encode else url

    def copy(self) -> "URL":
        """
        Deep copy of the current URL object.

        :return: New URL instance with same data.
        """
        return URL(
            protocol=self.protocol,
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            path=self.path,
            params=self._params.copy(),
            attributes=self._attributes.copy(),
        )

    def __repr__(self) -> str:
        return self.to_str()

    def __copy__(self) -> "URL":
        return self.copy()

    @staticmethod
    def from_str(url_str: str, decode: bool = False) -> "URL":
        """
        Create a URL instance from a string.

        :param url_str: URL string to parse.
        :param decode: Whether to decode percent-encoded characters.
        :return: URL object.
        """
        if decode:
            url_str = urlparse.unquote(url_str, encoding=constant.UTF_8)

        parsed = urlparse.urlparse(url_str)
        return URL(
            protocol=parsed.scheme,
            username=parsed.username or "",
            password=parsed.password or "",
            host=parsed.hostname or "",
            port=parsed.port or 0,
            path=parsed.path or "/",
            params={k: v[0] for k, v in urlparse.parse_qs(parsed.query).items()},
            attributes={},
        )


class URLBuilder:
    """
    A builder for constructing URL instances using a fluent API.
    """

    __slots__ = ("_protocol", "_username", "_password", "_host", "_port", "_path", "_params", "_attributes")

    _protocol: str
    _username: str
    _password: str
    _host: str
    _port: int
    _path: str
    _params: dict[str, str]
    _attributes: dict[str, Any]

    def __init__(self):
        self._protocol = ""
        self._username = ""
        self._password = ""
        self._host = ""
        self._port = 0
        self._path = "/"
        self._params = {}
        self._attributes = {}

    def protocol(self, value: str) -> "URLBuilder":
        self._protocol = value
        return self

    def username(self, value: str) -> "URLBuilder":
        self._username = value
        return self

    def password(self, value: str) -> "URLBuilder":
        self._password = value
        return self

    def host(self, value: IPAddressType) -> "URLBuilder":
        self._host = str(value)
        return self

    def port(self, value: int) -> "URLBuilder":
        self._port = value
        return self

    def path(self, value: str) -> "URLBuilder":
        self._path = value
        return self

    def param(self, key: str, value: Any) -> "URLBuilder":
        self._params[key] = str(value)
        return self

    def attribute(self, key: str, value: Any) -> "URLBuilder":
        self._attributes[key] = value
        return self

    def build(self) -> URL:
        return URL(
            protocol=self._protocol,
            username=self._username,
            password=self._password,
            host=self._host,
            port=self._port,
            path=self._path,
            params=self._params,
            attributes=self._attributes,
        )
