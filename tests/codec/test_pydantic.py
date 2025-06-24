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
from pydantic import BaseModel, Field, TypeAdapter
from pydantic_core import PydanticUndefined

from dubbo.codec.pydantic_codec import (
    _PYDANTIC_MERGED_MODEL,
    _PYDANTIC_PARAM_FIELDS,
    _PYDANTIC_RETURN_FIELD,
    ModelField,
    PydanticCodec,
    PydanticCodecFactory,
    PydanticDecoder,
    PydanticEncoder,
    analyze_method,
    create_merged_model,
    get_merged_model,
    get_param_fields,
    get_return_field,
    has_merged_model,
    has_param_fields,
    has_return_field,
    set_merged_model,
    set_param_fields,
    set_return_field,
    wrap_validation_errors,
)
from dubbo.common import constants
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


# Helper functions to reduce code duplication
def create_descriptor(name: str, params: list[ParamDetail], return_annotation=str, attributes=None):
    """Create a MethodDescriptor with default values."""
    return MethodDescriptor(
        name=name,
        call=lambda: None,
        call_type=CallType.UNARY,
        params=params,
        return_param=ReturnParamDetail(annotation=return_annotation),
        attributes=attributes or {},
    )


def create_param_detail(name: str, annotation=Any, kind=ParamKind.POSITIONAL_OR_KEYWORD, required=True, default=None):
    """Create a ParamDetail with default values."""
    return ParamDetail(name=name, annotation=annotation, kind=kind, required=required, default=default)


def create_field_info():
    """Create a common FieldInfo for testing."""
    return Field(max_length=100)


class TestParameterAndReturnFieldOperations:
    """Tests for parameter and return field operations in MethodDescriptor."""

    def setup_method(self):
        """Setup common test data."""
        self.field_info = create_field_info()
        self.param_field = ModelField(
            name="param1",
            raw_detail=create_param_detail("param1"),
            info=self.field_info,
            adapter=TypeAdapter(Annotated[str, self.field_info]),
        )
        self.return_field = ModelField(
            name="return",
            raw_detail=ReturnParamDetail(annotation=str),
            info=Field(max_length=100),
            adapter=TypeAdapter(str),
        )
        self.descriptor = create_descriptor("test_method", [])

    def test_param_fields_operations(self):
        """Test all parameter field operations."""
        # Test setting and checking existence
        set_param_fields(self.descriptor, {"param1": self.param_field})
        assert has_param_fields(self.descriptor) is True
        assert self.descriptor.attributes[_PYDANTIC_PARAM_FIELDS]["param1"] == self.param_field

        # Test getting param fields
        fields = get_param_fields(self.descriptor)
        assert "param1" in fields
        assert isinstance(fields["param1"], ModelField)

        # Test clearing and checking non-existence
        self.descriptor.attributes.clear()
        assert has_param_fields(self.descriptor) is False

        # Test getting param fields failure
        with pytest.raises(RuntimeError, match="has not been analyzed for pydantic parameters"):
            get_param_fields(self.descriptor)

    def test_return_field_operations(self):
        """Test all return field operations."""
        # Test setting and checking existence
        set_return_field(self.descriptor, self.return_field)
        assert has_return_field(self.descriptor) is True
        assert self.descriptor.attributes[_PYDANTIC_RETURN_FIELD] == self.return_field

        # Test getting return field
        field = get_return_field(self.descriptor)
        assert field.name == "return"
        assert isinstance(field, ModelField)
        assert field.raw_detail.annotation == str

        # Test clearing and checking non-existence
        self.descriptor.attributes.clear()
        assert has_return_field(self.descriptor) is False

        # Test getting return field failure
        with pytest.raises(RuntimeError, match="has not been analyzed for pydantic return type"):
            get_return_field(self.descriptor)

    def test_merged_model_operations(self):
        """Test all merged model operations."""

        # Create a test merged model
        class MergedModel(BaseModel):
            name: str
            age: int

        model_adapter = TypeAdapter(MergedModel)

        # Test setting and checking existence
        set_merged_model(self.descriptor, MergedModel, model_adapter)
        assert has_merged_model(self.descriptor) is True
        assert self.descriptor.attributes[_PYDANTIC_MERGED_MODEL] == (MergedModel, model_adapter)

        # Test getting merged model
        merged_model, adapter = get_merged_model(self.descriptor)
        assert merged_model == MergedModel
        assert adapter == model_adapter

        # Test clearing and checking non-existence
        self.descriptor.attributes.clear()
        assert has_merged_model(self.descriptor) is False

        # Test getting merged model failure
        with pytest.raises(RuntimeError, match="has not been set a merged model"):
            get_merged_model(self.descriptor)


