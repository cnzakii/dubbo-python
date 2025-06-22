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
import json
from typing import Annotated, Any, Optional

import pytest
from pydantic import BaseModel, Field, ValidationError
from pydantic_core import PydanticUndefined

from dubbo.codec.pydantic_codec import (
    ModelField,
    PydanticCodec,
    PydanticDecoder,
    PydanticEncoder,
    analyze_method,
    create_merged_model,
    get_param_fields,
    get_return_field,
)
from dubbo.common.classes import CallType
from dubbo.common.descriptor import MethodDescriptor, ParamDetail, ParamKind, ReturnParamDetail


# Test Pydantic models
class User(BaseModel):
    """Test user model."""

    name: str
    age: int = Field(ge=0, le=150)
    email: Optional[str] = None
    is_active: bool = True


class Address(BaseModel):
    """Test address model."""

    street: str
    city: str
    country: str = "China"


class UserWithAddress(BaseModel):
    """User model with address."""

    user: User
    address: Address
    tags: list[str] = Field(default_factory=list, alias="user_tags")


class TestModelField:
    """Tests for ModelField class."""

    def test_model_field_creation_from_param_detail(self):
        """Test creating ModelField from ParamDetail."""
        param = ParamDetail(
            name="test_param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD, required=True, default=None
        )

        field = ModelField.from_param_detail(param, {})
        assert field.name == "test_param"
        assert field.raw_detail == param
        assert field.default is PydanticUndefined

    def test_model_field_with_default_value(self):
        """Test ModelField with default value."""
        param = ParamDetail(
            name="optional_param", annotation=int, kind=ParamKind.POSITIONAL_OR_KEYWORD, required=False, default=42
        )

        field = ModelField.from_param_detail(param, {})
        assert field.name == "optional_param"
        assert field.default == 42

    def test_model_field_with_annotated_type(self):
        """Test ModelField with Annotated type."""
        param = ParamDetail(
            name="validated_param",
            annotation=Annotated[int, Field(ge=0, le=100)],
            kind=ParamKind.POSITIONAL_OR_KEYWORD,
            required=True,
            default=None,
        )

        field = ModelField.from_param_detail(param, {})
        assert field.name == "validated_param"

        # Test validation - should raise ValidationError for invalid values
        with pytest.raises(ValidationError):
            field.adapter.validate_python(-1)  # Should fail because ge=0

        with pytest.raises(ValidationError):
            field.adapter.validate_python(200)  # Should fail because le=100

        # Valid value should work
        validated = field.adapter.validate_python(50)
        assert validated == 50

    def test_model_field_with_pydantic_model(self):
        """Test ModelField with Pydantic model type."""
        param = ParamDetail(
            name="user_param", annotation=User, kind=ParamKind.POSITIONAL_OR_KEYWORD, required=True, default=None
        )

        field = ModelField.from_param_detail(param, {})
        assert field.name == "user_param"

        # Test validation
        user_data = {"name": "John", "age": 30}
        validated = field.adapter.validate_python(user_data)
        assert isinstance(validated, User)
        assert validated.name == "John"
        assert validated.age == 30


class TestCreateMergedModel:
    """Tests for create_merged_model function."""

    def test_create_merged_model_simple(self):
        """Test creating simple merged model."""
        fields = {
            "name": ModelField.from_param_detail(
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None), {}
            ),
            "age": ModelField.from_param_detail(
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, False, 18), {}
            ),
        }

        MergedModel = create_merged_model(fields)

        # Test model creation
        instance = MergedModel(name="John", age=25)
        assert instance.name == "John"
        assert instance.age == 25

        # Test default value
        instance_with_default = MergedModel(name="Jane")
        assert instance_with_default.name == "Jane"
        assert instance_with_default.age == 18

    def test_create_merged_model_with_pydantic_models(self):
        """Test creating merged model with Pydantic models."""
        fields = {
            "user": ModelField.from_param_detail(
                ParamDetail("user", User, ParamKind.POSITIONAL_OR_KEYWORD, True, None), {}
            ),
            "metadata": ModelField.from_param_detail(
                ParamDetail("metadata", dict[str, Any], ParamKind.POSITIONAL_OR_KEYWORD, False, {}), {}
            ),
        }

        MergedModel = create_merged_model(fields)

        instance_data = {"user": {"name": "John", "age": 30}, "metadata": {"source": "test"}}
        instance = MergedModel(**instance_data)

        assert isinstance(instance.user, User)
        assert instance.user.name == "John"
        assert instance.metadata["source"] == "test"


