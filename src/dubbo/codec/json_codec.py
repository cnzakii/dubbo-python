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
from collections import defaultdict, deque
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Interface, IPv6Network
from os import PathLike
from types import GeneratorType
from typing import Any, Callable, Union
from uuid import UUID

from typing_extensions import get_args, get_origin

from dubbo.common import constants
from dubbo.common.classes import SingletonBase
from dubbo.common.descriptor import MethodDescriptor, ParamDetail, ParamKind

from .base import Codec, CodecFactory, Decoder, Encoder

__all__ = ["JsonEncoder", "JsonDecoder", "JsonCodec", "JsonCodecFactory"]

TYPE_TO_ENCODER: dict[type, Callable[[Any], Any]] = {
    bytes: lambda o: o.decode(),
    datetime.date: lambda o: o.isoformat(),
    datetime.datetime: lambda o: o.isoformat(),
    datetime.time: lambda o: o.isoformat(),
    datetime.timedelta: lambda td: td.total_seconds(),
    decimal.Decimal: str,
    enum.Enum: lambda o: o.value,
    frozenset: list,
    deque: list,
    GeneratorType: list,
    IPv4Address: str,
    IPv4Interface: str,
    IPv4Network: str,
    IPv6Address: str,
    IPv6Interface: str,
    IPv6Network: str,
    PathLike: str,
    re.Pattern: lambda o: o.pattern,
    set: list,
    UUID: str,
}


def invert_encoders_map(
    type_to_encoder: dict[type, Callable[[Any], Any]],
) -> dict[Callable[[Any], Any], tuple[type, ...]]:
    """
    Invert mapping from type->encoder to encoder->tuple of types.
    """
    encoder_to_types: dict[Callable[[Any], Any], tuple[type, ...]] = defaultdict(tuple)
    for typ, encoder in type_to_encoder.items():
        encoder_to_types[encoder] += (typ,)
    return encoder_to_types


ENCODER_TO_TYPES = invert_encoders_map(TYPE_TO_ENCODER)


def encode_jsonable(obj: Any) -> Any:
    """
    Recursively encode an object to a JSON-serializable format.
    Args:
        obj (Any): The object to encode.
    Returns:
        Any: A JSON-serializable representation of the object.
    """
    # Dataclass instance -> dict
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return encode_jsonable(dataclasses.asdict(obj))

    # Primitives -> return as is
    if isinstance(obj, (str, int, float, type(None))):
        return obj

    # Dict -> encode keys and values recursively
    if isinstance(obj, dict):
        return {encode_jsonable(k): encode_jsonable(v) for k, v in obj.items()}

    # List -> encode each element recursively
    if isinstance(obj, list):
        return [encode_jsonable(item) for item in obj]

    # Direct type match -> use encoder
    encoder = TYPE_TO_ENCODER.get(type(obj))
    if encoder:
        return encoder(obj)

    # Subclass match -> use corresponding encoder
    for encoder_func, types_tuple in ENCODER_TO_TYPES.items():
        if isinstance(obj, types_tuple):
            return encoder_func(obj)

    # Fallback: try dict() then vars()
    try:
        data = dict(obj)
    except Exception as e:
        errors = [e]
        try:
            data = vars(obj)
        except Exception as e2:
            errors.append(e2)
            raise ValueError(errors) from e2
    return encode_jsonable(data)


TYPE_TO_DECODER: dict[type, Callable[[Any], Any]] = {
    bytes: lambda o: bytes(o),
    Any: lambda o: o,
    type(None): lambda o: None,
    str: lambda o: o,
    int: lambda o: int(o),
    float: lambda o: float(o),
    bool: lambda o: bool(o),
    datetime.date: lambda o: datetime.datetime.fromisoformat(o).date(),
    datetime.datetime: lambda o: datetime.datetime.fromisoformat(o),
    datetime.time: lambda o: datetime.datetime.fromisoformat(o).time(),
    datetime.timedelta: lambda s: datetime.timedelta(seconds=float(s)),
    decimal.Decimal: decimal.Decimal,
    frozenset: frozenset,
    deque: deque,
    GeneratorType: list,
    IPv4Address: IPv4Address,
    IPv4Interface: IPv4Interface,
    IPv4Network: IPv4Network,
    IPv6Address: IPv6Address,
    IPv6Interface: IPv6Interface,
    IPv6Network: IPv6Network,
    PathLike: str,
    re.Pattern: lambda o: re.compile(o),
    set: set,
    UUID: UUID,
}