class TestWrapValidationErrors:
    """Tests for wrap_validation_errors context manager."""

    def test_wrap_validation_errors_type_error(self):
        """Test that TypeError is preserved."""
        with pytest.raises(ValueError, match="Validation failed for field 'test_field':"):
            with wrap_validation_errors("test_field"):
                user = User(name="John", age="invalid")

    def test_wrap_validation_errors_value_error(self):
        """Test that ValueError is preserved."""
        with pytest.raises(ValueError, match="test error"):
            with wrap_validation_errors("test_field"):
                raise ValueError("test error")

    def test_wrap_validation_errors_other_exception(self):
        """Test wrapping other exceptions."""
        with pytest.raises(ValueError, match=r"Unexpected error in field 'test_field'"):
            with wrap_validation_errors("test_field"):
                raise RuntimeError("Some runtime error")

    def test_wrap_validation_errors_no_exception(self):
        """Test normal execution without exception."""
        with wrap_validation_errors("test_field"):
            result = "success"
        assert result == "success"


class TestModelField:
    """Tests for ModelField class."""

    def test_model_field_creation_from_param_detail(self):
        """Test creating ModelField from ParamDetail."""
        param = create_param_detail("test_param")
        field = ModelField.from_param_detail(param, {})

        assert field.name == "test_param"
        assert field.raw_detail == param
        assert field.default is PydanticUndefined

    def test_model_field_with_default_value(self):
        """Test ModelField with default value."""
        param = create_param_detail("optional_param", int, required=False, default=42)
        field = ModelField.from_param_detail(param, {})

        assert field.name == "optional_param"
        assert field.default == 42

    def test_model_field_with_annotated_type(self):
        """Test ModelField with Annotated type."""
        param = create_param_detail("validated_param", Annotated[int, Field(ge=0, le=100)])
        field = ModelField.from_param_detail(param, {})

        assert field.name == "validated_param"

        # Test validation - should raise ValueError for invalid values
        with pytest.raises(ValueError):
            field.validate_python(-1)  # Should fail because ge=0

        with pytest.raises(ValueError):
            field.validate_python(200)  # Should fail because le=100

        # Valid value should work
        validated = field.validate_python(50)
        assert validated == 50

    def test_model_field_with_pydantic_model(self):
        """Test ModelField with Pydantic model type."""
        param = create_param_detail("user_param", User)
        field = ModelField.from_param_detail(param, {})

        assert field.name == "user_param"

        # Test validation
        user_data = {"name": "John", "age": 30}
        validated = field.validate_python(user_data)
        assert isinstance(validated, User)
        assert validated.name == "John"
        assert validated.age == 30

    def test_model_field_validate_json(self):
        """Test ModelField validate_json method."""
        param = create_param_detail("user_param", User)
        field = ModelField.from_param_detail(param, {})

        json_data = b'{"name": "John", "age": 30}'
        validated = field.validate_json(json_data)
        assert isinstance(validated, User)
        assert validated.name == "John"
        assert validated.age == 30

    def test_model_field_dump_python(self):
        """Test ModelField dump_python method."""
        param = create_param_detail("user_param", User)
        field = ModelField.from_param_detail(param, {})

        user = User(name="John", age=30)
        dumped = field.dump_python(user)
        assert isinstance(dumped, dict)
        assert dumped["name"] == "John"
        assert dumped["age"] == 30

    def test_model_field_dump_json(self):
        """Test ModelField dump_json method."""
        param = create_param_detail("user_param", User)
        field = ModelField.from_param_detail(param, {})

        user = User(name="John", age=30)
        dumped = field.dump_json(user)
        assert isinstance(dumped, bytes)

        # Parse back to verify
        parsed = json.loads(dumped)
        assert parsed["name"] == "John"
        assert parsed["age"] == 30

    def test_model_field_validate_and_dump(self):
        """Test ModelField validate_and_dump method."""
        param = create_param_detail("user_param", User)
        field = ModelField.from_param_detail(param, {})

        user_data = {"name": "John", "age": 30}
        dumped = field.validate_and_dump(user_data)
        assert isinstance(dumped, bytes)

        # Parse back to verify
        parsed = json.loads(dumped)
        assert parsed["name"] == "John"
        assert parsed["age"] == 30

    def test_model_field_with_forward_ref(self):
        """Test ModelField with forward reference."""
        param = create_param_detail("forward_param", "User")
        globalns = {"User": User}
        field = ModelField.from_param_detail(param, globalns)

        assert field.name == "forward_param"
        user_data = {"name": "John", "age": 30}
        validated = field.validate_python(user_data)
        assert isinstance(validated, User)

    def test_model_field_with_field_info_from_default(self):
        """Test ModelField with FieldInfo from default value."""
        field_info = Field(description="Test field")
        param = ParamDetail(
            name="test_param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD, required=False, default=field_info
        )
        field = ModelField.from_param_detail(param, {})

        assert field.name == "test_param"
        assert field.info == field_info

    def test_model_field_validation_errors(self):
        """Test ModelField validation error handling."""
        param = create_param_detail("validated_param", Annotated[int, Field(ge=0)])
        field = ModelField.from_param_detail(param, {})

        # Test validate_python error - should be wrapped and re-raised as ValueError
        with pytest.raises(ValueError):
            field.validate_python(-1)

        # Test validate_json error - should be wrapped and re-raised as ValueError
        with pytest.raises(ValueError):
            field.validate_json(b'"-1"')

        # Test validate_and_dump with invalid data
        with pytest.raises(ValueError):
            field.validate_and_dump(-1)