class TestAnalyzeMethod:
    """Tests for analyze_method function."""

    def test_analyze_simple_method(self):
        """Test analyzing simple method."""

        def simple_func(name: str, age: int = 18) -> str:
            return f"{name} is {age} years old"

        descriptor = MethodDescriptor(
            name="simple_func",
            call=simple_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, False, 18),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        analyze_method(descriptor)

        param_fields = get_param_fields(descriptor)
        return_field = get_return_field(descriptor)

        assert len(param_fields) == 2
        assert "name" in param_fields
        assert "age" in param_fields
        assert param_fields["name"].name == "name"
        assert param_fields["age"].default == 18
        assert return_field.name == "return"

    def test_analyze_method_with_pydantic_models(self):
        """Test analyzing method with Pydantic models."""

        def user_func(user: User, address: Optional[Address] = None) -> UserWithAddress:
            return UserWithAddress(user=user, address=address or Address(street="", city=""))

        descriptor = MethodDescriptor(
            name="user_func",
            call=user_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("user", User, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("address", Optional[Address], ParamKind.POSITIONAL_OR_KEYWORD, False, None),
            ],
            return_param=ReturnParamDetail(annotation=UserWithAddress),
            attributes={},
        )

        analyze_method(descriptor)

        param_fields = get_param_fields(descriptor)
        return_field = get_return_field(descriptor)

        assert len(param_fields) == 2
        assert param_fields["user"].raw_detail.annotation == User
        assert param_fields["address"].raw_detail.annotation == Optional[Address]
        assert return_field.raw_detail.annotation == UserWithAddress


