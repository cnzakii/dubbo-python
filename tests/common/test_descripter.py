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

import inspect
from typing import Any, Optional, Union
from unittest.mock import Mock

import pytest

from src.dubbo.common.classes import CallType
from src.dubbo.common.descriptor import (
    MethodDescriptor,
    ParamDetail,
    ParamKind,
    ReturnParamDetail,
    _get_params_dict,
    _validate_param_kinds_uniformity,
    get_method_descriptor,
)


# Test fixtures
@pytest.fixture
def sample_function():
    """Fixture providing a sample function for testing."""

    def sample_func(param1: str, param2: int = 10) -> bool:
        return True

    return sample_func


@pytest.fixture
def complex_function():
    """Fixture providing a complex function signature for testing."""

    def complex_func(
        required_param: str,
        optional_param: int = 42,
        *,  # keyword-only parameters after this
        keyword_only: bool = True,
    ) -> Optional[dict[str, Any]]:
        return {"result": "success"}

    return complex_func


@pytest.fixture
def param_details_list():
    """Fixture providing a list of ParamDetail objects."""
    return [
        ParamDetail("param1", str, ParamKind.POSITIONAL_OR_KEYWORD),
        ParamDetail("param2", int, ParamKind.POSITIONAL_OR_KEYWORD, required=False, default=10),
    ]


@pytest.fixture
def return_param_detail():
    """Fixture providing a ReturnParamDetail object."""
    return ReturnParamDetail(annotation=bool)


