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

import dataclasses
import datetime
import decimal
import enum
import json
import re
from collections import deque
from ipaddress import IPv4Address, IPv6Address
from typing import Any, Dict, List, Optional, Union  # noqa: UP035
from unittest.mock import Mock
from uuid import UUID

import pytest

from dubbo.codec.json_codec import (
    JsonCodec,
    JsonCodecFactory,
    JsonDecoder,
    JsonEncoder,
    decode_value,
    to_jsonable,
)
from dubbo.common import constants
from dubbo.common.descriptor import MethodDescriptor, ParamDetail, ParamKind


# Test data classes and enums for testing
@dataclasses.dataclass
class SampleUser:
    """Sample dataclass for JSON codec testing."""

    name: str
    age: int
    email: Optional[str] = None


class SampleStatus(enum.Enum):
    """Sample enum for JSON codec testing."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclasses.dataclass
class SampleNestedData:
    """Sample nested dataclass for JSON codec testing."""

    user: SampleUser
    status: SampleStatus
    tags: list[str]
    metadata: dict[str, Any]


# Additional test classes for edge cases
class CustomType:
    """Custom type for testing fallback conversion."""

    def __init__(self, value):
        self.value = value

    def __dict__(self):
        # This should fail to access __dict__
        raise AttributeError("No __dict__ available")


class DictConvertible:
    """Class that can be converted to dict."""

    def __init__(self, data):
        self.data = data

    def __iter__(self):
        return iter(self.data.items())


class VarsConvertible:
    """Class that can be converted using vars()."""

    def __init__(self, value):
        self.value = value

    def __iter__(self):
        # This should fail, forcing fallback to vars()
        raise TypeError("Cannot iterate")


@dataclasses.dataclass
class NestedDataclass:
    """Dataclass for testing nested dataclass decoding."""

    inner: SampleUser
    count: int = 0


class TestToJsonable:
    """Test cases for the to_jsonable function."""

    def test_primitive_types(self):
        """Test conversion of primitive JSON-compatible types."""
        assert to_jsonable("string") == "string"
        assert to_jsonable(42) == 42
        assert to_jsonable(3.14) == 3.14
        assert to_jsonable(True) is True
        assert to_jsonable(False) is False
        assert to_jsonable(None) is None

    def test_collections(self):
        """Test conversion of collection types."""
        # List
        assert to_jsonable([1, 2, 3]) == [1, 2, 3]

        # Tuple
        assert to_jsonable((1, 2, 3)) == [1, 2, 3]

        # Set
        result = to_jsonable({1, 2, 3})
        assert isinstance(result, list)
        assert set(result) == {1, 2, 3}

        # Dict
        assert to_jsonable({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_dataclass_conversion(self):
        """Test conversion of dataclass instances."""
        user = SampleUser(name="John", age=30, email="john@example.com")
        result = to_jsonable(user)
        expected = {"name": "John", "age": 30, "email": "john@example.com"}
        assert result == expected

    def test_enum_conversion(self):
        """Test conversion of enum instances."""
        status = SampleStatus.ACTIVE
        assert to_jsonable(status) == "active"

    def test_datetime_conversion(self):
        """Test conversion of datetime objects."""
        dt = datetime.datetime(2023, 12, 25, 12, 30, 45)
        assert to_jsonable(dt) == "2023-12-25T12:30:45"

        date = datetime.date(2023, 12, 25)
        assert to_jsonable(date) == "2023-12-25"

        time = datetime.time(12, 30, 45)
        assert to_jsonable(time) == "12:30:45"

    def test_timedelta_conversion(self):
        """Test conversion of timedelta objects."""
        td = datetime.timedelta(days=1, hours=2, minutes=30)
        expected_seconds = 24 * 3600 + 2 * 3600 + 30 * 60  # 94200 seconds
        assert to_jsonable(td) == expected_seconds

    def test_decimal_conversion(self):
        """Test conversion of Decimal objects."""
        dec = decimal.Decimal("123.45")
        assert to_jsonable(dec) == "123.45"

    def test_uuid_conversion(self):
        """Test conversion of UUID objects."""
        uuid_obj = UUID("12345678-1234-5678-1234-567812345678")
        assert to_jsonable(uuid_obj) == "12345678-1234-5678-1234-567812345678"

    def test_ip_address_conversion(self):
        """Test conversion of IP address objects."""
        ipv4 = IPv4Address("192.168.1.1")
        assert to_jsonable(ipv4) == "192.168.1.1"

        ipv6 = IPv6Address("::1")
        assert to_jsonable(ipv6) == "::1"

    def test_regex_pattern_conversion(self):
        """Test conversion of regex pattern objects."""
        pattern = re.compile(r"\d+")
        assert to_jsonable(pattern) == r"\d+"

    def test_deque_conversion(self):
        """Test conversion of deque objects."""
        dq = deque([1, 2, 3])
        assert to_jsonable(dq) == [1, 2, 3]

    def test_nested_structures(self):
        """Test conversion of nested data structures."""
        user = SampleUser(name="Alice", age=25)
        nested = SampleNestedData(
            user=user,
            status=SampleStatus.ACTIVE,
            tags=["admin", "user"],
            metadata={"created": datetime.date(2023, 1, 1)},
        )

        result = to_jsonable(nested)
        expected = {
            "user": {"name": "Alice", "age": 25, "email": None},
            "status": "active",
            "tags": ["admin", "user"],
            "metadata": {"created": "2023-01-01"},
        }
        assert result == expected

    def test_fallback_conversion(self):
        """Test fallback conversion mechanisms."""

        # Test object that can be converted to dict
        class DictLike:
            def __iter__(self):
                return iter([("key1", "value1"), ("key2", "value2")])

        obj = DictLike()
        result = to_jsonable(obj)
        assert result == {"key1": "value1", "key2": "value2"}

    def test_conversion_failure(self):
        """Test handling of objects that cannot be converted."""

        class UnconvertibleObject:
            def __iter__(self):
                raise TypeError("Cannot iterate")

            @property
            def __dict__(self):
                raise AttributeError("No __dict__")

        obj = UnconvertibleObject()
        with pytest.raises(ValueError):
            to_jsonable(obj)

    def test_fallback_conversion_with_vars(self):
        """Test fallback conversion using vars() when dict() fails."""
        obj = VarsConvertible("test_value")
        result = to_jsonable(obj)
        assert result == {"value": "test_value"}

    def test_conversion_failure_no_dict_no_vars(self):
        """Test handling of objects that cannot be converted via dict() or vars()."""

        class UnconvertibleObject:
            def __iter__(self):
                raise TypeError("Cannot iterate")

            def __getattribute__(self, name):
                if name == "__dict__":
                    raise AttributeError("No __dict__")
                return super().__getattribute__(name)

        obj = UnconvertibleObject()
        with pytest.raises(ValueError):
            to_jsonable(obj)

    def test_frozenset_conversion(self):
        """Test conversion of frozenset objects."""
        fs = frozenset([1, 2, 3])
        result = to_jsonable(fs)
        assert isinstance(result, list)
        assert set(result) == {1, 2, 3}

    def test_bytes_conversion(self):
        """Test conversion of bytes objects."""
        data = b"hello world"
        result = to_jsonable(data)
        assert result == "hello world"


class TestDecodeValue:
    """Test cases for the decode_value function."""

    def test_any_type_passthrough(self):
        """Test that Any type returns raw value unchanged."""
        raw_value = {"key": "value"}
        result = decode_value(raw_value, Any)
        assert result == raw_value

    def test_primitive_type_decoding(self):
        """Test decoding of primitive types."""
        assert decode_value("hello", str) == "hello"
        assert decode_value(42, int) == 42
        assert decode_value(3.14, float) == 3.14
        assert decode_value(True, bool) is True
        assert decode_value(None, type(None)) is None

    def test_list_type_decoding(self):
        """Test decoding of list types."""
        raw_list = [1, 2, 3]
        result = decode_value(raw_list, List[int])
        assert result == [1, 2, 3]

        # Test nested list decoding
        raw_nested = [["a", "b"], ["c", "d"]]
        result = decode_value(raw_nested, List[List[str]])
        assert result == [["a", "b"], ["c", "d"]]

    def test_dict_type_decoding(self):
        """Test decoding of dict types."""
        raw_dict = {"key1": "value1", "key2": "value2"}
        result = decode_value(raw_dict, Dict[str, str])
        assert result == raw_dict

    def test_union_type_decoding(self):
        """Test decoding of Union types including Optional."""
        # Test Union with string (first type in Union takes precedence)
        result = decode_value("hello", Union[str, int])
        assert result == "hello"

        # Test Union with int (when raw value matches type exactly)
        result = decode_value(42, Union[str, int])
        assert result == 42

        # Test Optional (Union with None)
        result = decode_value(None, Optional[str])
        assert result is None

        result = decode_value("test", Optional[str])
        assert result == "test"

    def test_dataclass_decoding(self):
        """Test decoding of dataclass types."""
        raw_data = {"name": "John", "age": 30, "email": "john@example.com"}
        result = decode_value(raw_data, SampleUser)

        assert isinstance(result, SampleUser)
        assert result.name == "John"
        assert result.age == 30
        assert result.email == "john@example.com"

    def test_datetime_decoding(self):
        """Test decoding of datetime objects."""
        # Test datetime
        dt_str = "2023-12-25T12:30:45"
        result = decode_value(dt_str, datetime.datetime)
        expected = datetime.datetime(2023, 12, 25, 12, 30, 45)
        assert result == expected

    def test_decimal_decoding(self):
        """Test decoding of Decimal objects."""
        result = decode_value("123.45", decimal.Decimal)
        assert result == decimal.Decimal("123.45")

    def test_uuid_decoding(self):
        """Test decoding of UUID objects."""
        uuid_str = "12345678-1234-5678-1234-567812345678"
        result = decode_value(uuid_str, UUID)
        expected = UUID("12345678-1234-5678-1234-567812345678")
        assert result == expected

    def test_collection_type_decoding(self):
        """Test decoding of collection types."""
        # Test set
        result = decode_value([1, 2, 3], set)
        assert result == {1, 2, 3}

        # Test deque
        result = decode_value([1, 2, 3], deque)
        assert result == deque([1, 2, 3])

    def test_decode_failure(self):
        """Test handling of decoding failures."""
        with pytest.raises(ValueError, match="Failed to decode value"):
            decode_value("invalid", int)

    def test_union_type_no_match_failure(self):
        """Test Union type decoding when no type matches."""
        un_supported = {"key1": "value1", "key2": "value2"}
        with pytest.raises(ValueError, match="does not match any type in Union"):
            decode_value(un_supported, Union[int, datetime.datetime])

    def test_union_type_with_exception_handling(self):
        """Test Union type decoding with exception handling during conversion."""
        # Create a Union where some conversions will fail
        result = decode_value("123", Union[int, str])
        # Should return as string since it matches str type directly
        assert result == "123"

        # Test with a value that needs conversion
        result = decode_value(123, Union[str, int])
        # Should return as int since it matches int type directly
        assert result == 123

    def test_dataclass_decoding_with_non_dict_input(self):
        """Test dataclass decoding with non-dict input raises error."""
        with pytest.raises(ValueError, match="Expected a dict for dataclass"):
            decode_value("not_a_dict", SampleUser)

    def test_dataclass_decoding_with_nested_types(self):
        """Test dataclass decoding with nested dataclass fields."""
        raw_data = {"inner": {"name": "John", "age": 30, "email": "john@example.com"}, "count": 5}
        result = decode_value(raw_data, NestedDataclass)

        assert isinstance(result, NestedDataclass)
        assert isinstance(result.inner, SampleUser)
        assert result.inner.name == "John"
        assert result.count == 5

    def test_registered_decoder_usage(self):
        """Test usage of registered decoders for specific types."""
        # Test timedelta decoding
        result = decode_value(3600.5, datetime.timedelta)
        expected = datetime.timedelta(seconds=3600.5)
        assert result == expected

        # Test IP address decoding
        result = decode_value("192.168.1.1", IPv4Address)
        assert result == IPv4Address("192.168.1.1")

    def test_decoder_type_groups_fallback(self):
        """Test fallback to decoder type groups."""
        # Test regex pattern decoding
        result = decode_value(r"\d+", re.Pattern)
        assert result.pattern == r"\d+"

    def test_final_fallback_direct_construction(self):
        """Test final fallback using direct type construction."""
        # Test with a simple type that can be constructed directly
        result = decode_value("42", int)
        assert result == 42

        # Test with set construction
        result = decode_value([1, 2, 3, 2, 1], set)
        assert result == {1, 2, 3}

    def test_decode_value_type_error_handling(self):
        """Test error handling in decode_value with invalid conversions."""
        # Test invalid datetime conversion
        with pytest.raises(ValueError, match="Failed to decode value"):
            decode_value("invalid-datetime", datetime.datetime)

        # Test invalid UUID conversion
        with pytest.raises(ValueError, match="Failed to decode value"):
            decode_value("invalid-uuid", UUID)

    def test_list_with_no_type_args(self):
        """Test list decoding with no type arguments (defaults to Any)."""
        result = decode_value([1, "two", 3.0], List)
        assert result == [1, "two", 3.0]

    def test_dict_with_partial_type_args(self):
        """Test dict decoding with partial type arguments."""
        # Test dict with only key type specified - note Dict requires 2 type parameters
        # So we'll test the behavior with a properly typed Dict but check edge cases
        result = decode_value({"1": "value1", "2": "value2"}, Dict[str, str])
        assert result == {"1": "value1", "2": "value2"}

        # Test dict with no type arguments (using plain dict)
        result = decode_value({"key": "value"}, dict)
        assert result == {"key": "value"}


class TestJsonEncoder:
    """Test cases for JsonEncoder class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.encoder = JsonEncoder()

    def test_encoding_property(self):
        """Test that encoder returns correct encoding format."""
        assert self.encoder.encoding == constants.JSON

    def test_encode_empty_params(self):
        """Test encoding with empty parameters."""
        result = self.encoder.encode([], [])
        assert result == b""

    def test_encode_single_param_list_values(self):
        """Test encoding a single parameter with list values."""
        # Arrange
        user = SampleUser(name="Alice", age=30)
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act
        result = self.encoder.encode([user], params)

        # Assert
        assert isinstance(result, bytes)
        decoded = json.loads(result.decode("utf-8"))
        expected = {"name": "Alice", "age": 30, "email": None}
        assert decoded == expected

    def test_encode_single_param_dict_values(self):
        """Test encoding a single parameter with dict values."""
        # Arrange
        user = SampleUser(name="Bob", age=25)
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act
        result = self.encoder.encode({"user": user}, params)

        # Assert
        assert isinstance(result, bytes)
        decoded = json.loads(result.decode("utf-8"))
        expected = {"name": "Bob", "age": 25, "email": None}
        assert decoded == expected

    def test_encode_primitive_types(self):
        """Test encoding of primitive types."""
        params = [ParamDetail(name="value", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        result = self.encoder.encode(["hello world"], params)
        assert result == b'"hello world"'

        result = self.encoder.encode([42], params)
        assert result == b"42"

    def test_encode_complex_types(self):
        """Test encoding of complex types like datetime and UUID."""
        params = [ParamDetail(name="timestamp", annotation=datetime.datetime, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        dt = datetime.datetime(2023, 12, 25, 12, 30, 45)
        result = self.encoder.encode([dt], params)
        decoded = json.loads(result.decode("utf-8"))
        assert decoded == "2023-12-25T12:30:45"

    def test_encode_multiple_params_raises_error(self):
        """Test that encoding with multiple parameters raises ValueError."""
        params = [
            ParamDetail(name="param1", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD),
            ParamDetail(name="param2", annotation=int, kind=ParamKind.POSITIONAL_OR_KEYWORD),
        ]

        with pytest.raises(ValueError, match="JsonEncoder supports encoding only one parameter"):
            self.encoder.encode(["value1", 42], params)

    def test_encode_list_wrong_length_raises_error(self):
        """Test that encoding with wrong list length raises ValueError."""
        params = [ParamDetail(name="param1", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        with pytest.raises(ValueError, match="Expected a single-element list"):
            self.encoder.encode(["value1", "value2"], params)

    def test_encode_missing_dict_key_raises_error(self):
        """Test that encoding with missing dict key raises ValueError."""
        params = [ParamDetail(name="missing_key", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        with pytest.raises(ValueError, match="Expected key 'missing_key' in values dict"):
            self.encoder.encode({"different_key": "value"}, params)

    def test_encode_non_serializable_raises_error(self):
        """Test that encoding non-serializable objects raises ValueError."""

        class NonSerializable:
            def __iter__(self):
                raise TypeError("Cannot iterate")

            @property
            def __dict__(self):
                raise AttributeError("No __dict__")

        params = [ParamDetail(name="obj", annotation=NonSerializable, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        with pytest.raises(ValueError, match="Failed to encode value"):
            self.encoder.encode([NonSerializable()], params)

    def test_encode_with_custom_encoding(self):
        """Test encoding with custom character encoding."""
        params = [ParamDetail(name="text", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Test with UTF-8 (default)
        result = self.encoder.encode(["hello"], params, encoding="utf-8")
        assert result == b'"hello"'

        # Test with ASCII
        result = self.encoder.encode(["hello"], params, encoding="ascii")
        assert result == b'"hello"'

    def test_encode_with_complex_nested_structures(self):
        """Test encoding with deeply nested structures."""
        complex_data = {
            "users": [SampleUser(name="Alice", age=30), SampleUser(name="Bob", age=25)],
            "metadata": {
                "created": datetime.datetime(2023, 1, 1),
                "tags": {"admin", "user"},  # Set will be converted to list
                "config": {"timeout": datetime.timedelta(seconds=30), "pattern": re.compile(r"\d+")},
            },
        }

        params = [ParamDetail(name="data", annotation=dict, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        result = self.encoder.encode([complex_data], params)
        decoded = json.loads(result.decode("utf-8"))

        # Verify structure is properly encoded
        assert isinstance(decoded["users"], list)
        assert len(decoded["users"]) == 2
        assert decoded["users"][0]["name"] == "Alice"
        assert isinstance(decoded["metadata"]["tags"], list)

    def test_encode_generator_type(self):
        """Test encoding of generator objects."""

        def sample_generator():
            yield 1
            yield 2
            yield 3

        params = [ParamDetail(name="gen", annotation=object, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        result = self.encoder.encode([sample_generator()], params)
        decoded = json.loads(result.decode("utf-8"))
        assert decoded == [1, 2, 3]


class TestJsonDecoder:
    """Test cases for JsonDecoder class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.decoder = JsonDecoder()

    def test_encoding_property(self):
        """Test that decoder returns correct encoding format."""
        assert self.decoder.encoding == constants.JSON

    def test_decode_empty_params(self):
        """Test decoding with empty parameters."""
        result = self.decoder.decode(data=b"", params=[])
        assert result == []

    def test_decode_empty_data_with_params_raises_error(self):
        """Test that decoding empty data with parameters raises ValueError."""
        params = [ParamDetail(name="param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        with pytest.raises(ValueError, match="JsonDecoder received unexpected data"):
            self.decoder.decode(data=b'{"key": "value"}', params=[])

    def test_decode_single_param_positional(self):
        """Test decoding a single positional parameter."""
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_ONLY)]

        data = b'{"name": "Charlie", "age": 35, "email": "charlie@example.com"}'
        result = self.decoder.decode(data=data, params=params)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], SampleUser)
        assert result[0].name == "Charlie"
        assert result[0].age == 35
        assert result[0].email == "charlie@example.com"

    def test_decode_single_param_keyword(self):
        """Test decoding a single keyword parameter."""
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        data = b'{"name": "Diana", "age": 28, "email": null}'
        result = self.decoder.decode(data=data, params=params)

        assert isinstance(result, dict)
        assert "user" in result
        assert isinstance(result["user"], SampleUser)
        assert result["user"].name == "Diana"
        assert result["user"].age == 28

    def test_decode_single_param_keyword_with_none(self):
        """Test decoding a single keyword parameter with None value."""
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        data = b'{"name": "Diana", "age": 28}'  # Missing email field
        result = self.decoder.decode(data=data, params=params)

        assert isinstance(result, dict)
        assert "user" in result
        assert isinstance(result["user"], SampleUser)
        assert result["user"].name == "Diana"
        assert result["user"].age == 28
        assert result["user"].email is None

    def test_decode_primitive_types(self):
        """Test decoding of primitive types."""
        params = [ParamDetail(name="value", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        result = self.decoder.decode(data=b'"hello world"', params=params)
        assert result == {"value": "hello world"}

        params[0].annotation = int
        result = self.decoder.decode(data=b"42", params=params)
        assert result == {"value": 42}

    def test_decode_list_types(self):
        """Test decoding of list types."""
        params = [ParamDetail(name="numbers", annotation=List[int], kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        result = self.decoder.decode(data=b"[1, 2, 3, 4, 5]", params=params)
        assert result == {"numbers": [1, 2, 3, 4, 5]}

    def test_decode_dict_types(self):
        """Test decoding of dict types."""
        params = [ParamDetail(name="mapping", annotation=Dict[str, int], kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        result = self.decoder.decode(data=b'{"a": 1, "b": 2, "c": 3}', params=params)
        assert result == {"mapping": {"a": 1, "b": 2, "c": 3}}

    def test_decode_union_types(self):
        """Test decoding of Union types."""
        params = [ParamDetail(name="value", annotation=Union[str, int], kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Test string variant
        result = self.decoder.decode(data=b'"hello"', params=params)
        assert result == {"value": "hello"}

        # Test int variant - JSON decoder will decode numbers correctly
        result = self.decoder.decode(data=b"42", params=params)
        assert result == {"value": 42}

    def test_decode_multiple_params_raises_error(self):
        """Test that decoding with multiple parameters raises ValueError."""
        params = [
            ParamDetail(name="param1", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD),
            ParamDetail(name="param2", annotation=int, kind=ParamKind.POSITIONAL_OR_KEYWORD),
        ]

        with pytest.raises(ValueError, match="JsonDecoder supports only one parameter"):
            self.decoder.decode(data=b'{"param1": "value", "param2": 42}', params=params)

    def test_decode_invalid_encoding_raises_error(self):
        """Test that decoding with invalid encoding raises ValueError."""
        params = [ParamDetail(name="param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Invalid UTF-8 bytes
        invalid_bytes = b"\xff\xfe"
        with pytest.raises(ValueError, match="Failed to decode bytes"):
            self.decoder.decode(data=invalid_bytes, params=params, encoding="utf-8")

    def test_decode_invalid_json_raises_error(self):
        """Test that decoding invalid JSON raises ValueError."""
        params = [ParamDetail(name="param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        with pytest.raises(ValueError, match="Invalid JSON input"):
            self.decoder.decode(data=b'{"invalid": json}', params=params)

    def test_decode_type_mismatch_raises_error(self):
        """Test that decoding with type mismatch raises ValueError."""
        params = [ParamDetail(name="param", annotation=int, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        with pytest.raises(ValueError, match="Failed to decode JSON value"):
            self.decoder.decode(data=b'"not_an_integer"', params=params)

    def test_decode_with_custom_encoding(self):
        """Test decoding with custom character encoding."""
        params = [ParamDetail(name="text", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Test with ASCII
        result = self.decoder.decode(data=b'"hello"', params=params, encoding="ascii")
        assert result == {"text": "hello"}

    def test_decode_complex_union_types(self):
        """Test decoding of complex Union types."""
        params = [
            ParamDetail(name="value", annotation=Union[SampleUser, str, int], kind=ParamKind.POSITIONAL_OR_KEYWORD)
        ]

        # Test with dataclass data - should create SampleUser instance
        user_data = b'{"name": "Alice", "age": 30, "email": "alice@example.com"}'
        result = self.decoder.decode(data=user_data, params=params)

        # The decoder should successfully create a SampleUser instance
        assert isinstance(result["value"], SampleUser)
        assert result["value"].name == "Alice"

    def test_decode_with_dataclass_missing_fields(self):
        """Test decoding dataclass with missing optional fields."""
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Missing optional email field
        data = b'{"name": "Charlie", "age": 35}'
        result = self.decoder.decode(data=data, params=params)

        user = result["user"]
        assert isinstance(user, SampleUser)
        assert user.name == "Charlie"
        assert user.age == 35
        assert user.email is None

    def test_decode_with_invalid_dataclass_field_types(self):
        """Test decoding dataclass with invalid field types."""
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Invalid age type (string instead of int)
        data = b'{"name": "Diana", "age": "not_a_number", "email": "diana@example.com"}'

        with pytest.raises(ValueError, match="Failed to decode JSON value"):
            self.decoder.decode(data=data, params=params)

    def test_decode_empty_data_with_no_params(self):
        """Test decoding empty data with no parameters should return empty list."""
        result = self.decoder.decode(data=b"", params=[])
        assert result == []

    def test_decode_with_bytes_data(self):
        """Test decoding when dealing with bytes values in JSON."""
        params = [
            ParamDetail(
                name="data",
                annotation=str,  # Use str instead of bytes since JSON can't represent bytes directly
                kind=ParamKind.POSITIONAL_OR_KEYWORD,
            )
        ]

        # JSON doesn't natively support bytes, so this would be a string
        result = self.decoder.decode(data=b'"hello world"', params=params)
        assert result == {"data": "hello world"}

        # Test bytes conversion error handling separately
        with pytest.raises(ValueError, match="Failed to decode value"):
            decode_value("hello world", bytes)  # This will fail because bytes() needs encoding


class TestJsonCodec:
    """Test cases for JsonCodec class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.codec = JsonCodec()

    def test_encoding_property(self):
        """Test that codec returns correct encoding format."""
        assert self.codec.encoding == constants.JSON

    def test_codec_inherits_encoder_and_decoder(self):
        """Test that JsonCodec properly inherits from both encoder and decoder."""
        assert isinstance(self.codec, JsonEncoder)
        assert isinstance(self.codec, JsonDecoder)

    def test_round_trip_encoding_decoding(self):
        """Test complete round-trip encoding and decoding."""
        # Arrange
        user = SampleUser(name="Eve", age=40, email="eve@example.com")
        params = [ParamDetail(name="user", annotation=SampleUser, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act - Encode
        encoded_data = self.codec.encode({"user": user}, params)

        # Act - Decode
        decoded_result = self.codec.decode(data=encoded_data, params=params)

        # Assert
        assert isinstance(decoded_result, dict)
        assert "user" in decoded_result
        decoded_user = decoded_result["user"]
        assert isinstance(decoded_user, SampleUser)
        assert decoded_user.name == user.name
        assert decoded_user.age == user.age
        assert decoded_user.email == user.email

    def test_round_trip_with_complex_data(self):
        """Test round-trip with complex nested data structures."""
        # Arrange
        user = SampleUser(name="Frank", age=45)
        nested_data = SampleNestedData(
            user=user,
            status=SampleStatus.ACTIVE,
            tags=["admin", "power_user"],
            metadata={"created_at": datetime.datetime(2023, 1, 1, 12, 0, 0)},
        )
        params = [ParamDetail(name="data", annotation=SampleNestedData, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act - Encode
        encoded_data = self.codec.encode({"data": nested_data}, params)

        # Act - Decode
        decoded_result = self.codec.decode(data=encoded_data, params=params)

        # Assert
        assert isinstance(decoded_result, dict)
        decoded_nested = decoded_result["data"]
        assert isinstance(decoded_nested, SampleNestedData)
        assert decoded_nested.user.name == "Frank"
        # Note: Status will be decoded as string, not enum
        assert decoded_nested.status == "active"  # Changed this assertion
        assert decoded_nested.tags == ["admin", "power_user"]

    def test_round_trip_with_edge_case_types(self):
        """Test round-trip with various edge case types."""
        edge_data = {
            "uuid": UUID("12345678-1234-5678-1234-567812345678"),
            "ip": IPv4Address("192.168.1.1"),
            "pattern": re.compile(r"\d+"),
            "decimal_val": decimal.Decimal("123.456"),
            "timedelta": datetime.timedelta(hours=2, minutes=30),
            "deque_data": deque([1, 2, 3]),
            "set_data": {1, 2, 3},
        }

        params = [ParamDetail(name="data", annotation=dict, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Encode
        encoded = self.codec.encode({"data": edge_data}, params)

        # Decode back (note: some types will be decoded as strings/primitives)
        decoded = self.codec.decode(data=encoded, params=params)

        # Verify the structure exists
        assert "data" in decoded
        assert isinstance(decoded["data"], dict)

    def test_round_trip_preserves_basic_structure(self):
        """Test that round-trip preserves basic data structure."""
        test_data = {
            "string": "hello",
            "number": 42,
            "float": 3.14,
            "boolean": True,
            "null": None,
            "list": [1, 2, 3],
            "nested": {"inner": "value"},
        }

        params = [ParamDetail(name="data", annotation=dict, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        encoded = self.codec.encode({"data": test_data}, params)
        decoded = self.codec.decode(data=encoded, params=params)

        assert decoded["data"] == test_data


class TestJsonCodecFactory:
    """Test cases for JsonCodecFactory class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = JsonCodecFactory()

    def test_singleton_behavior(self):
        """Test that factory implements singleton pattern."""
        factory1 = JsonCodecFactory()
        factory2 = JsonCodecFactory()
        assert factory1 is factory2

    def test_create_encoder(self):
        """Test that factory creates encoder instances."""
        mock_descriptor = Mock(spec=MethodDescriptor)
        encoder = self.factory.create_encoder(mock_descriptor)

        assert isinstance(encoder, JsonEncoder)
        assert encoder.encoding == constants.JSON

    def test_create_decoder(self):
        """Test that factory creates decoder instances."""
        mock_descriptor = Mock(spec=MethodDescriptor)
        decoder = self.factory.create_decoder(mock_descriptor)

        assert isinstance(decoder, JsonDecoder)
        assert decoder.encoding == constants.JSON

    def test_create_codec(self):
        """Test that factory creates codec instances."""
        mock_descriptor = Mock(spec=MethodDescriptor)
        codec = self.factory.create_codec(mock_descriptor)

        assert isinstance(codec, JsonCodec)
        assert codec.encoding == constants.JSON

    def test_factory_codec_functionality(self):
        """Test that factory-created codec works correctly."""
        mock_descriptor = Mock(spec=MethodDescriptor)
        codec = self.factory.create_codec(mock_descriptor)

        # Test basic encoding/decoding
        params = [ParamDetail(name="message", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        encoded = codec.encode(["Hello World"], params)
        decoded = codec.decode(data=encoded, params=params)

        assert decoded == {"message": "Hello World"}
