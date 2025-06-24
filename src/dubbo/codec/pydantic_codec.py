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
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any, ForwardRef, Optional, Union, cast

from pydantic import BaseModel, TypeAdapter, ValidationError, create_model
from pydantic._internal._typing_extra import try_eval_type
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from typing_extensions import get_args, get_origin

from dubbo.common import constants
from dubbo.common.classes import SingletonBase
from dubbo.common.descriptor import MethodDescriptor, ParamDetail, ParamKind

from .base import Codec, CodecFactory, Decoder, Encoder

__all__ = ["PydanticCodec", "PydanticCodecFactory", "PydanticEncoder", "PydanticDecoder"]


_PYDANTIC_PARAM_FIELDS = "pydantic_param_fields"
_PYDANTIC_RETURN_FIELD = "pydantic_return_field"
_PYDANTIC_MERGED_MODEL = "pydantic_merged_model"


def set_param_fields(descriptor: MethodDescriptor, param_fields: dict[str, "ModelField"]) -> None:
    """Set the parameter fields in the method descriptor."""
    descriptor.attributes[_PYDANTIC_PARAM_FIELDS] = param_fields


def has_param_fields(descriptor: MethodDescriptor) -> bool:
    """Check if the method descriptor has parameter fields set."""
    return _PYDANTIC_PARAM_FIELDS in descriptor.attributes


def get_param_fields(descriptor: MethodDescriptor) -> dict[str, "ModelField"]:
    """Get the parameter fields from the method descriptor."""
    param_fields = descriptor.attributes.get(_PYDANTIC_PARAM_FIELDS)
    if param_fields is None:
        raise RuntimeError(
            f"Method {descriptor.name} has not been analyzed for pydantic parameters. "
            "Please call `analyze_method` first."
        )
    return param_fields


def set_return_field(descriptor: MethodDescriptor, return_field: "ModelField") -> None:
    """Set the return field in the method descriptor."""
    descriptor.attributes[_PYDANTIC_RETURN_FIELD] = return_field


def has_return_field(descriptor: MethodDescriptor) -> bool:
    """Check if the method descriptor has a return field set."""
    return _PYDANTIC_RETURN_FIELD in descriptor.attributes


def get_return_field(descriptor: MethodDescriptor) -> "ModelField":
    """Get the return field from the method descriptor."""
    if return_field := descriptor.attributes.get(_PYDANTIC_RETURN_FIELD):
        return return_field
    raise RuntimeError(
        f"Method {descriptor.name} has not been analyzed for pydantic return type. Please call `analyze_method` first."
    )


def set_merged_model(descriptor: MethodDescriptor, merged_model: type[BaseModel], type_adapter: TypeAdapter) -> None:
    """Set the merged model and its TypeAdapter in the method descriptor."""
    descriptor.attributes[_PYDANTIC_MERGED_MODEL] = (merged_model, type_adapter)


def has_merged_model(descriptor: MethodDescriptor) -> bool:
    """Check if the method descriptor has a merged model set."""
    return _PYDANTIC_MERGED_MODEL in descriptor.attributes


def get_merged_model(descriptor: MethodDescriptor) -> tuple[type[BaseModel], TypeAdapter]:
    """Get the merged model and its TypeAdapter from the method descriptor."""
    if merged_model := descriptor.attributes.get(_PYDANTIC_MERGED_MODEL):
        return merged_model
    raise RuntimeError(f"Method {descriptor.name} has not been set a merged model.")


@contextmanager
def wrap_validation_errors(field_name: str = "<unknown>"):
    """Context manager to wrap validation-related exceptions with field context."""
    try:
        yield
    except ValidationError as e:
        lines = [f"Validation failed for field '{field_name}':"]
        for item in e.errors():
            loc = " -> ".join(str(i) for i in item.get("loc", [])) or "<value>"
            msg = item.get("msg", "Unknown error")
            typ = item.get("type", "unknown_type")
            lines.append(f"  - {loc}: {msg} [{typ}]")
        raise ValueError("\n".join(lines)) from e
    except (TypeError, ValueError):
        raise  # Preserve original TypeError/ValueError
    except Exception as e:
        raise ValueError(f"Unexpected error in field '{field_name}': {e}") from e


