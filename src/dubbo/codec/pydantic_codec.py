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
from dataclasses import dataclass
from typing import Annotated, Any, ForwardRef, Optional, Union

from pydantic import BaseModel, TypeAdapter, create_model
from pydantic._internal._typing_extra import try_eval_type
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from typing_extensions import get_args, get_origin

from dubbo.common import constants
from dubbo.common.classes import SingletonBase
from dubbo.common.descriptor import MethodDescriptor, ParamDetail, ParamKind

from .base import Codec, CodecFactory, Decoder, Encoder

__all__ = ["PydanticCodec", "PydanticCodecFactory", "PydanticEncoder", "PydanticDecoder"]


_JSON_NAME = "json"
_PYDANTIC_PARAM_FIELDS = "pydantic_param_fields"
_PYDANTIC_RETURN_FIELD = "pydantic_return_field"
_PYDANTIC_MERGED_MODEL = "pydantic_merged_model"


@dataclass
class ModelField:
    """
    Represents a field in a Pydantic model.
    """

    name: str
    info: FieldInfo
    raw_detail: ParamDetail
    adapter: TypeAdapter

    @property
    def default(self) -> Any:
        """
        Get the default value of the field.
        If the field is required, return PydanticUndefined.
        """
        if self.info.is_required():
            # If the field is required, return PydanticUndefined to indicate no default value
            return PydanticUndefined
        # If the field is not required, return the default value
        return copy.deepcopy(self.info.get_default(call_default_factory=True))

    @classmethod
    def from_param_detail(cls, param: ParamDetail, globalns: dict[str, Any]) -> "ModelField":
        """
        Create a ModelField from a ParamDetail.
        """
        annotation = param.annotation
        value = param.default
        field_info: Optional[FieldInfo] = None
        # If the annotation is a string, try to evaluate it (e.g., "User" -> User)
        if isinstance(annotation, str):
            annotation, _ = try_eval_type(ForwardRef(annotation), globalns, globalns)

        # If the annotation is an Annotated type, extract the type
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            annotation = args[0]
            for meta in reversed(args[1:]):
                if isinstance(meta, FieldInfo):
                    # If the meta is a FieldInfo, use it directly
                    field_info = meta
                    break

        # build the field info
        if field_info is not None:
            # If we have a FieldInfo, use it as the field info
            field_info.annotation = annotation
        elif isinstance(value, FieldInfo):
            field_info = value
            field_info.annotation = annotation
        else:
            field_info = FieldInfo(
                annotation=annotation,
                default=value if not param.required else Ellipsis,
            )

        # create a ModelField instance
        return ModelField(
            name=param.name, info=field_info, raw_detail=param, adapter=TypeAdapter(Annotated[annotation, field_info])
        )


def create_merged_model(fields: dict[str, ModelField]) -> type[BaseModel]:
    """
    Create a Pydantic model with the given fields.
    This is used to merge multiple parameters into a single model.
    """
    # Create a dictionary of field names and their corresponding TypeAdapter
    model_fields: dict[str, tuple[Any, FieldInfo]] = {}
    for field in fields.values():
        if field.raw_detail.kind not in (
            ParamKind.POSITIONAL_OR_KEYWORD,
            ParamKind.KEYWORD_ONLY,
            ParamKind.POSITIONAL_ONLY,
        ):
            raise TypeError(
                f"Parameters in method must be POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD or KEYWORD_ONLY, "
                f"but got {field.raw_detail.kind} for parameter '{field.name}'"
            )
        annotation = field.info.annotation or field.raw_detail.annotation
        model_fields[field.name] = (annotation, field.info)

    # Create the Pydantic model dynamically
    return create_model("MergedModel", **model_fields)  # type: ignore


def analyze_method(descriptor: MethodDescriptor) -> None:
    """
    Analyze the method descriptor to extract parameter and return parameter information
    """
    params = descriptor.params
    func = descriptor.call

    globalns = getattr(func, "__globalns__", {})

    # Extract the parameters and their default values
    param_fields: dict[str, ModelField] = {}
    for param in params:
        if param.kind not in (ParamKind.POSITIONAL_OR_KEYWORD, ParamKind.KEYWORD_ONLY, ParamKind.POSITIONAL_ONLY):
            raise TypeError(
                f"Parameters in method {descriptor.name} must be "
                f"POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD or KEYWORD_ONLY, "
                f"but got {param.kind} for parameter '{param.name}'"
            )
        # create a ModelField from the ParamDetail
        param_fields[param.name] = ModelField.from_param_detail(param, globalns)

    # Extract the return parameter
    return_field: ModelField = ModelField.from_param_detail(descriptor.return_param, globalns)

    # Store the analyzed parameters and return type in the descriptor attributes
    descriptor.attributes[_PYDANTIC_PARAM_FIELDS] = param_fields
    descriptor.attributes[_PYDANTIC_RETURN_FIELD] = return_field


