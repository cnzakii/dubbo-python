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

from dubbo.common import constants

from .types import HostLike

__all__ = ["URL", "URLBuilder"]


def _regularize_path(raw: str) -> str:
    """Normalize a URL path to ensure consistent formatting.

    Ensures the path starts with a single '/', has no trailing slashes,
    and returns '/' for empty or root paths.

    Args:
        raw: The raw path string to normalize.

    Returns:
        A normalized path string.
    """
    path = raw.strip("/")
    return "/" + path if path else "/"


_BOOL_TRUE = ("true", "1", "yes", "on")


class URL:
    """Represents a Uniform Resource Locator (URL) for Dubbo services.

    Provides methods for working with URLs in the format:
    scheme://[username[:password]@]host:port/path?key=value&...

    The class handles parameters and custom attributes separately, with
    parameters being part of the URL string and attributes existing only
    in memory.
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
        """Initialize a URL object with all components.

        Args:
            protocol: Protocol scheme (e.g., 'dubbo', 'tri')
            username: Authentication username (optional)
            password: Authentication password (optional)
            host: Hostname or IP address
            port: Port number (0 for default)
            path: Path component of the URL
            params: Query parameters as a dictionary
            attributes: User-defined attributes (not part of URL string)
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
        """Get the user info portion of the URL.

        Returns:
            User credentials in the format "username:password", or just
            "username" if no password, or empty string if no credentials.
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
        """Get the host and port combination.

        Returns:
            Host and port as 'host:port' string.
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
        """Add or update a query parameter.

        Args:
            key: Parameter key
            value: Parameter value
        """
        self._params[key] = value

    def get_param(self, key: str, default: str = "") -> str:
        """Retrieve a query parameter value.

        Args:
            key: Parameter key to retrieve
            default: Default value if key not found

        Returns:
            Parameter value if present, else the default value
        """
        return self._params.get(key, default)

    def get_param_int(self, key: str, default: int = 0) -> int:
        """Retrieve a query parameter as an integer.

        Args:
            key: Parameter key to retrieve
            default: Default integer value if key not found or value not convertible

        Returns:
            Parameter value as integer if present and valid, else default
        """
        value = self.get_param(key)
        return int(value) if value else default

    def get_param_float(self, key: str, default: float = 0.0) -> float:
        """Retrieve a query parameter as a float.

        Args:
            key: Parameter key to retrieve
            default: Default float value if key not found or value not convertible

        Returns:
            Parameter value as float if present and valid, else default
        """
        value = self._params.get(key)
        return float(value) if value else default

    def get_param_bool(self, key: str, default: bool = False) -> bool:
        """Retrieve a query parameter as a boolean.

        Treats "true", "1", "yes", and "on" as True values (case-insensitive).

        Args:
            key: Parameter key to retrieve
            default: Default boolean value if key not found

        Returns:
            Parameter value as boolean if present, else default
        """
        value = self._params.get(key)
        return value.lower() in _BOOL_TRUE if value else default

    def remove_param(self, key: str) -> None:
        """Remove a query parameter if present.

        Args:
            key: Parameter key to remove
        """
        self._params.pop(key, None)

    def clear_params(self) -> None:
        """Remove all query parameters from this URL."""
        self._params.clear()

    def get_raw_parm(self, key: str) -> str:
        """Get a raw parameter or core URL field based on key.

        Special keys like 'protocol', 'host', 'port', etc. return the
        corresponding URL component. All other keys retrieve from parameters.

        Args:
            key: Logical field name (protocol, username, password, host, etc.)

        Returns:
            Corresponding value as string
        """
        if key == constants.PROTOCOL_KEY:
            return self.protocol
        elif key == constants.USERNAME_KEY:
            return self.username
        elif key == constants.PASSWORD_KEY:
            return self.password
        elif key == constants.HOST_KEY:
            return self.host
        elif key == constants.PORT_KEY:
            return str(self.port)
        elif key == constants.PATH_KEY:
            return self.path
        return self.get_param(key)

    def get_method_param(self, method: str, key: str, default: str = "") -> str:
        """Retrieve a method-specific parameter value.

        Looks for parameters in format "methods.{method_name}.{param_name}".

        Args:
            method: Method name
            key: Parameter key
            default: Default value if key not found

        Returns:
            Method-specific parameter value if present, else default
        """
        method_key = f"{constants.METHODS_KEY}.{method}.{key}"
        return self.get_param(method_key, default)

    def get_method_param_int(self, method: str, key: str, default: int = 0) -> int:
        """Retrieve a method-specific parameter as an integer.

        Args:
            method: Method name
            key: Parameter key
            default: Default value if key not found or not convertible

        Returns:
            Method parameter as integer if present and valid, else default
        """
        value = self.get_method_param(method, key)
        return int(value) if value else default

    def get_method_param_float(self, method: str, key: str, default: float = 0.0) -> float:
        """Retrieve a method-specific parameter as a float.

        Args:
            method: Method name
            key: Parameter key
            default: Default value if key not found or not convertible

        Returns:
            Method parameter as float if present and valid, else default
        """
        value = self.get_method_param(method, key)
        return float(value) if value else default

    def get_method_param_bool(self, method: str, key: str, default: bool = False) -> bool:
        """Retrieve a method-specific parameter as a boolean.

        Treats "true", "1", "yes", and "on" as True values (case-insensitive).

        Args:
            method: Method name
            key: Parameter key
            default: Default value if key not found

        Returns:
            Method parameter as boolean if present, else default
        """
        value = self.get_method_param(method, key)
        return value.lower() in _BOOL_TRUE if value else default

    # ---------------------- Attribute Methods ----------------------

    def add_attribute(self, key: str, value: Any) -> None:
        """Add a user-defined attribute (not part of URL string).

        Attributes are only stored in memory and not serialized in URL strings.

        Args:
            key: Attribute key
            value: Attribute value of any type
        """
        self._attributes[key] = value

    def get_attribute(self, key: str, default: Any = None) -> Any:
        """Get a user-defined attribute.

        Args:
            key: Attribute key
            default: Value to return if key not found

        Returns:
            Attribute value if present, else default
        """
        return self._attributes.get(key, default)

    def remove_attribute(self, key: str) -> None:
        """Remove a user-defined attribute if present."""
        self._attributes.pop(key, None)

    def clear_attributes(self) -> None:
        """Remove all user-defined attributes from this URL."""
        self._attributes.clear()

    # ---------------------- Serialization Methods ----------------------

    def to_dict(self) -> dict[str, str]:
        """Convert the URL object to a dictionary.

        Note that user-defined attributes are not included in the result.

        Returns:
            Dictionary with URL components and parameters
        """
        return {
            constants.PROTOCOL_KEY: self.protocol,
            constants.USERNAME_KEY: self.username,
            constants.PASSWORD_KEY: self.password,
            constants.HOST_KEY: self.host,
            constants.PORT_KEY: str(self.port),
            constants.PATH_KEY: self.path,
            **self._params,
        }

    def to_str(self, encode: bool = False) -> str:
        """Convert the URL to its string representation.

        Args:
            encode: Whether to percent-encode the URL. Defaults to False.

        Returns:
            Complete URL string
        """
        netloc = f"{self.userinfo}@{self.location}" if self.username else self.location
        query = urlparse.urlencode(self._params, encoding=constants.UTF_8)
        url = urlparse.urlunparse((self.protocol, netloc, self.path, "", query, ""))
        return urlparse.quote(url, encoding=constants.UTF_8) if encode else url

    def copy(self) -> "URL":
        """Create a deep copy of the current URL object.

        Returns:
            New URL instance with same data but independent dictionaries
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
        """Create a URL instance by parsing a string.

        Args:
            url_str: URL string to parse
            decode: Whether to decode percent-encoded characters. Defaults to False.

        Returns:
            New URL instance

        Example:
            url = URL.from_str("dubbo://localhost:20880/com.example.Service")
        """
        if decode:
            url_str = urlparse.unquote(url_str, encoding=constants.UTF_8)

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
    """Builder for constructing URL instances using a fluent API.

    Provides method chaining to set URL components one at a time before
    building the final URL object.

    Example:
        url = URLBuilder().protocol("dubbo").host("localhost").port(20880)\\
              .path("/com.example.Service").build()
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
        """Set the protocol/scheme component.

        Args:
            value: Protocol value (e.g., 'dubbo', 'tri')

        Returns:
            Self for method chaining
        """
        self._protocol = value
        return self

    def username(self, value: str) -> "URLBuilder":
        """Set the username for authentication.

        Args:
            value: Username string

        Returns:
            Self for method chaining
        """
        self._username = value
        return self

    def password(self, value: str) -> "URLBuilder":
        """Set the password for authentication.

        Args:
            value: Password string

        Returns:
            Self for method chaining
        """
        self._password = value
        return self

    def host(self, value: HostLike) -> "URLBuilder":
        """Set the host component.

        Args:
            value: Host name or IP address

        Returns:
            Self for method chaining
        """
        self._host = str(value)
        return self

    def port(self, value: int) -> "URLBuilder":
        """Set the port number.

        Args:
            value: Port number (use 0 for default)

        Returns:
            Self for method chaining
        """
        self._port = value
        return self

    def path(self, value: str) -> "URLBuilder":
        """Set the path component.

        Args:
            value: URL path string (will be normalized)

        Returns:
            Self for method chaining
        """
        self._path = value
        return self

    def param(self, key: str, value: Any) -> "URLBuilder":
        """Add a query parameter.

        Args:
            key: Parameter name
            value: Parameter value (will be converted to string)

        Returns:
            Self for method chaining
        """
        self._params[key] = str(value)
        return self

    def attribute(self, key: str, value: Any) -> "URLBuilder":
        """Add a user-defined attribute (not part of URL string).

        Args:
            key: Attribute name
            value: Attribute value (any type)

        Returns:
            Self for method chaining
        """
        self._attributes[key] = value
        return self

    def build(self) -> URL:
        """Create and return the final URL object.

        Returns:
            New URL instance with all configured components
        """
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