@dataclass(frozen=True)
class ModelField:
    """
    Represents a parameter or return value in a method, including metadata and
    a Pydantic TypeAdapter for validation and serialization.
    """

    name: str
    info: FieldInfo
    raw_detail: ParamDetail
    adapter: TypeAdapter

    @property
    def default(self) -> Any:
        """
        Return the default value of the field, or PydanticUndefined if required.
        """
        if self.info.is_required():
            return PydanticUndefined
        raw_default = self.info.get_default(call_default_factory=False)
        return copy.deepcopy(raw_default)

    def validate_python(self, value: Any) -> Any:
        """Validate a native Python value against the field's type."""
        with wrap_validation_errors(self.name):
            return self.adapter.validate_python(value)

    def validate_json(self, value: Union[str, bytes, bytearray]) -> Any:
        """Validate a JSON string or bytes input and convert to native Python."""
        with wrap_validation_errors(self.name):
            return self.adapter.validate_json(value)

    def dump_python(self, value: Any, **kwargs) -> Any:
        """Serialize a value to its Python representation."""
        with wrap_validation_errors(self.name):
            return self.adapter.dump_python(value, **kwargs)

    def dump_json(self, value: Any, **kwargs) -> bytes:
        """Serialize a value to JSON-encoded bytes."""
        with wrap_validation_errors(self.name):
            return self.adapter.dump_json(value, **kwargs)

    def validate_and_dump(self, value: Any, **kwargs) -> bytes:
        """Validate a Python value and serialize it to JSON."""
        with wrap_validation_errors(self.name):
            validated = self.adapter.validate_python(value)
            return self.adapter.dump_json(validated, **kwargs)

    @classmethod
    def from_param_detail(cls, param: ParamDetail, globalns: dict[str, Any]) -> "ModelField":
        """
        Construct a ModelField from a ParamDetail.

        Args:
            param: The method parameter description, including name, type, and default
            globalns: Global namespace for resolving forward references

        Returns:
            A fully configured ModelField
        """
        annotation = param.annotation
        value = param.default
        field_info: Optional[FieldInfo] = None

        # Resolve forward references in string annotations (e.g. "User" → User)
        if isinstance(annotation, str):
            annotation, _ = try_eval_type(ForwardRef(annotation), globalns, globalns)

        # Extract FieldInfo from Annotated[type, FieldInfo, ...]
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            annotation = args[0]
            for meta in reversed(args[1:]):
                if isinstance(meta, FieldInfo):
                    field_info = meta
                    break

        # Use FieldInfo from Annotated, from default value, or create a new one
        if field_info is not None:
            field_info.annotation = annotation
        elif isinstance(value, FieldInfo):
            field_info = value
            field_info.annotation = annotation
        else:
            field_info = FieldInfo(
                annotation=annotation,
                default=value if not param.required else Ellipsis,
            )

        # Wrap annotation with FieldInfo via Annotated for full validation metadata
        return cls(
            name=param.name, info=field_info, raw_detail=param, adapter=TypeAdapter(Annotated[annotation, field_info])
        )


def create_merged_model(fields: dict[str, ModelField]) -> type[BaseModel]:
    """
    Dynamically create a Pydantic model from a collection of ModelField instances.

    Args:
        fields: A mapping of parameter names to their corresponding ModelField.

    Returns:
        A dynamically created Pydantic model class (named "MergedModel").

    Raises:
        TypeError: If any parameter is not KEYWORD_ONLY or POSITIONAL_OR_KEYWORD.
    """
    model_fields: dict[str, tuple[Any, FieldInfo]] = {}

    for field in fields.values():
        if field.raw_detail.kind not in (ParamKind.POSITIONAL_OR_KEYWORD, ParamKind.KEYWORD_ONLY):
            raise TypeError(
                f"Parameters must be POSITIONAL_OR_KEYWORD or KEYWORD_ONLY, "
                f"but got {field.raw_detail.kind} for '{field.name}'"
            )

        annotation = field.info.annotation or field.raw_detail.annotation
        model_fields[field.name] = (annotation, field.info)

    return create_model("MergedModel", **model_fields)  # type: ignore


