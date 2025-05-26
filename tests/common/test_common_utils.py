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
import pytest

from dubbo.common import constants
from dubbo.common.utils.common import to_bytes, to_str


class TestToBytes:
    """
    Tests for to_bytes function.
    """

    def test_string_conversion(self):
        """Test converting strings to bytes."""
        # ASCII string
        assert to_bytes("hello") == b"hello"

        # Empty string
        assert to_bytes("") == b""

        # Unicode string
        assert to_bytes("你好") == "你好".encode(constants.UTF_8)

        # Custom encoding
        assert to_bytes("hello", encoding="latin1") == b"hello"
        assert to_bytes("café", encoding="latin1") == b"caf\xe9"

    def test_bytes_input(self):
        """Test passing bytes to to_bytes function."""
        # Regular bytes
        data = b"hello"
        assert to_bytes(data) is data  # Should return the same object
        assert to_bytes(data) == b"hello"

        # Empty bytes
        data = b""
        assert to_bytes(data) is data
        assert to_bytes(data) == b""

    def test_bytearray_input(self):
        """Test converting bytearray to bytes."""
        # Regular bytearray
        data = bytearray(b"hello")
        result = to_bytes(data)
        assert isinstance(result, bytes)
        assert result == b"hello"
        assert result is not data  # Should be a new object

        # Empty bytearray
        data = bytearray()
        assert to_bytes(data) == b""

    def test_memoryview_input(self):
        """Test converting memoryview to bytes."""
        # Regular memoryview
        data = memoryview(b"hello")
        result = to_bytes(data)
        assert isinstance(result, bytes)
        assert result == b"hello"

        # Empty memoryview
        data = memoryview(b"")
        assert to_bytes(data) == b""

        # memoryview of bytearray
        data = memoryview(bytearray(b"hello"))
        assert to_bytes(data) == b"hello"

    def test_invalid_input(self):
        """Test handling of invalid input types."""
        with pytest.raises(TypeError) as excinfo:
            to_bytes(123)
        assert "Expected str, bytes, bytearray, or memoryview" in str(excinfo.value)

        with pytest.raises(TypeError) as excinfo:
            to_bytes(None)
        assert "Expected str, bytes, bytearray, or memoryview" in str(excinfo.value)

        with pytest.raises(TypeError) as excinfo:
            to_bytes([1, 2, 3])
        assert "Expected str, bytes, bytearray, or memoryview" in str(excinfo.value)


class TestToStr:
    """
    Tests for to_str function.
    """

    def test_string_input(self):
        """Test passing string to to_str function."""
        # Regular string
        data = "hello"
        assert to_str(data) is data  # Should return the same object
        assert to_str(data) == "hello"

        # Unicode string
        data = "你好"
        assert to_str(data) is data
        assert to_str(data) == "你好"

        # Empty string
        data = ""
        assert to_str(data) is data
        assert to_str(data) == ""

    def test_bytes_conversion(self):
        """Test converting bytes to string."""
        # ASCII bytes
        assert to_str(b"hello") == "hello"

        # Empty bytes
        assert to_str(b"") == ""

        # UTF-8 encoded bytes
        utf8_bytes = "你好".encode(constants.UTF_8)
        assert to_str(utf8_bytes) == "你好"

        # Custom encoding
        latin1_bytes = b"caf\xe9"
        assert to_str(latin1_bytes, encoding="latin1") == "café"

    def test_bytearray_conversion(self):
        """Test converting bytearray to string."""
        # Regular bytearray
        data = bytearray(b"hello")
        assert to_str(data) == "hello"

        # Empty bytearray
        data = bytearray()
        assert to_str(data) == ""

        # UTF-8 encoded bytearray
        data = bytearray("你好".encode(constants.UTF_8))
        assert to_str(data) == "你好"

    def test_memoryview_conversion(self):
        """Test converting memoryview to string."""
        # Regular memoryview
        data = memoryview(b"hello")
        assert to_str(data) == "hello"

        # Empty memoryview
        data = memoryview(b"")
        assert to_str(data) == ""

        # UTF-8 encoded memoryview
        data = memoryview("你好".encode(constants.UTF_8))
        assert to_str(data) == "你好"

        # memoryview of bytearray
        data = memoryview(bytearray(b"hello"))
        assert to_str(data) == "hello"

    def test_invalid_input(self):
        """Test handling of invalid input types."""
        with pytest.raises(TypeError) as excinfo:
            to_str(123)
        assert "Expected str, bytes, bytearray, or memoryview" in str(excinfo.value)

        with pytest.raises(TypeError) as excinfo:
            to_str(None)
        assert "Expected str, bytes, bytearray, or memoryview" in str(excinfo.value)

        with pytest.raises(TypeError) as excinfo:
            to_str([1, 2, 3])
        assert "Expected str, bytes, bytearray, or memoryview" in str(excinfo.value)

    def test_encoding_errors(self):
        """Test handling of encoding errors."""
        # Invalid UTF-8 bytes
        invalid_utf8 = b"\xff\xfe\xfd"

        # This should raise an error when trying to decode as UTF-8
        with pytest.raises(UnicodeDecodeError):
            to_str(invalid_utf8)

        # Using 'ignore' error handler
        result = to_str(invalid_utf8, errors="ignore")
        assert result == ""

        # Using 'replace' error handler
        result = to_str(invalid_utf8, errors="replace")
        assert result == "���"  # Replacement character