def invert_decoders_map(
    type_to_decoder: dict[type, Callable],
) -> dict[Callable, tuple[type, ...]]:
    """
    Invert mapping from type->decoder to decoder->tuple of types.
    """
    decoder_to_types: dict[Callable, tuple[type, ...]] = defaultdict(tuple)
    for typ, decoder in type_to_decoder.items():
        decoder_to_types[decoder] += (typ,)
    return decoder_to_types


DECODER_TO_TYPES = invert_decoders_map(TYPE_TO_DECODER)


def decode_value(raw: Any, target_type: type) -> Any:
    """
    Attempt to convert a `raw` value to the specified `target_type`.

    Supports basic types, collections, dataclasses, and known special types.
    """
    # Fast-path: already correct type
    if isinstance(raw, target_type):
        return raw

    try:
        origin = get_origin(target_type)
        args = get_args(target_type)

        # Handle generic list type: List[T]
        if origin is list and isinstance(raw, list) and args:
            return [decode_value(v, args[0]) for v in raw]

        # Handle generic dict type: Dict[K, V]
        if origin is dict and isinstance(raw, dict) and args:
            return {decode_value(k, args[0]): decode_value(v, args[1]) for k, v in raw.items()}

        # Handle dataclasses
        if dataclasses.is_dataclass(target_type):
            return target_type(**raw)

        # Use direct decoder if available
        decoder = TYPE_TO_DECODER.get(target_type)
        if decoder:
            return decoder(raw)

        # Fallback: try matching decoder based on input type
        for decoder_func, types_tuple in DECODER_TO_TYPES.items():
            if isinstance(raw, types_tuple):
                return decoder_func(raw)

        # Final fallback: try direct construction
        return target_type(raw)

    except Exception as e:
        raise ValueError(f"Failed to decode value {raw!r} to type {target_type.__name__}: {e}") from e


_JSON_NAME = "json"


class JsonEncoder(Encoder):
    """
    JSON encoder for encoding structured parameters.
    """

    @property
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this encoder.
        """
        return _JSON_NAME

    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """
        Encode the given values based on parameter metadata into a JSON byte representation.
        """
        if not params:
            return b""

        if len(params) > 1:
            raise ValueError("JsonEncoder supports only one parameter for encoding, but multiple were provided.")

        param = params[0]
        if isinstance(values, list):
            if len(values) != 1:
                raise ValueError(f"Expected a single value in list, but got {len(values)}.")
            value = values[0]
        else:
            if param.name not in values:
                raise ValueError(f"Expected parameter '{param.name}' in values, but it was not found.")
            value = values[param.name]

        try:
            # Encode the value to a JSON-serializable format
            jsonable_obj = encode_jsonable(value)
            # Convert to JSON bytes
            return json.dumps(jsonable_obj, ensure_ascii=False).encode(encoding)
        except Exception as e:
            raise ValueError(
                f"Failed to encode value of type {type(value).__name__} for parameter '{param.name}': {e}"
            ) from e


class JsonDecoder(Decoder):
    """
    JSON decoder for decoding structured parameters.
    """

    @property
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this decoder.
        """
        return _JSON_NAME

    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """
        Decode bytes into positional (list) or keyword (dict) arguments based on parameter metadata.
        """
        if not params:
            if data:
                raise ValueError("JsonDecoder received unexpected data without parameter definitions.")
            return []

        if len(params) > 1:
            raise ValueError("JsonDecoder supports only one parameter for decoding, but multiple were provided.")

        param = params[0]
        # Decode the JSON data
        json_str = data.decode(encoding)
        decoded_value = json.loads(json_str)

        # Validate the decoded value and ensure it matches the parameter's type
        excepted_value = decode_value(decoded_value, param.annotation)

        # Return as a single-item list or dict based on the parameter kind
        if param.kind == ParamKind.POSITIONAL_ONLY:
            return [excepted_value]
        return {param.name: excepted_value}


class JsonCodec(JsonEncoder, JsonDecoder, Codec):
    """
    JSON codec that combines encoding and decoding functionality for structured parameters.
    """

    @property
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this codec.
        """
        return _JSON_NAME


class JsonCodecFactory(CodecFactory, SingletonBase):
    __slots__ = ("_codec",)

    def __init__(self) -> None:
        self._codec = JsonCodec()

    def create_encoder(self, descriptor: MethodDescriptor) -> Encoder:
        """
        Create a JSON encoder based on the method descriptor.
        """
        return self._codec

    def create_decoder(self, descriptor: MethodDescriptor) -> Decoder:
        """
        Create a JSON decoder based on the method descriptor.
        """
        return self._codec

    def create_codec(self, descriptor: MethodDescriptor) -> Codec:
        """
        Create a JSON codec that combines both encoding and decoding functionality.
        """
        return self._codec