class TestCreateMergedModel:
    """Tests for create_merged_model function."""

    def test_create_merged_model_simple(self):
        """Test creating simple merged model."""
        fields = {
            "name": ModelField.from_param_detail(create_param_detail("name", str), {}),
            "age": ModelField.from_param_detail(create_param_detail("age", int, required=False, default=18), {}),
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
            "user": ModelField.from_param_detail(create_param_detail("user", User), {}),
            "metadata": ModelField.from_param_detail(
                create_param_detail("metadata", dict[str, Any], required=False, default={}), {}
            ),
        }

        MergedModel = create_merged_model(fields)

        instance_data = {"user": {"name": "John", "age": 30}, "metadata": {"source": "test"}}
        instance = MergedModel(**instance_data)

        assert isinstance(instance.user, User)
        assert instance.user.name == "John"
        assert instance.metadata["source"] == "test"

    def test_create_merged_model_invalid_param_kind(self):
        """Test creating merged model with invalid parameter kind."""
        fields = {
            "invalid": ModelField.from_param_detail(
                create_param_detail("invalid", str, kind=ParamKind.VAR_POSITIONAL), {}
            ),
        }

        with pytest.raises(TypeError, match="Parameters must be POSITIONAL_OR_KEYWORD or KEYWORD_ONLY"):
            create_merged_model(fields)

    def test_create_merged_model_with_keyword_only(self):
        """Test creating merged model with keyword-only parameters."""
        fields = {
            "name": ModelField.from_param_detail(create_param_detail("name", str, kind=ParamKind.KEYWORD_ONLY), {}),
        }

        MergedModel = create_merged_model(fields)
        instance = MergedModel(name="John")
        assert instance.name == "John"