def analyze_method(descriptor: MethodDescriptor) -> None:
    """
    Analyze a method's signature and populate its descriptor with Pydantic model fields.

    Args:
        descriptor: The method descriptor containing parameter/return metadata.

    Raises:
        TypeError: If any parameter uses an unsupported kind.
    """
    globalns = getattr(descriptor.call, "__globalns__", {})
    param_fields: dict[str, ModelField] = {}

    for param in descriptor.params:
        if param.kind not in (
            ParamKind.POSITIONAL_ONLY,
            ParamKind.POSITIONAL_OR_KEYWORD,
            ParamKind.KEYWORD_ONLY,
        ):
            raise TypeError(f"Invalid parameter kind for '{param.name}' in method '{descriptor.name}': {param.kind}")

        param_fields[param.name] = ModelField.from_param_detail(param, globalns)

    return_field = ModelField.from_param_detail(descriptor.return_param, globalns)

    descriptor.attributes[_PYDANTIC_PARAM_FIELDS] = param_fields
    descriptor.attributes[_PYDANTIC_RETURN_FIELD] = return_field


class PydanticEncoder(Encoder):
    """
    Encodes Python values into JSON bytes using Pydantic models.

    It supports both single and multiple parameters by utilizing TypeAdapter validation,
    and dynamically generates a merged model for multi-parameter scenarios.
    """

    __slots__ = ("_descriptor",)

    _descriptor: MethodDescriptor

    def __init__(self, descriptor: MethodDescriptor) -> None:
        self._descriptor = descriptor

        if not has_param_fields(descriptor) or not has_return_field(descriptor):
            analyze_method(descriptor)

        self._ensure_merged_model()

    def _ensure_merged_model(self) -> None:
        """
        If the method has multiple parameters, create and attach a merged Pydantic model
        to simplify encoding and validation.
        """
        if len(self._descriptor.params) <= 1 or has_merged_model(self._descriptor):
            return

        param_fields = get_param_fields(self._descriptor)
        merged_model = create_merged_model(param_fields)
        adapter = TypeAdapter(merged_model)
        set_merged_model(self._descriptor, merged_model, adapter)

    @property
    def encoding(self) -> str:
        """Return the encoding format used (currently fixed to JSON)."""
        return constants.JSON

    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """Encode parameter values into bytes using the method's Pydantic model(s)."""
        if isinstance(values, list) and len(params) != len(values):
            raise ValueError("Number of provided values does not match the number of parameters.")

        if not params:
            return b""

        if len(params) == 1:
            return self._encode_single_param(values, params)

        return self._encode_multiple_params(values, params)

    def _encode_single_param(self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail]) -> bytes:
        """
        Encode a single parameter to JSON bytes using its TypeAdapter.
        """
        param = params[0]
        field = (
            get_return_field(self._descriptor)
            if param.kind == ParamKind.RETURN
            else get_param_fields(self._descriptor)[param.name]
        )

        value = values.get(param.name, PydanticUndefined) if isinstance(values, dict) else values[0]

        return field.validate_and_dump(value, by_alias=True)

    def _encode_multiple_params(self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail]) -> bytes:
        """
        Encode multiple parameters by building an instance of the merged model
        and serializing it using its TypeAdapter.
        """
        MergedModel, adapter = get_merged_model(self._descriptor)

        # Convert to dict if values is a list
        value_dict = {param.name: values[i] for i, param in enumerate(params)} if isinstance(values, list) else values

        with wrap_validation_errors("MergedModel"):
            merged_instance = MergedModel(**value_dict)
            return adapter.dump_json(merged_instance, by_alias=True)


