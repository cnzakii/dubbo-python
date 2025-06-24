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
from contextlib import suppress
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Interface, IPv6Network
from os import PathLike
from types import GeneratorType
from typing import Any, Callable, Union, cast
from uuid import UUID

from typing_extensions import get_args, get_origin

from dubbo.common import constants
from dubbo.common.classes import SingletonBase
from dubbo.common.descriptor import MethodDescriptor, ParamDetail, ParamKind

from .base import Codec, CodecFactory, Decoder, Encoder

__all__ = ["JsonEncoder", "JsonDecoder", "JsonCodec", "JsonCodecFactory"]

# Mapping: type -> encoder function
TYPE_ENCODERS: dict[type, Callable[[Any], Any]] = {
    bytes: lambda o: o.decode(),
    datetime.date: lambda o: o.isoformat(),
    datetime.datetime: lambda o: o.isoformat(),
    datetime.time: lambda o: o.isoformat(),
    datetime.timedelta: lambda td: td.total_seconds(),
    decimal.Decimal: str,
    enum.Enum: lambda o: o.value,
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
    UUID: str,
}


def group_types_by_encoder(
    type_encoders: dict[type, Callable[[Any], Any]],
) -> dict[Callable[[Any], Any], tuple[type, ...]]:
    """
    Build an inverse mapping: encoder function -> tuple of types it supports.
    """
    encoder_to_types: dict[Callable[[Any], Any], tuple[type, ...]] = defaultdict(tuple)
    for typ, encoder in type_encoders.items():
        encoder_to_types[encoder] += (typ,)
    return encoder_to_types


ENCODERS_TYPE_GROUPS = group_types_by_encoder(TYPE_ENCODERS)


def to_jsonable(obj: Any) -> Any:
    """
    Recursively convert an object to a JSON-serializable format.

    Args:
        obj (Any): The object to encode.

    Returns:
        Any: A JSON-safe structure (primitives, list, dict, etc.)
    """
    # Primitive JSON-compatible types
    if isinstance(obj, (str, int, float, type(None))):
        return obj

    # Mapping: recursively encode keys and values
    if isinstance(obj, dict):
        return {to_jsonable(k): to_jsonable(v) for k, v in obj.items()}

    # Sequence-like: recursively encode items
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in obj]

    # Dataclass instance: convert to dict first
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(dataclasses.asdict(obj))

    # Exact type match in encoder registry
    if encoder := TYPE_ENCODERS.get(type(obj)):
        return encoder(obj)

    # Subclass match: check grouped encoder-to-type map
    for encoder_func, types_tuple in ENCODERS_TYPE_GROUPS.items():
        if isinstance(obj, types_tuple):
            return encoder_func(obj)

    # Fallback: attempt dict(obj) or vars(obj)
    try:
        data = dict(obj)
    except Exception as e1:
        try:
            data = vars(obj)
        except Exception as e2:
            raise ValueError([e1, e2]) from e2

    return to_jsonable(data)


class JsonEncoder(Encoder):
    """
    JSON encoder that serializes a single named parameter into JSON bytes.

    This encoder expects exactly one parameter (name + type) and either:
    - A list with one value corresponding to that parameter, or
    - A dict containing the parameter name.

    It uses `to_jsonable` to recursively transform the object into a
    JSON-compatible structure, then serializes it with `json.dumps`.
    """

    @property
    def encoding(self) -> str:
        """Returns the name of the serialization format used by this encoder."""
        return constants.JSON

    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """
        Encode a single parameter value to JSON.

        Args:
            values: Either a list or dict of values.
            params: List of parameter metadata (must contain exactly one).
            encoding: Character encoding used for the resulting JSON bytes.

        Returns:
            JSON-encoded bytes.

        Raises:
            ValueError: On invalid input shape or serialization failure.
        """
        if not params:
            return b""  # No parameters to encode

        if len(params) > 1:
            raise ValueError(f"JsonEncoder supports encoding only one parameter, but received {len(params)}.")

        param = params[0]

        # Extract value by index or key
        if isinstance(values, list):
            if len(values) != 1:
                raise ValueError(f"Expected a single-element list for parameter '{param.name}', but got {len(values)}.")
            value = values[0]
        else:
            if param.name not in values:
                raise ValueError(f"Expected key '{param.name}' in values dict, but it was not found.")
            value = values[param.name]

        # Serialize
        try:
            jsonable = to_jsonable(value)
            return json.dumps(jsonable, ensure_ascii=False).encode(encoding)
        except Exception as e:
            raise ValueError(
                f"Failed to encode value of type '{type(value).__name__}' for parameter '{param.name}': {e}"
            ) from e


# Mapping: target type -> decoder function
TYPE_DECODERS: dict[type, Callable[[Any], Any]] = {
    bytes: bytes,
    type(None): lambda o: None,
    str: str,
    int: int,
    float: float,
    bool: bool,
    datetime.date: lambda o: datetime.datetime.fromisoformat(o).date(),
    datetime.datetime: datetime.datetime.fromisoformat,
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
    re.Pattern: re.compile,
    set: set,
    UUID: UUID,
}