class TestAnalyzeMethod:
    """Tests for analyze_method function."""

    def test_analyze_simple_method(self):
        """Test analyzing simple method."""

        def simple_func(name: str, age: int = 18) -> str:
            return f"{name} is {age} years old"

        descriptor = create_descriptor(
            "simple_func",
            [
                create_param_detail("name", str),
                create_param_detail("age", int, required=False, default=18),
            ],
            str,
        )
        descriptor.call = simple_func

        analyze_method(descriptor)

        param_fields = get_param_fields(descriptor)
        return_field = get_return_field(descriptor)

        assert len(param_fields) == 2
        assert "name" in param_fields
        assert "age" in param_fields
        assert param_fields["name"].name == "name"
        assert param_fields["age"].default == 18
        assert return_field.name == "return"

    def test_analyze_no_params_method(self):
        """Test analyzing method with no parameters."""

        def no_param_func() -> None:
            pass

        descriptor = create_descriptor("no_param_func", [], type(None))
        descriptor.call = no_param_func

        analyze_method(descriptor)

        param_fields = get_param_fields(descriptor)
        return_field = get_return_field(descriptor)

        assert len(param_fields) == 0
        assert return_field.name == "return"
        assert return_field.raw_detail.annotation is type(None)

    def test_analyze_method_with_pydantic_models(self):
        """Test analyzing method with Pydantic models."""

        def user_func(user: User, address: Optional[Address] = None) -> UserWithAddress:
            return UserWithAddress(user=user, address=address or Address(street="", city=""))

        descriptor = create_descriptor(
            "user_func",
            [
                create_param_detail("user", User),
                create_param_detail("address", Optional[Address], required=False, default=None),
            ],
            UserWithAddress,
        )
        descriptor.call = user_func

        analyze_method(descriptor)

        param_fields = get_param_fields(descriptor)
        return_field = get_return_field(descriptor)

        assert len(param_fields) == 2
        assert param_fields["user"].raw_detail.annotation == User
        assert param_fields["address"].raw_detail.annotation == Optional[Address]
        assert return_field.raw_detail.annotation == UserWithAddress

    def test_analyze_method_with_invalid_param_kind(self):
        """Test analyzing method with invalid parameter kind."""

        def invalid_func(*args) -> str:
            return "test"

        descriptor = create_descriptor(
            "invalid_func", [create_param_detail("args", tuple, kind=ParamKind.VAR_POSITIONAL, required=False)], str
        )
        descriptor.call = invalid_func

        with pytest.raises(TypeError, match="Invalid parameter kind"):
            analyze_method(descriptor)

    def test_analyze_method_with_globalns(self):
        """Test analyzing method with global namespace for forward references."""

        def func_with_forward_ref(user: "User") -> str:
            return user.name

        # Set global namespace on the function
        func_with_forward_ref.__globalns__ = {"User": User}

        descriptor = create_descriptor("func_with_forward_ref", [create_param_detail("user", "User")], str)
        descriptor.call = func_with_forward_ref

        analyze_method(descriptor)

        param_fields = get_param_fields(descriptor)
        assert len(param_fields) == 1
        assert "user" in param_fields