class TestPydanticEncoder:
    """Tests for PydanticEncoder class."""

    def test_encode_no_params(self):
        """Test encoding method with no parameters."""

        def no_param_func() -> str:
            return "hello"

        descriptor = MethodDescriptor(
            name="no_param_func",
            call=no_param_func,
            call_type=CallType.UNARY,
            params=[],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        encoder = PydanticEncoder(descriptor)
        result = encoder.encode([], [])
        assert result == b""

    def test_encode_single_param_basic_type(self):
        """Test encoding single basic type parameter."""

        def single_param_func(name: str) -> str:
            return f"Hello {name}"

        descriptor = MethodDescriptor(
            name="single_param_func",
            call=single_param_func,
            call_type=CallType.UNARY,
            params=[ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        encoder = PydanticEncoder(descriptor)

        # Test list input
        result = encoder.encode(["John"], descriptor.params)
        assert result == b'"John"'

        # Test dict input
        result = encoder.encode({"name": "Jane"}, descriptor.params)
        assert result == b'"Jane"'

    def test_encode_single_param_pydantic_model(self):
        """Test encoding single Pydantic model parameter."""

        def user_func(user: User) -> str:
            return f"User: {user.name}"

        descriptor = MethodDescriptor(
            name="user_func",
            call=user_func,
            call_type=CallType.UNARY,
            params=[ParamDetail("user", User, ParamKind.POSITIONAL_OR_KEYWORD, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        encoder = PydanticEncoder(descriptor)

        user_data = {"name": "John", "age": 30, "email": "john@example.com"}
        result = encoder.encode([user_data], descriptor.params)

        # Parse result for validation
        decoded = json.loads(result)
        assert decoded["name"] == "John"
        assert decoded["age"] == 30
        assert decoded["email"] == "john@example.com"
        assert decoded["is_active"] is True  # Default value

    def test_encode_multiple_params(self):
        """Test encoding multiple parameters."""

        def multi_param_func(name: str, age: int = 18, active: bool = True) -> str:
            return f"{name}, {age}, {active}"

        descriptor = MethodDescriptor(
            name="multi_param_func",
            call=multi_param_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, False, 18),
                ParamDetail("active", bool, ParamKind.POSITIONAL_OR_KEYWORD, False, True),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        encoder = PydanticEncoder(descriptor)

        # Test list input
        result = encoder.encode(["John", 25, False], descriptor.params)
        decoded = json.loads(result)
        assert decoded["name"] == "John"
        assert decoded["age"] == 25
        assert decoded["active"] is False

        # Test dict input
        result = encoder.encode({"name": "Jane", "age": 30}, descriptor.params)
        decoded = json.loads(result)
        assert decoded["name"] == "Jane"
        assert decoded["age"] == 30
        assert decoded["active"] is True  # Default value

    def test_encode_with_validation_error(self):
        """Test encoding with validation error."""

        def validated_func(age: Annotated[int, Field(ge=0, le=150)]) -> str:
            return str(age)

        descriptor = MethodDescriptor(
            name="validated_func",
            call=validated_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("age", Annotated[int, Field(ge=0, le=150)], ParamKind.POSITIONAL_OR_KEYWORD, True, None)
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        encoder = PydanticEncoder(descriptor)

        # Should raise ValidationError for invalid age
        with pytest.raises(ValidationError):
            encoder.encode([-5], descriptor.params)

        with pytest.raises(ValidationError):
            encoder.encode([200], descriptor.params)


class TestPydanticDecoder:
    """Tests for PydanticDecoder class."""

    def test_decode_no_params(self):
        """Test decoding method with no parameters."""

        def no_param_func() -> str:
            return "hello"

        descriptor = MethodDescriptor(
            name="no_param_func",
            call=no_param_func,
            call_type=CallType.UNARY,
            params=[],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)
        result = decoder.decode(data=b"", params=[])
        assert result == []

    def test_decode_single_param_basic_type(self):
        """Test decoding single basic type parameter."""

        def single_param_func(name: str) -> str:
            return f"Hello {name}"

        descriptor = MethodDescriptor(
            name="single_param_func",
            call=single_param_func,
            call_type=CallType.UNARY,
            params=[ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Test decoding
        data = b'"John"'
        result = decoder.decode(data=data, params=descriptor.params)
        assert result == {"name": "John"}

    def test_decode_single_param_positional_only(self):
        """Test decoding single positional-only parameter."""

        def positional_func(name: str, /) -> str:
            return f"Hello {name}"

        descriptor = MethodDescriptor(
            name="positional_func",
            call=positional_func,
            call_type=CallType.UNARY,
            params=[ParamDetail("name", str, ParamKind.POSITIONAL_ONLY, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Test decoding positional-only parameter
        data = b'"John"'
        result = decoder.decode(data=data, params=descriptor.params)
        assert result == ["John"]

    def test_decode_return_value(self):
        """Test decoding return value."""

        def func_with_return() -> User:
            return User(name="John", age=30)

        descriptor = MethodDescriptor(
            name="func_with_return",
            call=func_with_return,
            call_type=CallType.UNARY,
            params=[],
            return_param=ReturnParamDetail(annotation=User),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Test decoding return value
        return_param = ParamDetail("return", User, ParamKind.RETURN, True, None)
        data = b'{"name": "John", "age": 30, "is_active": true}'
        result = decoder.decode(data=data, params=[return_param])

        assert isinstance(result, list)
        assert len(result) == 1
        user = result[0]
        assert isinstance(user, User)
        assert user.name == "John"
        assert user.age == 30

    def test_decode_multiple_params(self):
        """Test decoding multiple parameters."""

        def multi_param_func(name: str, age: int = 18, active: bool = True) -> str:
            return f"{name}, {age}, {active}"

        descriptor = MethodDescriptor(
            name="multi_param_func",
            call=multi_param_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, False, 18),
                ParamDetail("active", bool, ParamKind.POSITIONAL_OR_KEYWORD, False, True),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Test decoding with all parameters
        data = b'{"name": "John", "age": 25, "active": false}'
        result = decoder.decode(data=data, params=descriptor.params)
        assert result == {"name": "John", "age": 25, "active": False}

        # Test decoding with missing optional parameters (should use defaults)
        data = b'{"name": "Jane"}'
        result = decoder.decode(data=data, params=descriptor.params)
        assert result == {"name": "Jane", "age": 18, "active": True}

    def test_decode_with_missing_required_param(self):
        """Test decoding with missing required parameter."""

        def required_param_func(name: str, age: int) -> str:
            return f"{name}, {age}"

        descriptor = MethodDescriptor(
            name="required_param_func",
            call=required_param_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Should raise ValueError for missing required parameter
        data = b'{"name": "John"}'
        with pytest.raises(ValueError):
            decoder.decode(data=data, params=descriptor.params)

    def test_decode_with_pydantic_model(self):
        """Test decoding with Pydantic model parameter."""

        def user_func(user: User) -> str:
            return f"User: {user.name}"

        descriptor = MethodDescriptor(
            name="user_func",
            call=user_func,
            call_type=CallType.UNARY,
            params=[ParamDetail("user", User, ParamKind.POSITIONAL_OR_KEYWORD, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Test decoding Pydantic model
        data = b'{"name": "John", "age": 30, "email": "john@example.com"}'
        result = decoder.decode(data=data, params=descriptor.params)

        assert "user" in result
        user = result["user"]
        assert isinstance(user, User)
        assert user.name == "John"
        assert user.age == 30
        assert user.email == "john@example.com"


class TestPydanticCodec:
    """Tests for PydanticCodec complete encode/decode functionality."""

    def test_codec_round_trip_simple(self):
        """Test round-trip encoding and decoding with simple types."""

        def simple_func(name: str, age: int) -> str:
            return f"{name} is {age} years old"

        descriptor = MethodDescriptor(
            name="simple_func",
            call=simple_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        codec = PydanticCodec(descriptor)

        # Original data
        original_data = {"name": "John", "age": 30}

        # Encode
        encoded = codec.encode(original_data, descriptor.params)

        # Decode
        decoded = codec.decode(data=encoded, params=descriptor.params)

        assert decoded == original_data

    def test_codec_round_trip_with_defaults(self):
        """Test round-trip with default values."""

        def func_with_defaults(name: str, age: int = 18, active: bool = True) -> str:
            return f"{name}, {age}, {active}"

        descriptor = MethodDescriptor(
            name="func_with_defaults",
            call=func_with_defaults,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, False, 18),
                ParamDetail("active", bool, ParamKind.POSITIONAL_OR_KEYWORD, False, True),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        codec = PydanticCodec(descriptor)

        # Test with partial data (should fill defaults)
        partial_data = {"name": "John"}
        encoded = codec.encode(partial_data, descriptor.params)
        decoded = codec.decode(data=encoded, params=descriptor.params)

        expected = {"name": "John", "age": 18, "active": True}
        assert decoded == expected

    def test_codec_round_trip_pydantic_models(self):
        """Test round-trip with Pydantic models."""

        def user_address_func(user: User, address: Address) -> UserWithAddress:
            return UserWithAddress(user=user, address=address)

        descriptor = MethodDescriptor(
            name="user_address_func",
            call=user_address_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("user", User, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("address", Address, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
            ],
            return_param=ReturnParamDetail(annotation=UserWithAddress),
            attributes={},
        )

        codec = PydanticCodec(descriptor)

        # Original data
        original_data = {
            "user": {"name": "John", "age": 30, "email": "john@example.com"},
            "address": {"street": "Main St", "city": "New York"},
        }

        # Encode
        encoded = codec.encode(original_data, descriptor.params)

        # Decode
        decoded = codec.decode(data=encoded, params=descriptor.params)

        # Verify structure
        assert "user" in decoded
        assert "address" in decoded
        assert isinstance(decoded["user"], User)
        assert isinstance(decoded["address"], Address)
        assert decoded["user"].name == "John"
        assert decoded["address"].city == "New York"
        assert decoded["address"].country == "China"  # Default value

    def test_codec_with_aliases(self):
        """Test codec with field aliases."""

        def func_with_aliases(data: UserWithAddress) -> str:
            return f"{data.user.name} has {len(data.tags)} tags"

        descriptor = MethodDescriptor(
            name="func_with_aliases",
            call=func_with_aliases,
            call_type=CallType.UNARY,
            params=[ParamDetail("data", UserWithAddress, ParamKind.POSITIONAL_OR_KEYWORD, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        codec = PydanticCodec(descriptor)

        # Use alias in data
        data_with_alias = {
            "user": {"name": "John", "age": 30},
            "address": {"street": "Main St", "city": "New York"},
            "user_tags": ["developer", "python"],  # Using alias
        }

        # Encode
        encoded = codec.encode({"data": data_with_alias}, descriptor.params)

        # Decode
        decoded = codec.decode(data=encoded, params=descriptor.params)

        # Verify the alias was handled correctly
        assert "data" in decoded
        user_with_address = decoded["data"]
        assert isinstance(user_with_address, UserWithAddress)
        assert len(user_with_address.tags) == 2
        assert "developer" in user_with_address.tags

    def test_codec_validation_errors(self):
        """Test codec validation error handling."""

        def validated_func(age: Annotated[int, Field(ge=0, le=150)]) -> str:
            return str(age)

        descriptor = MethodDescriptor(
            name="validated_func",
            call=validated_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("age", Annotated[int, Field(ge=0, le=150)], ParamKind.POSITIONAL_OR_KEYWORD, True, None)
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        codec = PydanticCodec(descriptor)

        # Test encoding validation error
        with pytest.raises(ValidationError):
            codec.encode({"age": -5}, descriptor.params)

        with pytest.raises(ValidationError):
            codec.encode({"age": 200}, descriptor.params)

        # Test decoding validation error
        invalid_data = b'{"age": -5}'
        with pytest.raises(ValidationError):
            codec.decode(data=invalid_data, params=descriptor.params)


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_data_with_params(self):
        """Test empty data with required parameters."""

        def required_param_func(name: str) -> str:
            return f"Hello {name}"

        descriptor = MethodDescriptor(
            name="required_param_func",
            call=required_param_func,
            call_type=CallType.UNARY,
            params=[ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Should raise error for empty data with required params
        with pytest.raises(ValueError):
            decoder.decode(data=b"{}", params=descriptor.params)

    def test_mismatched_param_count(self):
        """Test mismatched parameter count in encoding."""

        def two_param_func(name: str, age: int) -> str:
            return f"{name}, {age}"

        descriptor = MethodDescriptor(
            name="two_param_func",
            call=two_param_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
                ParamDetail("age", int, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        encoder = PydanticEncoder(descriptor)

        # Should raise error for mismatched parameter count
        with pytest.raises(ValueError):
            encoder.encode(["John"], descriptor.params)  # Missing age parameter

    def test_method_not_analyzed(self):
        """Test accessing fields from non-analyzed method."""

        def unanalyzed_func(name: str) -> str:
            return f"Hello {name}"

        descriptor = MethodDescriptor(
            name="unanalyzed_func",
            call=unanalyzed_func,
            call_type=CallType.UNARY,
            params=[ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None)],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},  # No analysis done
        )

        # Should raise TypeError when trying to get param fields
        with pytest.raises(TypeError):
            get_param_fields(descriptor)

        with pytest.raises(TypeError):
            get_return_field(descriptor)

    def test_invalid_param_kinds_for_multiple_params(self):
        """Test invalid parameter kinds for multiple parameters."""

        def invalid_func(*args, **kwargs) -> str:
            return "test"

        # VAR_POSITIONAL parameter should raise error
        descriptor = MethodDescriptor(
            name="invalid_func",
            call=invalid_func,
            call_type=CallType.UNARY,
            params=[
                ParamDetail("args", tuple, ParamKind.VAR_POSITIONAL, False, None),
                ParamDetail("name", str, ParamKind.POSITIONAL_OR_KEYWORD, True, None),
            ],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        # Should raise TypeError for invalid parameter kind
        with pytest.raises(TypeError):
            analyze_method(descriptor)

    def test_non_existent_data_with_no_error(self):
        """Test decoding with non-existent data should return empty list."""

        def no_param_func() -> str:
            return "hello"

        descriptor = MethodDescriptor(
            name="no_param_func",
            call=no_param_func,
            call_type=CallType.UNARY,
            params=[],
            return_param=ReturnParamDetail(annotation=str),
            attributes={},
        )

        decoder = PydanticDecoder(descriptor)

        # Should handle non-empty data with no parameters gracefully
        with pytest.raises(ValueError):
            decoder.decode(data=b'{"unexpected": "data"}', params=[])
