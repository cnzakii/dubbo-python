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
from typing import Any, Union

from google.protobuf.message import Message

from dubbo.common import constants
from dubbo.common.classes import SingletonBase
from dubbo.common.descriptor import MethodDescriptor, ParamDetail, ParamKind

from .base import Codec, CodecFactory, Decoder, Encoder

__all__ = ["ProtobufEncoder", "ProtobufDecoder", "ProtobufCodec", "ProtobufCodecFactory"]

_PROTOBUF_NAME = "protobuf"


class ProtobufEncoder(Encoder):
    """
    Protobuf encoder for encoding structured parameters.
    """

    @property
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this encoder.
        """
        return _PROTOBUF_NAME

    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """
        Encode the given values based on parameter metadata into a serialized byte representation using Protobuf.
        """
        if not params:
            return b""

        if len(params) > 1:
            raise ValueError("PbEncoder supports only one parameter for encoding, but multiple were provided.")

        param = params[0]

        # Extract value from list or dict
        if isinstance(values, list):
            if len(values) != 1:
                raise ValueError(f"Expected a single value in list, but got {len(values)}.")
            value = values[0]
        else:
            if param.name not in values:
                raise ValueError(f"Expected parameter '{param.name}' in values, but it was not found.")
            value = values[param.name]

        # Check value is a Protobuf Message or has SerializeToString method
        if isinstance(value, Message) or hasattr(value, "SerializeToString"):
            try:
                return value.SerializeToString()
            except Exception as e:
                raise ValueError(f"Failed to serialize Protobuf message of type {type(value).__name__}: {e}") from e

        raise TypeError(f"Expected a Protobuf message, but got {type(value).__name__} for parameter '{param.name}'.")


class ProtobufDecoder(Decoder):
    """
    Protobuf decoder for decoding structured parameters.
    """

    @property
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this decoder.
        """
        return _PROTOBUF_NAME

    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """
        Decode a serialized byte representation into a dictionary of parameter names and values.
        """
        if not params:
            if data:
                raise ValueError("PbDecoder received unexpected data without parameter definitions.")
            return []

        if len(params) > 1:
            raise ValueError("PbDecoder supports only one parameter for decoding, but multiple were provided.")

        param = params[0]
        annotation = param.annotation

        # Case 1: Expected Protobuf message
        if isinstance(annotation, type) and (issubclass(annotation, Message) or hasattr(annotation, "ParseFromString")):
            try:
                message = annotation()
                message.ParseFromString(data)
            except Exception as e:
                raise ValueError(f"Failed to parse Protobuf message: {e}") from e

        # Case 2: Explicit None — no decoding needed
        elif annotation is type(None):
            message = None

        # Case 3: Any — return raw bytes
        elif annotation is Any:
            message = data

        # Case 4: Unknown/unsupported
        else:
            raise TypeError(f"Unsupported annotation for PbDecoder: {annotation!r}")

        # Return as dict or list depending on param kind
        if param.kind in (ParamKind.KEYWORD_ONLY, ParamKind.POSITIONAL_OR_KEYWORD):
            return {param.name: message}
        else:
            return [message]


class ProtobufCodec(ProtobufEncoder, ProtobufDecoder, Codec):
    """
    Protobuf codec that combines encoding and decoding functionality.
    """

    @property
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this codec.
        """
        return _PROTOBUF_NAME


class ProtobufCodecFactory(CodecFactory, SingletonBase):
    """Factory for creating Protobuf codecs, encoders, and decoders."""

    __slots__ = ("_codec",)

    def __init__(self) -> None:
        self._codec = ProtobufCodec()

    def create_encoder(self, descriptor: MethodDescriptor) -> Encoder:
        return self._codec

    def create_decoder(self, descriptor: MethodDescriptor) -> Decoder:
        return self._codec

    def create_codec(self, descriptor: MethodDescriptor) -> Codec:
        return self._codec