class TestPydanticEncoder:
    """Tests for PydanticEncoder class."""

    def setup_method(self):
        """Setup common test data."""
        self.simple_descriptor = create_descriptor("simple_func", [], str)
        self.single_param_descriptor = create_descriptor("single_param_func", [create_param_detail("name", str)], str)
        self.multi_param_descriptor = create_descriptor(
            "multi_param_func",
            [
                create_param_detail("name", str),
                create_param_detail("age", int, required=False, default=18),
            ],
            str,
        )

    def test_encoding_property(self):
        """Test that encoder returns correct encoding format."""
        encoder = PydanticEncoder(self.simple_descriptor)
        assert encoder.encoding == constants.JSON

    def test_encode_no_params(self):
        """Test encoding method with no parameters."""
        encoder = PydanticEncoder(self.simple_descriptor)
        result = encoder.encode([], [])
        assert result == b""

    def test_encode_single_param_basic_type(self):
        """Test encoding single basic type parameter."""
        encoder = PydanticEncoder(self.single_param_descriptor)

        # Test list input
        result = encoder.encode(["John"], self.single_param_descriptor.params)
        assert result == b'"John"'

        # Test dict input
        result = encoder.encode({"name": "Jane"}, self.single_param_descriptor.params)
        assert result == b'"Jane"'

    def test_encode_single_param_pydantic_model(self):
        """Test encoding single Pydantic model parameter."""
        descriptor = create_descriptor("user_func", [create_param_detail("user", User)], str)
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
        encoder = PydanticEncoder(self.multi_param_descriptor)

        # Test list input
        result = encoder.encode(["John", 25], self.multi_param_descriptor.params)
        decoded = json.loads(result)
        assert decoded["name"] == "John"
        assert decoded["age"] == 25

        # Test dict input with missing optional param
        result = encoder.encode({"name": "Jane"}, self.multi_param_descriptor.params)
        decoded = json.loads(result)
        assert decoded["name"] == "Jane"
        assert decoded["age"] == 18  # Default value

    def test_encode_with_validation_constraints(self):
        """Test encoding with validation constraints."""
        descriptor = create_descriptor(
            "validated_func", [create_param_detail("age", Annotated[int, Field(ge=0, le=150)])], str
        )
        encoder = PydanticEncoder(descriptor)

        # Valid value should work
        result = encoder.encode([25], descriptor.params)
        # For single parameter, the result is the direct value, not a dict
        decoded = json.loads(result)
        assert decoded == 25

        # Invalid values should raise ValueError
        with pytest.raises(ValueError):
            encoder.encode([-5], descriptor.params)

        with pytest.raises(ValueError):
            encoder.encode([200], descriptor.params)

    def test_encode_return_param(self):
        """Test encoding return parameter."""
        descriptor = create_descriptor("func_with_return", [], User)
        encoder = PydanticEncoder(descriptor)

        user = User(name="John", age=30)
        result = encoder.encode([user], [descriptor.return_param])
        decoded = json.loads(result)

        assert decoded["name"] == "John"
        assert decoded["age"] == 30
        assert decoded["is_active"] is True

    def test_encode_return_none(self):
        """Test encoding return parameter with None value."""
        descriptor = create_descriptor("func_with_none_return", [], Optional[User])
        encoder = PydanticEncoder(descriptor)

        result = encoder.encode([None], [descriptor.return_param])
        decoded = json.loads(result)
        assert decoded is None

    def test_encode_mismatched_param_count(self):
        """Test encoding with mismatched parameter count."""
        encoder = PydanticEncoder(self.multi_param_descriptor)

        with pytest.raises(ValueError, match="Number of provided values does not match"):
            encoder.encode(["John"], self.multi_param_descriptor.params)  # Missing age

    def test_encode_positional_only_single_param(self):
        """Test encoding with single positional-only parameter."""
        descriptor = create_descriptor(
            "positional_func", [create_param_detail("name", str, kind=ParamKind.POSITIONAL_ONLY)], str
        )
        encoder = PydanticEncoder(descriptor)

        # Single positional-only parameter should work
        result = encoder.encode(["John"], descriptor.params)
        assert result == b'"John"'

    def test_encode_multiple_positional_only_fails(self):
        """Test that multiple positional-only parameters fail during merged model creation."""
        descriptor = create_descriptor(
            "positional_func",
            [
                create_param_detail("name", str, kind=ParamKind.POSITIONAL_ONLY),
                create_param_detail("age", int, kind=ParamKind.POSITIONAL_ONLY),
            ],
            str,
        )

        with pytest.raises(TypeError, match="Parameters must be POSITIONAL_OR_KEYWORD or KEYWORD_ONLY"):
            PydanticEncoder(descriptor)

    def test_encoder_auto_analysis(self):
        """Test that encoder automatically analyzes method if not done."""
        descriptor = create_descriptor("test_func", [create_param_detail("name", str)], str)
        # Don't pre-analyze the method
        assert not has_param_fields(descriptor)
        assert not has_return_field(descriptor)

        encoder = PydanticEncoder(descriptor)

        # After creating encoder, method should be analyzed
        assert has_param_fields(descriptor)
        assert has_return_field(descriptor)