class TestParamDetail:
    """Test cases for ParamDetail dataclass."""

    def test_param_detail_creation(self):
        """Test basic ParamDetail creation."""
        param = ParamDetail(
            name="test_param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD, required=True, default=None
        )

        assert param.name == "test_param"
        assert param.annotation is str
        assert param.kind == "positional_or_keyword"
        assert param.required is True
        assert param.default is None

    def test_param_detail_with_default(self):
        """Test ParamDetail with default value."""
        param = ParamDetail(
            name="optional_param", annotation=int, kind=ParamKind.POSITIONAL_OR_KEYWORD, required=False, default=42
        )

        assert param.name == "optional_param"
        assert param.annotation is int
        assert param.required is False
        assert param.default == 42

    def test_param_detail_defaults(self):
        """Test ParamDetail default values."""
        param = ParamDetail(name="simple_param", annotation=bool, kind=ParamKind.KEYWORD_ONLY)

        assert param.required is True
        assert param.default is None

    def test_param_detail_edge_cases(self):
        """Test ParamDetail with various edge cases."""
        # Test with complex type annotations
        param = ParamDetail(
            name="complex_param",
            annotation=Optional[list[dict[str, Any]]],
            kind=ParamKind.KEYWORD_ONLY,
            required=False,
            default=[],
        )

        assert param.annotation == Optional[list[dict[str, Any]]]
        assert param.default == []

    def test_param_detail_str_representation(self):
        """Test ParamDetail string representation."""
        param = ParamDetail(name="test_param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)

        # Should not raise any exceptions when converting to string
        str_repr = str(param)
        assert "test_param" in str_repr

    @pytest.mark.parametrize(
        "annotation,expected_type",
        [
            (str, str),
            (int, int),
            (list[str], list[str]),
            (Optional[dict[str, Any]], Optional[dict[str, Any]]),
            (Union[str, int], Union[str, int]),
        ],
    )
    def test_param_detail_type_annotations(self, annotation, expected_type):
        """Parametrized test for various type annotations."""
        param = ParamDetail(name="test_param", annotation=annotation, kind=ParamKind.POSITIONAL_OR_KEYWORD)
        assert param.annotation == expected_type

    @pytest.mark.parametrize(
        "kind", [ParamKind.POSITIONAL_OR_KEYWORD, ParamKind.POSITIONAL_ONLY, ParamKind.KEYWORD_ONLY]
    )
    def test_param_detail_kinds(self, kind):
        """Parametrized test for parameter kinds."""
        param = ParamDetail(name="test_param", annotation=str, kind=kind)
        assert param.kind == kind


class TestReturnParamDetail:
    """Test cases for ReturnParamDetail dataclass."""

    def test_return_param_detail_creation(self):
        """Test ReturnParamDetail creation."""
        return_param = ReturnParamDetail(annotation=str, required=True)

        assert return_param.name == "return"
        assert return_param.annotation is str
        assert return_param.kind == ParamKind.RETURN
        assert return_param.required is True

    def test_return_param_detail_name_fixed(self):
        """Test that ReturnParamDetail name is always 'return'."""
        return_param = ReturnParamDetail(annotation=int)
        assert return_param.name == "return"

    def test_return_param_detail_complex_types(self):
        """Test ReturnParamDetail with complex type annotations."""
        return_param = ReturnParamDetail(annotation=Optional[dict[str, list[int]]])

        assert return_param.annotation == Optional[dict[str, list[int]]]
        assert return_param.name == "return"


class TestMethodDescriptor:
    """Test cases for MethodDescriptor dataclass."""

    def test_method_descriptor_creation(self, param_details_list, return_param_detail):
        """Test basic MethodDescriptor creation."""
        mock_func = Mock()
        params = param_details_list
        return_param = return_param_detail
        attributes = {"custom_attr": "value"}

        descriptor = MethodDescriptor(
            name="test_method",
            call=mock_func,
            call_type=CallType.UNARY,
            params=params,
            return_param=return_param,
            attributes=attributes,
        )

        assert descriptor.name == "test_method"
        assert descriptor.call == mock_func
        assert descriptor.call_type == CallType.UNARY
        assert len(descriptor.params) == 2
        assert descriptor.return_param == return_param
        assert descriptor.attributes == attributes

    def test_method_descriptor_without_callable(self):
        """Test MethodDescriptor creation without a callable."""
        params = [ParamDetail("param1", str, ParamKind.POSITIONAL_OR_KEYWORD)]
        return_param = ReturnParamDetail(annotation=int)

        descriptor = MethodDescriptor(
            name="abstract_method",
            call=None,
            call_type=CallType.SERVER_STREAM,
            params=params,
            return_param=return_param,
            attributes={},
        )

        assert descriptor.call is None
        assert descriptor.name == "abstract_method"

    def test_method_descriptor_empty_params(self):
        """Test MethodDescriptor with no parameters."""
        return_param = ReturnParamDetail(annotation=str)

        descriptor = MethodDescriptor(
            name="no_params_method",
            call=lambda: "result",
            call_type=CallType.UNARY,
            params=[],
            return_param=return_param,
            attributes={},
        )

        assert len(descriptor.params) == 0
        assert descriptor.return_param.annotation is str


class TestGetParamsdict:
    """Test cases for _get_params_dict helper function."""

    def test_get_params_dict_with_dict(self):
        """Test _get_params_dict with dictionary input."""
        params = {"param1": str, "param2": int}
        result = _get_params_dict(params)
        assert result == params

    def test_get_params_dict_with_list(self):
        """Test _get_params_dict with list input."""
        params = [str, int, bool]
        result = _get_params_dict(params)
        expected = {"param_0": str, "param_1": int, "param_2": bool}
        assert result == expected

    def test_get_params_dict_with_single_type(self):
        """Test _get_params_dict with single type input."""
        result = _get_params_dict(str)
        expected = {"param_0": str}
        assert result == expected

    def test_get_params_dict_with_none(self):
        """Test _get_params_dict with None input."""
        result = _get_params_dict(None)
        assert result is None

    def test_get_params_dict_with_empty_list(self):
        """Test _get_params_dict with empty list."""
        result = _get_params_dict([])
        assert result == {}

    def test_get_params_dict_with_empty_dict(self):
        """Test _get_params_dict with empty dictionary."""
        result = _get_params_dict({})
        assert result == {}

    def test_get_params_dict_with_complex_types(self):
        """Test _get_params_dict with complex type annotations."""
        params = [Optional[str], list[int], dict[str, Any]]
        result = _get_params_dict(params)
        expected = {"param_0": Optional[str], "param_1": list[int], "param_2": dict[str, Any]}
        assert result == expected


class TestValidateParamKindsUniformity:
    """Test cases for _validate_param_kinds_uniformity helper function."""

    def test_validate_uniform_positional_or_keyword(self):
        """Test validation with uniform positional_or_keyword parameters."""
        params = [
            ParamDetail("param1", str, ParamKind.POSITIONAL_OR_KEYWORD),
            ParamDetail("param2", int, ParamKind.POSITIONAL_OR_KEYWORD),
        ]
        # Should not raise any exception
        _validate_param_kinds_uniformity(params)

    def test_validate_uniform_positional_only(self):
        """Test validation with uniform positional_only parameters."""
        params = [
            ParamDetail("param1", str, ParamKind.POSITIONAL_ONLY),
            ParamDetail("param2", int, ParamKind.POSITIONAL_ONLY),
        ]
        # Should not raise any exception
        _validate_param_kinds_uniformity(params)

    def test_validate_uniform_keyword_only(self):
        """Test validation with uniform keyword_only parameters."""
        params = [
            ParamDetail("param1", str, ParamKind.KEYWORD_ONLY),
            ParamDetail("param2", int, ParamKind.KEYWORD_ONLY),
        ]
        # Should not raise any exception
        _validate_param_kinds_uniformity(params)

    def test_validate_mixed_positional_and_keyword_only_fails(self):
        """Test validation fails with mixed positional_only and keyword_only."""
        params = [
            ParamDetail("param1", str, ParamKind.POSITIONAL_ONLY),
            ParamDetail("param2", int, ParamKind.KEYWORD_ONLY),
        ]
        with pytest.raises(TypeError, match="Cannot mix positional-only and keyword-only parameters"):
            _validate_param_kinds_uniformity(params)

    def test_validate_unsupported_kind_fails(self):
        """Test validation fails with unsupported parameter kinds."""
        params = [
            ParamDetail("param1", str, ParamKind.VAR_POSITIONAL),
            ParamDetail("param2", int, ParamKind.POSITIONAL_OR_KEYWORD),
        ]
        with pytest.raises(TypeError, match="Unsupported parameter kind"):
            _validate_param_kinds_uniformity(params)

    def test_validate_empty_params(self):
        """Test validation with empty parameter list."""
        # Should not raise any exception
        _validate_param_kinds_uniformity([])

    def test_validate_single_param(self):
        """Test validation with single parameter."""
        params = [ParamDetail("single", str, ParamKind.POSITIONAL_OR_KEYWORD)]
        _validate_param_kinds_uniformity(params)

    def test_validate_var_keyword_fails(self):
        """Test validation fails with VAR_KEYWORD parameter kind."""
        params = [ParamDetail("kwargs", dict, ParamKind.VAR_KEYWORD)]
        with pytest.raises(TypeError, match="Unsupported parameter kind"):
            _validate_param_kinds_uniformity(params)


class TestGetMethodDescriptor:
    """Test cases for get_method_descriptor function."""

    def test_get_method_descriptor_from_function(self, sample_function):
        """Test creating MethodDescriptor from a function."""
        descriptor = get_method_descriptor(CallType.UNARY, sample_function)

        assert descriptor.name == "sample_func"
        assert descriptor.call == sample_function
        assert descriptor.call_type == CallType.UNARY
        assert len(descriptor.params) == 2

        # Check first parameter
        assert descriptor.params[0].name == "param1"
        assert descriptor.params[0].annotation is str
        assert descriptor.params[0].required is True

        # Check second parameter
        assert descriptor.params[1].name == "param2"
        assert descriptor.params[1].annotation is int
        assert descriptor.params[1].required is False
        assert descriptor.params[1].default == 10

        # Check return type
        assert descriptor.return_param.annotation is bool

    def test_get_method_descriptor_with_explicit_name(self):
        """Test creating MethodDescriptor with explicit name."""

        def sample_func():
            pass

        descriptor = get_method_descriptor(CallType.CLIENT_STREAM, sample_func, name="custom_name")

        assert descriptor.name == "custom_name"

    def test_get_method_descriptor_with_params_dict(self):
        """Test creating MethodDescriptor with explicit params dict."""
        params = {"user_id": str, "count": int}

        descriptor = get_method_descriptor(
            CallType.SERVER_STREAM, name="test_method", param_types=params, return_type=list[str]
        )

        assert descriptor.name == "test_method"
        assert len(descriptor.params) == 2
        assert descriptor.params[0].name == "user_id"
        assert descriptor.params[0].annotation is str
        assert descriptor.params[1].name == "count"
        assert descriptor.params[1].annotation is int
        assert descriptor.return_param.annotation == list[str]

    def test_get_method_descriptor_with_params_list(self):
        """Test creating MethodDescriptor with params list."""
        params = [str, int, bool]

        descriptor = get_method_descriptor(
            CallType.BI_STREAM, name="test_method", param_types=params, return_type=dict[str, Any]
        )

        assert descriptor.name == "test_method"
        assert len(descriptor.params) == 3
        assert descriptor.params[0].name == "param_0"
        assert descriptor.params[0].annotation is str
        assert descriptor.params[1].name == "param_1"
        assert descriptor.params[1].annotation is int
        assert descriptor.params[2].name == "param_2"
        assert descriptor.params[2].annotation is bool

    def test_get_method_descriptor_with_single_param_type(self):
        """Test creating MethodDescriptor with single param type."""
        descriptor = get_method_descriptor(CallType.UNARY, name="simple_method", param_types=str, return_type=int)

        assert descriptor.name == "simple_method"
        assert len(descriptor.params) == 1
        assert descriptor.params[0].name == "param_0"
        assert descriptor.params[0].annotation is str

    def test_get_method_descriptor_with_attributes(self):
        """Test creating MethodDescriptor with custom attributes."""
        attributes = {"version": "1.0", "deprecated": False}

        descriptor = get_method_descriptor(
            CallType.UNARY, name="attributed_method", param_types=str, attributes=attributes
        )

        assert descriptor.attributes == attributes

    def test_get_method_descriptor_function_no_annotations(self):
        """Test creating MethodDescriptor from function without annotations."""

        def no_annotations_func(param1, param2=None):
            return "result"

        descriptor = get_method_descriptor(CallType.UNARY, no_annotations_func)

        assert descriptor.name == "no_annotations_func"
        assert len(descriptor.params) == 2
        assert descriptor.params[0].annotation == Any
        assert descriptor.params[1].annotation == Any
        assert descriptor.return_param.annotation == Any

    def test_get_method_descriptor_param_count_mismatch(self):
        """Test error when param count doesn't match function signature."""

        def sample_func(param1, param2):
            pass

        params = {"only_one": str}  # Function has 2 params, but we provide 1

        with pytest.raises(ValueError, match="parameter count mismatch"):
            get_method_descriptor(CallType.UNARY, sample_func, param_types=params)

    def test_get_method_descriptor_missing_name_and_func(self):
        """Test error when neither name nor function is provided."""
        with pytest.raises(ValueError, match="Method name must be provided"):
            get_method_descriptor(CallType.UNARY)

    def test_get_method_descriptor_missing_params_and_func(self):
        """Test error when neither params nor function is provided."""
        with pytest.raises(ValueError, match="Must provide either 'params' or a 'func'"):
            get_method_descriptor(CallType.UNARY, name="test_method")

    def test_get_method_descriptor_return_param_override(self):
        """Test overriding return type from function signature."""

        def sample_func() -> str:
            return "test"

        descriptor = get_method_descriptor(CallType.UNARY, sample_func, return_type=int)

        assert descriptor.return_param.annotation is int

    def test_get_method_descriptor_with_none_params(self):
        """Test creating MethodDescriptor with None params and explicit func."""

        def no_param_func():
            return "result"

        descriptor = get_method_descriptor(CallType.UNARY, no_param_func, name="no_param_method")

        assert descriptor.name == "no_param_method"
        assert len(descriptor.params) == 0

    def test_get_method_descriptor_complex_function_signature(self):
        """Test with complex function signatures including keyword-only parameters."""

        def complex_func(a: int, b: str = "default", *, c: bool = True, d: float) -> dict[str, Any]:
            return {"a": a, "b": b, "c": c, "d": d}

        descriptor = get_method_descriptor(CallType.UNARY, complex_func)

        assert descriptor.name == "complex_func"
        assert len(descriptor.params) == 4

        # Check positional parameter
        assert descriptor.params[0].name == "a"
        assert descriptor.params[0].annotation is int
        assert descriptor.params[0].required is True

        # Check positional with default
        assert descriptor.params[1].name == "b"
        assert descriptor.params[1].annotation is str
        assert descriptor.params[1].required is False
        assert descriptor.params[1].default == "default"

        # Check keyword-only with default
        assert descriptor.params[2].name == "c"
        assert descriptor.params[2].annotation is bool
        assert descriptor.params[2].required is False
        assert descriptor.params[2].default is True
        assert descriptor.params[2].kind == ParamKind.KEYWORD_ONLY

        # Check keyword-only required
        assert descriptor.params[3].name == "d"
        assert descriptor.params[3].annotation is float
        assert descriptor.params[3].required is True
        assert descriptor.params[3].kind == ParamKind.KEYWORD_ONLY

    def test_get_method_descriptor_lambda_function(self):
        """Test creating MethodDescriptor from lambda function."""

        def _func(x: int) -> str:
            return str(x)

        descriptor = get_method_descriptor(CallType.UNARY, _func, name="lambda_method")

        assert descriptor.name == "lambda_method"
        assert descriptor.call == _func
        assert len(descriptor.params) == 1

    def test_get_method_descriptor_with_mock_function(self):
        """Test creating MethodDescriptor with mock function."""
        from unittest.mock import patch

        mock_func = Mock()
        mock_func.__name__ = "mock_method"

        # Create a mock signature
        param1 = inspect.Parameter("param1", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)
        param2 = inspect.Parameter("param2", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=int, default=10)
        sig = inspect.Signature([param1, param2], return_annotation=bool)

        with patch("inspect.signature", return_value=sig):
            descriptor = get_method_descriptor(CallType.UNARY, mock_func)

        assert descriptor.name == "mock_method"
        assert descriptor.call == mock_func
        assert len(descriptor.params) == 2


class TestDescriptorIntegration:
    """Integration tests for descriptor functionality."""

    def test_complete_workflow_with_complex_function(self, complex_function):
        """Test complete workflow with a complex function signature."""
        descriptor = get_method_descriptor(CallType.UNARY, complex_function)

        assert descriptor.name == "complex_func"
        assert len(descriptor.params) == 3

        # Required parameter
        assert descriptor.params[0].name == "required_param"
        assert descriptor.params[0].required is True

        # Optional parameter
        assert descriptor.params[1].name == "optional_param"
        assert descriptor.params[1].required is False
        assert descriptor.params[1].default == 42

        # Keyword-only parameter
        assert descriptor.params[2].name == "keyword_only"
        assert descriptor.params[2].required is False
        assert descriptor.params[2].default is True
        assert descriptor.params[2].kind == ParamKind.KEYWORD_ONLY

    def test_all_call_types(self):
        """Test descriptor creation with all call types."""

        def sample_func():
            pass

        for call_type in CallType:
            descriptor = get_method_descriptor(call_type, sample_func)
            assert descriptor.call_type == call_type

    @pytest.mark.parametrize(
        "call_type", [CallType.UNARY, CallType.CLIENT_STREAM, CallType.SERVER_STREAM, CallType.BI_STREAM]
    )
    def test_parametrized_call_types(self, call_type):
        """Parametrized test for all call types."""
        descriptor = get_method_descriptor(call_type, name="test_method", param_types=str, return_type=int)
        assert descriptor.call_type == call_type

    def test_descriptor_with_various_annotations(self):
        """Test descriptor creation with various type annotations."""

        def annotated_func(
            basic_str: str,
            basic_int: int,
            optional_type: Optional[str],
            list_type: list[int],
            dict_type: dict[str, Any],
            union_type: Union[str, int],
        ) -> Optional[list[dict[str, Any]]]:
            return None

        descriptor = get_method_descriptor(CallType.UNARY, annotated_func)

        assert len(descriptor.params) == 6
        assert descriptor.params[0].annotation is str
        assert descriptor.params[1].annotation is int
        assert descriptor.params[2].annotation == Optional[str]
        assert descriptor.params[3].annotation == list[int]
        assert descriptor.params[4].annotation == dict[str, Any]
        assert descriptor.params[5].annotation == Union[str, int]
        assert descriptor.return_param.annotation == Optional[list[dict[str, Any]]]

    def test_descriptor_serialization_compatibility(self):
        """Test that descriptors can be created consistently for serialization."""

        def test_method(data: dict[str, Any]) -> list[str]:
            return list(data.keys())

        # Create descriptor from function
        desc1 = get_method_descriptor(CallType.UNARY, test_method)

        # Create descriptor from explicit parameters
        desc2 = get_method_descriptor(
            CallType.UNARY, name="test_method", param_types={"data": dict[str, Any]}, return_type=list[str]
        )

        # They should have equivalent structure
        assert desc1.name == desc2.name
        assert desc1.call_type == desc2.call_type
        assert len(desc1.params) == len(desc2.params)
        assert desc1.params[0].name == desc2.params[0].name
        assert desc1.params[0].annotation == desc2.params[0].annotation
        assert desc1.return_param.annotation == desc2.return_param.annotation

    def test_edge_case_empty_function(self):
        """Test descriptor creation with function having no parameters or return annotation."""

        def empty_func():
            pass

        descriptor = get_method_descriptor(CallType.UNARY, empty_func)

        assert descriptor.name == "empty_func"
        assert len(descriptor.params) == 0
        assert descriptor.return_param.annotation == Any

    def test_descriptor_with_callable_objects(self):
        """Test descriptor creation with callable objects."""

        class CallableClass:
            def __call__(self, param: str) -> int:
                return len(param)

        callable_obj = CallableClass()

        descriptor = get_method_descriptor(CallType.UNARY, callable_obj, name="callable_method")

        assert descriptor.name == "callable_method"
        assert descriptor.call == callable_obj

    @pytest.mark.parametrize(
        "param_format,expected_count",
        [
            (str, 1),
            ([str, int], 2),
            ({"name": str, "age": int}, 2),
            ({}, 0),
            ([], 0),
        ],
    )
    def test_parametrized_param_formats(self, param_format, expected_count):
        """Parametrized test for different parameter formats."""
        descriptor = get_method_descriptor(
            CallType.UNARY, name="test_method", param_types=param_format, return_type=str
        )
        assert len(descriptor.params) == expected_count


class TestDescriptorErrorHandling:
    """Test error handling and edge cases in descriptor functionality."""

    def test_function_with_varargs_fails(self):
        """Test that functions with *args raise appropriate errors."""

        def varargs_func(*args):
            pass

        # This should fail since *args is not supported
        with pytest.raises(TypeError, match="Unsupported parameter kind"):
            get_method_descriptor(CallType.UNARY, varargs_func)

    def test_function_with_kwargs_fails(self):
        """Test that functions with **kwargs raise appropriate errors."""

        def kwargs_func(**kwargs):
            pass

        # This should fail since **kwargs is not supported
        with pytest.raises(TypeError, match="Unsupported parameter kind"):
            get_method_descriptor(CallType.UNARY, kwargs_func)

    def test_inconsistent_parameter_kinds_error(self):
        """Test error when mixing incompatible parameter kinds."""
        params = [
            ParamDetail("pos_only", str, ParamKind.POSITIONAL_ONLY),
            ParamDetail("kw_only", int, ParamKind.KEYWORD_ONLY),
        ]

        with pytest.raises(TypeError, match="Cannot mix positional-only and keyword-only"):
            _validate_param_kinds_uniformity(params)

    def test_invalid_call_type(self):
        """Test descriptor creation with various call types."""

        # This test ensures all CallType values are supported
        def test_func():
            pass

        for call_type in CallType:
            descriptor = get_method_descriptor(call_type, test_func)
            assert descriptor.call_type == call_type

    def test_param_detail_immutability(self):
        """Test that ParamDetail behaves correctly as a dataclass."""
        param = ParamDetail("test", str, ParamKind.POSITIONAL_OR_KEYWORD)

        # Test that we can access attributes
        assert param.name == "test"
        assert param.annotation is str
        assert param.kind == "positional_or_keyword"
        assert param.required is True  # default value
        assert param.default is None  # default value

    def test_method_descriptor_with_none_attributes(self):
        """Test MethodDescriptor creation with None attributes."""
        descriptor = get_method_descriptor(CallType.UNARY, name="test_method", param_types=str, attributes=None)

        assert descriptor.attributes == {}

    def test_return_param_detail_inheritance(self):
        """Test that ReturnParamDetail properly inherits from ParamDetail."""
        return_param = ReturnParamDetail(annotation=str)

        # Should have all ParamDetail attributes
        assert hasattr(return_param, "name")
        assert hasattr(return_param, "annotation")
        assert hasattr(return_param, "kind")
        assert hasattr(return_param, "required")
        assert hasattr(return_param, "default")

        # Name should always be "return"
        assert return_param.name == "return"


# Performance and stress tests
class TestDescriptorPerformance:
    """Performance-related tests for descriptor functionality."""

    def test_descriptor_creation_with_many_params(self):
        """Test descriptor creation with a large number of parameters."""
        # Create a function with many parameters
        param_count = 50
        params = {f"param_{i}": str for i in range(param_count)}

        descriptor: MethodDescriptor = get_method_descriptor(
            CallType.UNARY, name="many_params_method", param_types=params, return_type=dict
        )

        assert len(descriptor.params) == param_count
        assert all(param.annotation is str for param in descriptor.params)

    def test_descriptor_with_deeply_nested_types(self):
        """Test descriptor creation with complex nested type annotations."""
        complex_type = dict[str, list[Optional[dict[str, Union[int, str]]]]]

        descriptor = get_method_descriptor(
            CallType.UNARY, name="complex_type_method", param_types={"data": complex_type}, return_type=complex_type
        )

        assert descriptor.params[0].annotation == complex_type
        assert descriptor.return_param.annotation == complex_type