def get_param_fields(descriptor: MethodDescriptor) -> dict[str, ModelField]:
    """
    Get the parameter fields from the method descriptor
    Args:
        descriptor (MethodDescriptor): The method descriptor to get the parameter fields from.
    Returns:
        dict[str, ModelField]: A dictionary mapping parameter names to ModelField instances.
    Raises:
        TypeError: If the method descriptor has not been analyzed for pydantic parameters.
    """
    try:
        return descriptor.attributes[_PYDANTIC_PARAM_FIELDS]
    except KeyError:
        raise TypeError(
            f"Method {descriptor.name} has not been analyzed for pydantic parameters. "
            "Please call `analyze_method` first."
        )


def get_return_field(descriptor: MethodDescriptor) -> ModelField:
    """
    Get the return field from the method descriptor
    Args:
        descriptor (MethodDescriptor): The method descriptor to get the return field from.
    Returns:
        ModelField: The ModelField instance representing the return type of the method.
    Raises:
        TypeError: If the method descriptor has not been analyzed for pydantic return type.
    """
    try:
        return descriptor.attributes[_PYDANTIC_RETURN_FIELD]
    except KeyError:
        raise TypeError(
            f"Method {descriptor.name} has not been analyzed for pydantic return type. "
            "Please call `analyze_method` first."
        )


def get_merged_model_info(descriptor: MethodDescriptor) -> tuple[type[BaseModel], TypeAdapter]:
    """
    Get the merged model and its TypeAdapter from the method descriptor.
    Args:
        descriptor (MethodDescriptor): The method descriptor to get the merged model from.
    Returns:
        tuple[type[BaseModel], TypeAdapter]: A tuple containing the merged model and its TypeAdapter.
    Raises:
        TypeError: If the method descriptor has not been analyzed for pydantic merged model.
    """
    try:
        return descriptor.attributes[_PYDANTIC_MERGED_MODEL]
    except KeyError:
        raise TypeError(f"Method {descriptor.name} has not been analyzed for pydantic merged model. ")


class PydanticEncoder(Encoder):
    __slots__ = ("_descriptor",)

    _descriptor: MethodDescriptor

    @property
    def encoding(self) -> str:
        """Get the encoding format used by this encoder."""
        return _JSON_NAME

    def __init__(self, descriptor: MethodDescriptor) -> None:
        self._descriptor = descriptor

        attributes = descriptor.attributes

        if _PYDANTIC_PARAM_FIELDS not in attributes or _PYDANTIC_RETURN_FIELD not in attributes:
            # If the attributes are not set, analyze the method to extract parameter fields
            analyze_method(descriptor)

        if len(descriptor.params) > 1:
            # If the method has multiple parameters, create a merged model
            merged_model_info = attributes.get(_PYDANTIC_MERGED_MODEL)

            if merged_model_info is None:
                param_fields: dict[str, ModelField] = get_param_fields(descriptor)
                merged_model = create_merged_model(param_fields)
                type_adapter: TypeAdapter = TypeAdapter(merged_model)

                attributes[_PYDANTIC_MERGED_MODEL] = (merged_model, type_adapter)

    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """
        Encode the given values based on parameter metadata into a serialized byte representation.
        """
        params_len = len(params)

        # Only check length for list inputs, dict inputs can have fewer values (using defaults)
        if isinstance(values, list) and params_len != len(values):
            raise ValueError("Number of parameters does not match number of values provided for encoding.")

        if params_len == 0:
            return b""
        elif params_len == 1:
            return self._encode_single_param(values, params)
        else:
            return self._encode_multiple_params(values, params)

    def _encode_single_param(self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail]) -> bytes:
        """Encode a single parameter into bytes."""
        # Analyze the parameters to get the field info and adapter
        param = params[0]
        if param.kind == ParamKind.RETURN:
            param_field = get_return_field(self._descriptor)
        else:
            param_field = get_param_fields(self._descriptor)[param.name]

        if isinstance(values, dict):
            value = values.get(param.name, PydanticUndefined)
        else:
            value = values[0] if isinstance(values, list) else values

        # Validate the value using the adapter
        adapter = param_field.adapter
        validated_value = adapter.validate_python(value)

        # Dump the value to JSON
        return adapter.dump_json(validated_value, by_alias=True)

    def _encode_multiple_params(self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail]) -> bytes:
        """Encode multiple parameters into bytes."""
        # get the merged model and its adapter
        MergedModel, type_adapter = get_merged_model_info(self._descriptor)

        # convert values to a dictionary if it's a list
        value_dict: dict[str, Any] = {}
        if isinstance(values, list):
            value_dict = {param.name: values[i] for i, param in enumerate(params)}
        else:
            value_dict = values

        # instantiate the merged model with the values
        merged_instance = MergedModel(**value_dict)

        # dump the merged instance to JSON
        return type_adapter.dump_json(merged_instance, by_alias=True)