class TestPydanticDecoder:
    """Tests for PydanticDecoder class."""

    def setup_method(self):
        """Setup common test data."""
        self.simple_descriptor = create_descriptor("simple_func", [], str)
        self.single_param_descriptor = create_descriptor("single_param_func", [create_param_detail("name", str)], str)
        self.multi_param_descriptor = create_descriptor(
            "multi_param_func",
            [
                create_param_detail("name", str),
                create_param_detail("age", int, required=False, default=18),
            ],
            str,
        )

    def test_encoding_property(self):
        """Test that decoder returns correct encoding format."""
        decoder = PydanticDecoder(self.simple_descriptor)
        assert decoder.encoding == constants.JSON

    def test_decode_no_params(self):
        """Test decoding method with no parameters."""
        decoder = PydanticDecoder(self.simple_descriptor)
        result = decoder.decode(data=b"", params=[])
        assert result == []

    def test_decode_non_empty_data_with_no_params(self):
        """Test decoding non-empty data with no parameters."""
        decoder = PydanticDecoder(self.simple_descriptor)

        with pytest.raises(ValueError, match="Expected no parameters, but non-empty data was provided"):
            decoder.decode(data=b'{"unexpected": "data"}', params=[])

    def test_decode_single_param_basic_type(self):
        """Test decoding single basic type parameter."""
        decoder = PydanticDecoder(self.single_param_descriptor)

        data = b'"John"'
        result = decoder.decode(data=data, params=self.single_param_descriptor.params)
        assert result == {"name": "John"}

    def test_decode_single_param_positional_only(self):
        """Test decoding single positional-only parameter."""
        descriptor = create_descriptor(
            "positional_func", [create_param_detail("name", str, kind=ParamKind.POSITIONAL_ONLY)], str
        )
        decoder = PydanticDecoder(descriptor)

        data = b'"John"'
        result = decoder.decode(data=data, params=descriptor.params)
        assert result == ["John"]

    def test_decode_return_value(self):
        """Test decoding return value."""
        descriptor = create_descriptor("func_with_return", [], User)
        decoder = PydanticDecoder(descriptor)

        data = b'{"name": "John", "age": 30, "is_active": true}'
        result = decoder.decode(data=data, params=[descriptor.return_param])

        assert isinstance(result, list)
        assert len(result) == 1
        user = result[0]
        assert isinstance(user, User)
        assert user.name == "John"
        assert user.age == 30

    def test_decode_return_none(self):
        """Test decoding return value with None."""
        descriptor = create_descriptor("func_with_none_return", [], Optional[User])
        decoder = PydanticDecoder(descriptor)

        data = b"null"
        result = decoder.decode(data=data, params=[descriptor.return_param])
        assert result == [None]

    def test_decode_multiple_params(self):
        """Test decoding multiple parameters."""
        decoder = PydanticDecoder(self.multi_param_descriptor)

        # Test decoding with all parameters
        data = b'{"name": "John", "age": 25}'
        result = decoder.decode(data=data, params=self.multi_param_descriptor.params)
        assert result == {"name": "John", "age": 25}

        # Test decoding with missing optional parameters (should use defaults)
        data = b'{"name": "Jane"}'
        result = decoder.decode(data=data, params=self.multi_param_descriptor.params)
        assert result == {"name": "Jane", "age": 18}

    def test_decode_with_missing_required_param(self):
        """Test decoding with missing required parameter."""
        descriptor = create_descriptor(
            "required_param_func",
            [
                create_param_detail("name", str),
                create_param_detail("age", int),
            ],
            str,
        )
        decoder = PydanticDecoder(descriptor)

        data = b'{"name": "John"}'
        with pytest.raises(ValueError, match="Field 'age' is required but not provided"):
            decoder.decode(data=data, params=descriptor.params)

    def test_decode_with_pydantic_model(self):
        """Test decoding with Pydantic model parameter."""
        descriptor = create_descriptor("user_func", [create_param_detail("user", User)], str)
        decoder = PydanticDecoder(descriptor)

        data = b'{"name": "John", "age": 30, "email": "john@example.com"}'
        result = decoder.decode(data=data, params=descriptor.params)

        assert "user" in result
        user = result["user"]
        assert isinstance(user, User)
        assert user.name == "John"
        assert user.age == 30
        assert user.email == "john@example.com"

    def test_decode_multiple_positional_only_fails(self):
        """Test that multiple positional-only parameters fail during setup."""
        descriptor = create_descriptor(
            "multi_positional_func",
            [
                create_param_detail("name", str, kind=ParamKind.POSITIONAL_ONLY),
                create_param_detail("age", int, kind=ParamKind.POSITIONAL_ONLY),
            ],
            str,
        )

        with pytest.raises(TypeError, match="All parameters in method .* must be keyword-compatible"):
            PydanticDecoder(descriptor)

    def test_decode_with_validation_error(self):
        """Test decoding with validation error."""
        descriptor = create_descriptor(
            "validated_func", [create_param_detail("age", Annotated[int, Field(ge=0, le=150)])], str
        )
        decoder = PydanticDecoder(descriptor)

        # Invalid data should raise validation error
        invalid_data = b'{"age": -5}'
        with pytest.raises(ValueError):
            decoder.decode(data=invalid_data, params=descriptor.params)

    def test_decoder_auto_analysis(self):
        """Test that decoder automatically analyzes method if not done."""
        descriptor = create_descriptor("test_func", [create_param_detail("name", str)], str)
        # Don't pre-analyze the method
        assert not has_param_fields(descriptor)
        assert not has_return_field(descriptor)

        decoder = PydanticDecoder(descriptor)

        # After creating decoder, method should be analyzed
        assert has_param_fields(descriptor)
        assert has_return_field(descriptor)

    def test_decode_with_json_validation_error(self):
        """Test decoding with invalid JSON."""
        decoder = PydanticDecoder(self.single_param_descriptor)

        with pytest.raises(ValueError):
            decoder.decode(data=b"invalid json", params=self.single_param_descriptor.params)