class PydanticDecoder(Decoder):
    """
    Decodes JSON bytes into typed parameters using Pydantic models.

    Supports both single and multiple parameter decoding by leveraging
    method descriptors and TypeAdapter-based validation.
    """

    __slots__ = ("_descriptor", "_dict_adapter")

    _descriptor: MethodDescriptor
    _dict_adapter: Optional[TypeAdapter]

    def __init__(self, descriptor: MethodDescriptor) -> None:
        self._descriptor = descriptor
        self._dict_adapter = None

        if not has_param_fields(descriptor) or not has_return_field(descriptor):
            analyze_method(descriptor)

        self._setup_multi_params()

    def _setup_multi_params(self) -> None:
        """
        Set up a TypeAdapter for decoding multiple parameters from a JSON object.

        Raises:
            TypeError: If any parameter is not keyword-compatible.
        """
        params = self._descriptor.params
        if len(params) <= 1:
            return

        if any(p.kind not in (ParamKind.KEYWORD_ONLY, ParamKind.POSITIONAL_OR_KEYWORD) for p in params):
            raise TypeError(
                f"All parameters in method '{self._descriptor.name}' must be keyword-compatible "
                f"(KEYWORD_ONLY or POSITIONAL_OR_KEYWORD)."
            )

        self._dict_adapter = TypeAdapter(dict[str, Any])

    @property
    def encoding(self) -> str:
        """Return the encoding format used by this decoder (currently JSON)."""
        return constants.JSON

    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """
        Decode a byte string into Python arguments based on method parameter metadata.

        Returns:
            - `list`: if the method takes positional or return parameters.
            - `dict`: if the method takes keyword parameters.
        """
        if not params:
            if data:
                raise ValueError("Expected no parameters, but non-empty data was provided.")
            return []

        if len(params) == 1:
            return self._decode_single_param(data, params[0])

        return self._decode_multiple_params(data, params)

    def _decode_single_param(self, data: bytes, param: ParamDetail) -> Union[list[Any], dict[str, Any]]:
        """Decode a single parameter from JSON bytes."""
        field = (
            get_return_field(self._descriptor)
            if param.kind == ParamKind.RETURN
            else get_param_fields(self._descriptor)[param.name]
        )

        value = field.validate_json(data)

        if param.kind in (ParamKind.POSITIONAL_ONLY, ParamKind.RETURN):
            return [value]
        return {param.name: value}

    def _decode_multiple_params(self, data: bytes, params: list[ParamDetail]) -> dict[str, Any]:
        """Decode multiple parameters from JSON bytes into a dict of named arguments."""
        param_fields = get_param_fields(self._descriptor)

        with wrap_validation_errors("Multiple parameters"):
            decoded_dict = cast(TypeAdapter, self._dict_adapter).validate_json(data)

        result: dict[str, Any] = {}

        for param in params:
            field = param_fields[param.name]
            raw_value = decoded_dict.get(field.name, field.default)

            if raw_value is PydanticUndefined:
                raise ValueError(f"Field '{param.name}' is required but not provided.")

            result[param.name] = field.validate_python(raw_value)

        return result


class PydanticCodec(Codec):
    """
    A Codec implementation based on Pydantic, combining encoding and decoding logic.

    It delegates to PydanticEncoder and PydanticDecoder to handle validation and transformation
    of parameters to/from serialized JSON bytes.
    """

    __slots__ = ("_encoder", "_decoder")

    def __init__(self, descriptor: MethodDescriptor) -> None:
        self._encoder = PydanticEncoder(descriptor)
        self._decoder = PydanticDecoder(descriptor)

    @property
    def encoding(self) -> str:
        """The encoding format used by this codec (e.g., 'json')."""
        return constants.JSON

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
    """
    Factory class for creating Pydantic-based codec components.

    Implements Encoder, Decoder, and Codec creation logic.
    """

    def create_encoder(self, descriptor: MethodDescriptor) -> Encoder:
        """Create a PydanticEncoder for the given method descriptor."""
        return PydanticEncoder(descriptor)

    def create_decoder(self, descriptor: MethodDescriptor) -> Decoder:
        """Create a PydanticDecoder for the given method descriptor."""
        return PydanticDecoder(descriptor)

    def create_codec(self, descriptor: MethodDescriptor) -> Codec:
        """Create a PydanticCodec for the given method descriptor."""
        return PydanticCodec(descriptor)