class PydanticDecoder(Decoder):
    """
    PydanticDecoder decodes bytes into parameters using Pydantic models.
    It supports both single and multiple parameters.
    """

    __slots__ = ("_descriptor", "_dict_adapter")

    _descriptor: MethodDescriptor
    _dict_adapter: TypeAdapter

    def __init__(self, descriptor: MethodDescriptor) -> None:
        self._descriptor = descriptor
        if _PYDANTIC_PARAM_FIELDS not in descriptor.attributes or _PYDANTIC_RETURN_FIELD not in descriptor.attributes:
            # If the attributes are not set, analyze the method to extract parameter fields
            analyze_method(descriptor)
        self._dict_adapter: TypeAdapter = TypeAdapter(dict[str, Any])

    @property
    def encoding(self) -> str:
        """Get the encoding format used by this encoder."""
        return _JSON_NAME

    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """Decode bytes into positional (list) or keyword (dict) arguments based on parameter metadata."""
        params_len = len(params)
        if params_len == 0:
            if len(data) != 0:
                raise ValueError("No parameters provided for decoding, but data is not empty.")
            return []
        elif params_len == 1:
            return self._decode_single_param(data, params[0])
        else:
            return self._decode_multiple_params(data, params)

    def _decode_single_param(self, data: bytes, param: ParamDetail) -> Union[list[Any], dict[str, Any]]:
        """Decode a single parameter from bytes."""
        if param.kind == ParamKind.RETURN:
            param_field = get_return_field(self._descriptor)
        else:
            param_field = get_param_fields(self._descriptor)[param.name]

        # Validate the value using the adapter
        adapter = param_field.adapter
        validated_value = adapter.validate_json(data)

        # Return as a single-item list or dict based on the parameter kind
        if param.kind in (ParamKind.POSITIONAL_ONLY, ParamKind.RETURN):
            return [validated_value]
        return {param.name: validated_value}

    def _decode_multiple_params(self, data: bytes, params: list[ParamDetail]) -> dict[str, Any]:
        """Decode multiple parameters from bytes."""
        param_fields = get_param_fields(self._descriptor)

        # Decode the data into a dictionary
        decoded_dict = self._dict_adapter.validate_json(data)

        final_result: dict[str, Any] = {}
        for param_detail in params:
            field = param_fields[param_detail.name]

            raw_value = decoded_dict.get(param_detail.name, PydanticUndefined)

            if raw_value is PydanticUndefined:
                # If the value is not present, check if the field is required
                if not field.info.is_required():
                    # If not required, use the default value
                    value = copy.deepcopy(field.default)
                else:
                    # Raise a more specific error for missing required field
                    raise ValueError(f"Field {param_detail.name} is required but not provided.")
            else:
                # Validate the raw value using the adapter
                value = field.adapter.validate_python(raw_value)

            final_result[param_detail.name] = value
        return final_result


class PydanticCodec(Codec):
    """
    PydanticCodec combines PydanticEncoder and PydanticDecoder to handle multiple parameters in a method.
    It encodes and decodes multiple parameters using the actual encoder and decoder.
    """

    __slots__ = ("_encoder", "_decoder")

    def __init__(self, descriptor: MethodDescriptor) -> None:
        self._encoder = PydanticEncoder(descriptor)
        self._decoder = PydanticDecoder(descriptor)

    @property
    def encoding(self) -> str:
        """Get the encoding format used by this codec."""
        return _JSON_NAME

    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """Encode the given values based on parameter metadata into a serialized byte representation."""
        return self._encoder.encode(values, params, encoding=encoding)

    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """Decode bytes into positional (list) or keyword (dict) arguments based on parameter metadata."""
        return self._decoder.decode(data=data, params=params, encoding=encoding)


class PydanticCodecFactory(CodecFactory, SingletonBase):
    """PydanticCodecFactory"""

    def create_encoder(self, descriptor: MethodDescriptor) -> Encoder:
        """Create a PydanticEncoder for the given method descriptor."""
        return PydanticEncoder(descriptor)

    def create_decoder(self, descriptor: MethodDescriptor) -> Decoder:
        """Create a PydanticDecoder for the given method descriptor."""
        return PydanticDecoder(descriptor)

    def create_codec(self, descriptor: MethodDescriptor) -> Codec:
        """Create a PydanticCodec for the given method descriptor."""
        return PydanticCodec(descriptor)