class TestPydanticCodec:
    """Tests for PydanticCodec complete encode/decode functionality."""

    def setup_method(self):
        """Setup common test data."""
        self.simple_descriptor = create_descriptor(
            "simple_func",
            [
                create_param_detail("name", str),
                create_param_detail("age", int),
            ],
            str,
        )

    def test_encoding_property(self):
        """Test that codec returns correct encoding format."""
        codec = PydanticCodec(self.simple_descriptor)
        assert codec.encoding == constants.JSON

    def test_codec_round_trip_simple(self):
        """Test round-trip encoding and decoding with simple types."""
        codec = PydanticCodec(self.simple_descriptor)

        # Original data
        original_data = {"name": "John", "age": 30}

        # Encode
        encoded = codec.encode(original_data, self.simple_descriptor.params)

        # Decode
        decoded = codec.decode(data=encoded, params=self.simple_descriptor.params)

        assert decoded == original_data

    def test_codec_round_trip_with_defaults(self):
        """Test round-trip with default values."""
        descriptor = create_descriptor(
            "func_with_defaults",
            [
                create_param_detail("name", str),
                create_param_detail("age", int, required=False, default=18),
                create_param_detail("active", bool, required=False, default=True),
            ],
            str,
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
        descriptor = create_descriptor(
            "user_address_func",
            [
                create_param_detail("user", User),
                create_param_detail("address", Address),
            ],
            UserWithAddress,
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
        descriptor = create_descriptor("func_with_aliases", [create_param_detail("data", UserWithAddress)], str)
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
        descriptor = create_descriptor(
            "validated_func", [create_param_detail("age", Annotated[int, Field(ge=0, le=150)])], str
        )
        codec = PydanticCodec(descriptor)

        # Test encoding validation error
        with pytest.raises(ValueError):
            codec.encode({"age": -5}, descriptor.params)

        with pytest.raises(ValueError):
            codec.encode({"age": 200}, descriptor.params)

        # Test decoding validation error
        invalid_data = b'{"age": -5}'
        with pytest.raises(ValueError):
            codec.decode(data=invalid_data, params=descriptor.params)


class TestEdgeCasesAndErrorHandling:
    """Tests for edge cases and error handling."""

    def test_empty_data_with_required_params(self):
        """Test empty data with required parameters."""
        descriptor = create_descriptor("required_param_func", [create_param_detail("name", str)], str)
        decoder = PydanticDecoder(descriptor)

        # Should raise error for empty data with required params
        with pytest.raises(ValueError):
            decoder.decode(data=b"{}", params=descriptor.params)

    def test_method_not_analyzed(self):
        """Test accessing fields from non-analyzed method."""
        descriptor = create_descriptor("unanalyzed_func", [create_param_detail("name", str)], str)

        # Should raise RuntimeError when trying to get param fields without analysis
        with pytest.raises(RuntimeError, match="has not been analyzed for pydantic parameters"):
            get_param_fields(descriptor)

        with pytest.raises(RuntimeError, match="has not been analyzed for pydantic return type"):
            get_return_field(descriptor)

    def test_invalid_param_kinds_for_analysis(self):
        """Test invalid parameter kinds during analysis."""
        descriptor = create_descriptor(
            "invalid_func",
            [
                create_param_detail("args", tuple, kind=ParamKind.VAR_POSITIONAL, required=False),
                create_param_detail("kwargs", dict, kind=ParamKind.VAR_KEYWORD, required=False),
            ],
            str,
        )

        # Should raise TypeError for invalid parameter kinds
        with pytest.raises(TypeError, match="Invalid parameter kind"):
            analyze_method(descriptor)

    def test_codec_factory_singleton(self):
        """Test that codec factory is a singleton."""
        factory1 = PydanticCodecFactory()
        factory2 = PydanticCodecFactory()
        assert factory1 is factory2


class TestCodecFactory:
    """Tests for CodecFactory implementation."""

    def setup_method(self):
        """Setup common test data."""
        self.factory = PydanticCodecFactory()
        self.descriptor = create_descriptor("test_func", [create_param_detail("x", str)], str)

    def test_create_encoder(self):
        """Test creating encoder from factory."""
        encoder = self.factory.create_encoder(self.descriptor)
        assert isinstance(encoder, PydanticEncoder)
        assert encoder.encoding == constants.JSON

    def test_create_decoder(self):
        """Test creating decoder from factory."""
        decoder = self.factory.create_decoder(self.descriptor)
        assert isinstance(decoder, PydanticDecoder)
        assert decoder.encoding == constants.JSON

    def test_create_codec(self):
        """Test creating codec from factory."""
        codec = self.factory.create_codec(self.descriptor)
        assert isinstance(codec, PydanticCodec)
        assert codec.encoding == constants.JSON


class TestAdvancedScenarios:
    """Tests for advanced scenarios and comprehensive coverage."""

    def test_model_field_default_property_deep_copy(self):
        """Test that ModelField.default property returns deep copies."""
        param = create_param_detail("list_param", list, required=False, default=[1, 2, 3])
        field = ModelField.from_param_detail(param, {})

        default1 = field.default
        default2 = field.default

        # Should be equal but not the same object
        assert default1 == default2
        assert default1 is not default2

    def test_model_field_from_param_with_complex_annotations(self):
        """Test ModelField creation with complex type annotations."""
        # Test with Union types
        param = create_param_detail("union_param", Optional[User])
        field = ModelField.from_param_detail(param, {})

        # Should handle None
        validated = field.validate_python(None)
        assert validated is None

        # Should handle User object
        user_data = {"name": "John", "age": 30}
        validated = field.validate_python(user_data)
        assert isinstance(validated, User)

    def test_create_merged_model_with_complex_fields(self):
        """Test create_merged_model with complex field configurations."""
        fields = {
            "required_str": ModelField.from_param_detail(create_param_detail("required_str", str), {}),
            "optional_int": ModelField.from_param_detail(
                create_param_detail("optional_int", Optional[int], required=False, default=None), {}
            ),
            "validated_field": ModelField.from_param_detail(
                create_param_detail("validated_field", Annotated[str, Field(min_length=1, max_length=50)]), {}
            ),
        }

        MergedModel = create_merged_model(fields)

        # Test with valid data
        instance = MergedModel(required_str="test", validated_field="valid")
        assert instance.required_str == "test"
        assert instance.optional_int is None
        assert instance.validated_field == "valid"

    def test_encoder_decoder_with_complex_nested_models(self):
        """Test encoder/decoder with deeply nested Pydantic models."""

        class NestedModel(BaseModel):
            inner: User
            settings: dict[str, Any] = {}

        descriptor = create_descriptor("complex_func", [create_param_detail("data", NestedModel)], str)

        codec = PydanticCodec(descriptor)

        # Test data with nested structure
        test_data = {
            "data": {"inner": {"name": "Alice", "age": 25}, "settings": {"theme": "dark", "notifications": True}}
        }

        # Round-trip test
        encoded = codec.encode(test_data, descriptor.params)
        decoded = codec.decode(data=encoded, params=descriptor.params)

        assert "data" in decoded
        nested = decoded["data"]
        assert isinstance(nested, NestedModel)
        assert isinstance(nested.inner, User)
        assert nested.inner.name == "Alice"
        assert nested.settings["theme"] == "dark"

    def test_error_handling_edge_cases(self):
        """Test various error handling edge cases."""
        # Test wrap_validation_errors with actual validation scenarios
        param = create_param_detail("complex_field", Annotated[str, Field(min_length=5)])
        field = ModelField.from_param_detail(param, {})

        # This should trigger wrap_validation_errors
        with pytest.raises(ValueError):
            field.validate_python("ab")  # Too short, will fail validation

    def test_merged_model_already_exists(self):
        """Test that encoder doesn't recreate merged model if it already exists."""
        descriptor = create_descriptor(
            "multi_param_func",
            [
                create_param_detail("name", str),
                create_param_detail("age", int),
            ],
            str,
        )

        # Create encoder - should create merged model
        encoder1 = PydanticEncoder(descriptor)
        assert has_merged_model(descriptor)

        # Get the merged model
        original_model, _ = get_merged_model(descriptor)

        # Create another encoder - should reuse existing merged model
        encoder2 = PydanticEncoder(descriptor)
        reused_model, _ = get_merged_model(descriptor)

        assert original_model is reused_model

    def test_param_kind_edge_cases(self):
        """Test parameter kind edge cases."""
        # Test KEYWORD_ONLY parameter
        descriptor = create_descriptor(
            "keyword_only_func", [create_param_detail("name", str, kind=ParamKind.KEYWORD_ONLY)], str
        )

        codec = PydanticCodec(descriptor)

        # Test encoding/decoding
        data = {"name": "test"}
        encoded = codec.encode(data, descriptor.params)
        decoded = codec.decode(data=encoded, params=descriptor.params)
        assert decoded == data
