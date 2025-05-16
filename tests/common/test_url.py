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
import copy
from urllib.parse import quote

import pytest

from dubbo.common import constants
from dubbo.common._url import URL, URLBuilder, _regularize_path


class TestRegularizePath:
    """
    Tests for the _regularize_path function.
    """

    def test_empty_path(self):
        """
        Test regularizing an empty path.

        :return: None
        """
        assert _regularize_path("") == "/"
        assert _regularize_path("/") == "/"

    def test_path_with_trailing_slashes(self):
        """
        Test regularizing paths with trailing slashes.

        :return: None
        """
        assert _regularize_path("api/") == "/api"
        assert _regularize_path("api///") == "/api"

    def test_path_with_leading_slashes(self):
        """
        Test regularizing paths with leading slashes.

        :return: None
        """
        assert _regularize_path("/api") == "/api"
        assert _regularize_path("///api") == "/api"

    def test_path_with_both_slashes(self):
        """
        Test regularizing paths with both leading and trailing slashes.

        :return: None
        """
        assert _regularize_path("/api/") == "/api"
        assert _regularize_path("///api///") == "/api"


class TestURL:
    """
    Tests for the URL class.
    """

    @pytest.fixture
    def basic_url(self):
        """
        Fixture providing a basic URL instance for testing.

        :return: URL instance
        """
        return URL(
            protocol="dubbo",
            username="user",
            password="pass",
            host="example.com",
            port=8080,
            path="/api/service",
            params={"version": "1.0", "group": "test"},
            attributes={"metadata": {"key": "value"}},
        )

    def test_init(self, basic_url):
        """
        Test URL initialization with all parameters.

        :param basic_url: Basic URL fixture
        :return: None
        """
        assert basic_url.protocol == "dubbo"
        assert basic_url.username == "user"
        assert basic_url.password == "pass"
        assert basic_url.host == "example.com"
        assert basic_url.port == 8080
        assert basic_url.path == "/api/service"
        assert basic_url.get_param("version") == "1.0"
        assert basic_url.get_param("group") == "test"
        assert basic_url.get_attribute("metadata") == {"key": "value"}

    def test_properties(self, basic_url):
        """
        Test URL properties and their setters.

        :param basic_url: Basic URL fixture
        :return: None
        """
        # Test getters
        assert basic_url.protocol == "dubbo"
        assert basic_url.username == "user"
        assert basic_url.password == "pass"
        assert basic_url.host == "example.com"
        assert basic_url.port == 8080
        assert basic_url.path == "/api/service"

        # Test setters
        basic_url.protocol = "http"
        basic_url.username = "newuser"
        basic_url.password = "newpass"
        basic_url.host = "new.example.com"
        basic_url.port = 9090
        basic_url.path = "/new/path"

        assert basic_url.protocol == "http"
        assert basic_url.username == "newuser"
        assert basic_url.password == "newpass"
        assert basic_url.host == "new.example.com"
        assert basic_url.port == 9090
        assert basic_url.path == "/new/path"

        # Test negative port handling
        basic_url.port = -1
        assert basic_url.port == 0

    def test_derived_properties(self):
        """
        Test derived properties like userinfo and location.

        :return: None
        """
        # Test userinfo with both username and password
        url = URL(
            protocol="dubbo",
            username="user",
            password="pass",
            host="example.com",
            port=8080,
            path="/",
            params={},
            attributes={},
        )
        assert url.userinfo == "user:pass"
        assert url.location == "example.com:8080"

        # Test userinfo with only username
        url = URL(
            protocol="dubbo",
            username="user",
            password="",
            host="example.com",
            port=8080,
            path="/",
            params={},
            attributes={},
        )
        assert url.userinfo == "user"

        # Test userinfo with no credentials
        url = URL(
            protocol="dubbo",
            username="",
            password="",
            host="example.com",
            port=8080,
            path="/",
            params={},
            attributes={},
        )
        assert url.userinfo == ""

    def test_param_methods(self, basic_url):
        """
        Test parameter manipulation methods.

        :param basic_url: Basic URL fixture
        :return: None
        """
        # Test get_param
        assert basic_url.get_param("version") == "1.0"
        assert basic_url.get_param("nonexistent") == ""
        assert basic_url.get_param("nonexistent", "default") == "default"

        # Test add_param
        basic_url.add_param("new_param", "value")
        assert basic_url.get_param("new_param") == "value"

        # Test overwrite param
        basic_url.add_param("version", "2.0")
        assert basic_url.get_param("version") == "2.0"

        # Test get_param_int
        basic_url.add_param("int_param", "123")
        assert basic_url.get_param_int("int_param") == 123
        assert basic_url.get_param_int("nonexistent") == 0
        assert basic_url.get_param_int("nonexistent", 42) == 42
        assert basic_url.get_param_int("empty_param") == 0
        basic_url.add_param("empty_param", "")
        assert basic_url.get_param_int("empty_param") == 0

        # Test get_param_float
        basic_url.add_param("float_param", "123.45")
        assert basic_url.get_param_float("float_param") == 123.45
        assert basic_url.get_param_float("nonexistent") == 0.0
        assert basic_url.get_param_float("nonexistent", 42.5) == 42.5
        assert basic_url.get_param_float("empty_param") == 0.0

        # Test get_param_bool
        for true_val in ["true", "1", "yes", "on"]:
            basic_url.add_param("bool_param", true_val)
            assert basic_url.get_param_bool("bool_param") is True

        for false_val in ["false", "0", "no", "off"]:
            basic_url.add_param("bool_param", false_val)
            assert basic_url.get_param_bool("bool_param") is False

        assert basic_url.get_param_bool("nonexistent") is False
        assert basic_url.get_param_bool("nonexistent", True) is True

        # Test remove_param
        basic_url.remove_param("version")
        assert basic_url.get_param("version") == ""

        # Test remove nonexistent param (should not raise exception)
        basic_url.remove_param("nonexistent")

        # Test clear_params
        basic_url.clear_params()
        assert basic_url.get_param("group") == ""
        assert basic_url.get_param("new_param") == ""

    def test_raw_param(self, basic_url):
        """
        Test get_raw_parm method for retrieving core URL fields.

        :param basic_url: Basic URL fixture
        :return: None
        """
        assert basic_url.get_raw_parm(constants.PROTOCOL_KEY) == "dubbo"
        assert basic_url.get_raw_parm(constants.USERNAME_KEY) == "user"
        assert basic_url.get_raw_parm(constants.PASSWORD_KEY) == "pass"
        assert basic_url.get_raw_parm(constants.HOST_KEY) == "example.com"
        assert basic_url.get_raw_parm(constants.PORT_KEY) == "8080"
        assert basic_url.get_raw_parm(constants.PATH_KEY) == "/api/service"
        assert basic_url.get_raw_parm("version") == "1.0"
        assert basic_url.get_raw_parm("nonexistent") == ""

    def test_method_param_methods(self):
        """
        Test method-specific parameter methods.

        :return: None
        """
        url = URL(
            protocol="dubbo",
            username="",
            password="",
            host="example.com",
            port=8080,
            path="/",
            params={
                "methods.sayHello.timeout": "1000",
                "methods.sayHello.retries": "3",
                "methods.sayHello.enabled": "true",
                "methods.sayHello.rate": "0.5",
            },
            attributes={},
        )

        # Test get_method_param
        assert url.get_method_param("sayHello", "timeout") == "1000"
        assert url.get_method_param("sayHello", "nonexistent") == ""
        assert url.get_method_param("nonexistentMethod", "timeout") == ""
        assert url.get_method_param("sayHello", "nonexistent", "default") == "default"

        # Test get_method_param_int
        assert url.get_method_param_int("sayHello", "timeout") == 1000
        assert url.get_method_param_int("sayHello", "retries") == 3
        assert url.get_method_param_int("sayHello", "nonexistent") == 0
        assert url.get_method_param_int("nonexistentMethod", "timeout") == 0
        assert url.get_method_param_int("sayHello", "nonexistent", 42) == 42

        # Test get_method_param_float
        assert url.get_method_param_float("sayHello", "rate") == 0.5
        assert url.get_method_param_float("sayHello", "nonexistent") == 0.0
        assert url.get_method_param_float("nonexistentMethod", "rate") == 0.0
        assert url.get_method_param_float("sayHello", "nonexistent", 42.5) == 42.5

        # Test get_method_param_bool
        assert url.get_method_param_bool("sayHello", "enabled") is True
        assert url.get_method_param_bool("sayHello", "nonexistent") is False
        assert url.get_method_param_bool("nonexistentMethod", "enabled") is False
        assert url.get_method_param_bool("sayHello", "nonexistent", True) is True

    def test_attribute_methods(self, basic_url):
        """
        Test attribute manipulation methods.

        :param basic_url: Basic URL fixture
        :return: None
        """
        # Test get_attribute
        assert basic_url.get_attribute("metadata") == {"key": "value"}
        assert basic_url.get_attribute("nonexistent") is None
        assert basic_url.get_attribute("nonexistent", "default") == "default"

        # Test add_attribute
        basic_url.add_attribute("new_attr", "value")
        assert basic_url.get_attribute("new_attr") == "value"

        # Test overwrite attribute
        basic_url.add_attribute("metadata", {"new_key": "new_value"})
        assert basic_url.get_attribute("metadata") == {"new_key": "new_value"}

        # Test remove_attribute
        basic_url.remove_attribute("metadata")
        assert basic_url.get_attribute("metadata") is None

        # Test remove nonexistent attribute (should not raise exception)
        basic_url.remove_attribute("nonexistent")

        # Test clear_attributes
        basic_url.add_attribute("attr1", "value1")
        basic_url.add_attribute("attr2", "value2")
        basic_url.clear_attributes()
        assert basic_url.get_attribute("attr1") is None
        assert basic_url.get_attribute("attr2") is None

    def test_to_dict(self, basic_url):
        """
        Test converting URL to dictionary.

        :param basic_url: Basic URL fixture
        :return: None
        """
        url_dict = basic_url.to_dict()
        assert url_dict[constants.PROTOCOL_KEY] == "dubbo"
        assert url_dict[constants.USERNAME_KEY] == "user"
        assert url_dict[constants.PASSWORD_KEY] == "pass"
        assert url_dict[constants.HOST_KEY] == "example.com"
        assert url_dict[constants.PORT_KEY] == "8080"
        assert url_dict[constants.PATH_KEY] == "/api/service"
        assert url_dict["version"] == "1.0"
        assert url_dict["group"] == "test"
        assert "metadata" not in url_dict  # attributes are not included

    def test_to_str(self):
        """
        Test converting URL to string representation.

        :return: None
        """
        url = URL(
            protocol="http",
            username="user",
            password="pass",
            host="example.com",
            port=8080,
            path="/api/service",
            params={"version": "1.0", "group": "test"},
            attributes={},
        )

        expected_str = "http://user:pass@example.com:8080/api/service?version=1.0&group=test"
        assert url.to_str() == expected_str

        # Test with encoding
        encoded_str = quote(expected_str, encoding=constants.UTF_8)
        assert url.to_str(encode=True) == encoded_str

        # Test without username/password
        url = URL(
            protocol="http",
            username="",
            password="",
            host="example.com",
            port=8080,
            path="/api/service",
            params={"version": "1.0"},
            attributes={},
        )
        assert url.to_str() == "http://example.com:8080/api/service?version=1.0"

    def test_copy_and_repr(self, basic_url):
        """
        Test copy and __repr__ methods.

        :param basic_url: Basic URL fixture
        :return: None
        """
        # Test copy
        url_copy = basic_url.copy()
        assert url_copy is not basic_url
        assert url_copy.protocol == basic_url.protocol
        assert url_copy.username == basic_url.username
        assert url_copy.password == basic_url.password
        assert url_copy.host == basic_url.host
        assert url_copy.port == basic_url.port
        assert url_copy.path == basic_url.path
        assert url_copy.get_param("version") == basic_url.get_param("version")
        assert url_copy.get_attribute("metadata") == basic_url.get_attribute("metadata")

        # Verify deep copy
        url_copy.add_param("new_param", "value")
        assert basic_url.get_param("new_param") == ""

        url_copy.add_attribute("new_attr", "value")
        assert basic_url.get_attribute("new_attr") is None

        # Test __copy__
        url_copy2 = copy.copy(basic_url)
        assert url_copy2 is not basic_url
        assert url_copy2.protocol == basic_url.protocol

        # Test __repr__
        assert repr(basic_url) == basic_url.to_str()

    def test_from_str(self):
        """
        Test creating URL from string.

        :return: None
        """
        # Complete URL with all components
        url_str = "http://user:pass@example.com:8080/api/service?version=1.0&group=test"
        url = URL.from_str(url_str)

        assert url.protocol == "http"
        assert url.username == "user"
        assert url.password == "pass"
        assert url.host == "example.com"
        assert url.port == 8080
        assert url.path == "/api/service"
        assert url.get_param("version") == "1.0"
        assert url.get_param("group") == "test"

        # URL with minimal components
        url_str = "http://example.com"
        url = URL.from_str(url_str)

        assert url.protocol == "http"
        assert url.username == ""
        assert url.password == ""
        assert url.host == "example.com"
        assert url.port == 0
        assert url.path == "/"

        # URL with encoded characters
        encoded_url = quote("http://user:pass@example.com:8080/api/service?q=hello world", encoding=constants.UTF_8)
        url = URL.from_str(encoded_url, decode=True)

        assert url.protocol == "http"
        assert url.username == "user"
        assert url.password == "pass"
        assert url.host == "example.com"
        assert url.port == 8080
        assert url.path == "/api/service"
        assert url.get_param("q") == "hello world"