def group_types_by_decoder(
    type_decoders: dict[type, Callable[[Any], Any]],
) -> dict[Callable[[Any], Any], tuple[type, ...]]:
    """
    Build a reverse mapping: decoder function -> tuple of supported types.
    """
    decoder_to_types: dict[Callable[[Any], Any], tuple[type, ...]] = defaultdict(tuple)
    for typ, decoder in type_decoders.items():
        decoder_to_types[decoder] += (typ,)
    return decoder_to_types


DECODER_TYPE_GROUPS = group_types_by_decoder(TYPE_DECODERS)


def decode_value(raw: Union[dict, list, str, int, float, bool, None], target_type: Any) -> Any:
    """
    Decode a raw value into the specified target type.
    """
    try:
        # Handle Any type: return raw as is
        if target_type is Any:
            return raw

        origin = get_origin(target_type)
        args = get_args(target_type)

        # Handle Union (including Optional)
        if origin is Union:
            # If raw type matches any of the Union types, return it directly
            if type(raw) in args:
                return raw

            # Try each possible type until one succeeds
            for possible_type in args:
                with suppress(Exception):
                    return decode_value(raw, possible_type)
            raise ValueError(f"Value {raw!r} does not match any type in Union {args}")

        # Use origin for isinstance checks; fallback to target_type itself
        check_type = origin or target_type

        # Fast path: if raw is already of the target type (or origin), return it
        if isinstance(raw, check_type) and not isinstance(raw, (list, dict)):
            return raw

        # Handle list, decode each element recursively
        if origin in (list, tuple, set, frozenset) and isinstance(raw, (list, tuple)):
            item_type = args[0] if args else Any
            return [decode_value(item, item_type) for item in raw]

        # Handle dict, decode each key-value pair recursively
        if origin is dict and isinstance(raw, dict):
            key_type = args[0] if len(args) > 0 else str
            value_type = args[1] if len(args) > 1 else Any
            return {decode_value(k, key_type): decode_value(v, value_type) for k, v in raw.items()}

        # Handle dataclass from dict input
        if dataclasses.is_dataclass(check_type):
            if not isinstance(raw, dict):
                raise ValueError(f"Expected a dict for dataclass {check_type}, but got {type(raw).__name__}")
            cls = cast(type, check_type)
            return cls(
                **{
                    field.name: decode_value(raw.get(field.name), field.type)
                    for field in dataclasses.fields(check_type)
                }
            )

        # Use registered decoder function if available
        if decoder := TYPE_DECODERS.get(target_type):
            return decoder(raw)

        # Fallback: try to find decoder by matching raw's type
        for decoder_func, supported_types in DECODER_TYPE_GROUPS.items():
            if isinstance(raw, supported_types):
                return decoder_func(raw)

        # Final fallback: try direct construction
        return target_type(raw)
    except Exception as e:
        raise ValueError(f"Failed to decode value {raw!r} to type '{target_type}': {e}") from e


class JsonDecoder(Decoder):
    """
    JSON decoder that parses a single parameter from JSON-encoded bytes.

    This decoder only supports a single parameter and uses `decode_value`
    to convert the parsed JSON object into the expected type.
    """

    @property
    def encoding(self) -> str:
        """
        Return the name of the decoding format.
        """
        return constants.JSON

    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """
        Decode JSON-encoded bytes into one parameter's value.

        Args:
            data: The incoming byte stream (e.g., from network).
            params: A list of exactly one parameter descriptor.
            encoding: The character encoding of the bytes (default: UTF-8).

        Returns:
            A list or dict containing the decoded value, depending on the parameter kind.

        Raises:
            ValueError: If the input is invalid, not JSON, or the value cannot be decoded.
        """
        if not params:
            if data:
                raise ValueError("JsonDecoder received unexpected data without parameter definitions.")
            return []

        if len(params) > 1:
            raise ValueError("JsonDecoder supports only one parameter for decoding, but multiple were provided.")

        param = params[0]

        try:
            json_str = data.decode(encoding)
        except UnicodeDecodeError as e:
            raise ValueError(f"Failed to decode bytes using encoding '{encoding}': {e}") from e

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON input: {e}") from e

        try:
            value = decode_value(parsed, param.annotation)
        except Exception as e:
            raise ValueError(
                f"Failed to decode JSON value {parsed!r} to expected type '{param.annotation}': {e}"
            ) from e

        # Return based on parameter kind
        if param.kind == ParamKind.POSITIONAL_ONLY:
            return [value]
        return {param.name: value}


class JsonCodec(JsonEncoder, JsonDecoder, Codec):
    """
    Combines both JsonEncoder and JsonDecoder into a unified Codec.

    Implements the full Codec interface using JSON as the serialization format.
    """

    @property
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this codec.
        """
        return constants.JSON


class JsonCodecFactory(CodecFactory, SingletonBase):
    """
    A singleton factory that always returns the same JsonCodec instance.

    Ensures consistent reuse of a stateless JSON codec implementation.
    """

    __slots__ = ("_codec",)

    def __init__(self) -> None:
        self._codec: JsonCodec = JsonCodec()

    def create_encoder(self, descriptor: MethodDescriptor) -> Encoder:
        """
        Returns the shared JsonCodec as the encoder.
        """
        return self._codec

    def create_decoder(self, descriptor: MethodDescriptor) -> Decoder:
        """
        Returns the shared JsonCodec as the decoder.
        """
        return self._codec

    def create_codec(self, descriptor: MethodDescriptor) -> Codec:
        """
        Returns the shared JsonCodec instance.
        """
        return self._codec