class TestURLBuilder:
    """
    Tests for the URLBuilder class.
    """

    def test_init(self):
        """
        Test URLBuilder initialization.

        :return: None
        """
        builder = URLBuilder()
        assert builder._protocol == ""
        assert builder._username == ""
        assert builder._password == ""
        assert builder._host == ""
        assert builder._port == 0
        assert builder._path == "/"
        assert builder._params == {}
        assert builder._attributes == {}

    def test_fluent_api(self):
        """
        Test URLBuilder's fluent API.

        :return: None
        """
        builder = URLBuilder()

        # Test method chaining
        result = (
            builder.protocol("dubbo")
            .username("user")
            .password("pass")
            .host("example.com")
            .port(8080)
            .path("/api/service")
            .param("version", "1.0")
            .param("group", "test")
            .attribute("metadata", {"key": "value"})
        )

        # Ensure each method returns the builder itself
        assert result is builder

        # Ensure values were set correctly
        assert builder._protocol == "dubbo"
        assert builder._username == "user"
        assert builder._password == "pass"
        assert builder._host == "example.com"
        assert builder._port == 8080
        assert builder._path == "/api/service"
        assert builder._params == {"version": "1.0", "group": "test"}
        assert builder._attributes == {"metadata": {"key": "value"}}

    def test_build(self):
        """
        Test building URL from builder.

        :return: None
        """
        url = (
            URLBuilder()
            .protocol("dubbo")
            .username("user")
            .password("pass")
            .host("example.com")
            .port(8080)
            .path("/api/service")
            .param("version", "1.0")
            .param("group", "test")
            .attribute("metadata", {"key": "value"})
            .build()
        )

        # Verify URL was built with correct values
        assert url.protocol == "dubbo"
        assert url.username == "user"
        assert url.password == "pass"
        assert url.host == "example.com"
        assert url.port == 8080
        assert url.path == "/api/service"
        assert url.get_param("version") == "1.0"
        assert url.get_param("group") == "test"
        assert url.get_attribute("metadata") == {"key": "value"}

        # Test parameter value conversion
        url = URLBuilder().param("int_param", 123).param("bool_param", True).param("float_param", 123.45).build()

        assert url.get_param("int_param") == "123"
        assert url.get_param("bool_param") == "True"
        assert url.get_param("float_param") == "123.45"
