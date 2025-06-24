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
from dubbo.common.descriptor import ParamDetail, ParamKind

from .base import Codec, CodecFactory, Decoder, Encoder

__all__ = ["ProtobufEncoder", "ProtobufDecoder", "ProtobufCodec", "ProtobufCodecFactory"]


class ProtobufEncoder(Encoder):
    """
    Encoder that serializes a single Protobuf message into bytes.
    """

    @property
    def encoding(self) -> str:
        """Return the serialization format used by this encoder."""
        return constants.PROTOBUF

    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """Serialize a single Protobuf-compatible value to bytes."""
        if not params:
            return b""

        if len(params) > 1:
            raise ValueError("PbEncoder supports only one parameter for encoding, but multiple were provided.")

        param = params[0]

        # Extract the actual value
        if isinstance(values, list):
            if len(values) != 1:
                raise ValueError(f"Expected a single value in list, but got {len(values)}.")
            value = values[0]
        else:
            if param.name not in values:
                raise ValueError(f"Missing parameter '{param.name}' in values.")
            value = values[param.name]

        # Ensure the value is serializable via Protobuf
        if hasattr(value, "SerializeToString"):
            try:
                return value.SerializeToString()
            except Exception as e:
                raise ValueError(f"Failed to serialize Protobuf message ({type(value).__name__}): {e}") from e

        raise TypeError(f"Expected a Protobuf message, but got {type(value).__name__}.")


class ProtobufDecoder(Decoder):
    """
    Decoder that deserializes bytes into a single Protobuf-compatible object.
    """

    @property
    def encoding(self) -> str:
        """Return the serialization format used by this decoder."""
        return constants.PROTOBUF

    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """Deserialize bytes into a Python object (Protobuf message, None, or raw bytes)."""
        if not params:
            if data:
                raise ValueError("Received data with no parameters defined.")
            return []

        if len(params) > 1:
            raise ValueError("Protobuf decoding supports only one parameter.")

        param = params[0]
        annotation = param.annotation

        if isinstance(annotation, type) and (issubclass(annotation, Message) or hasattr(annotation, "ParseFromString")):
            try:
                message = annotation()
                message.ParseFromString(data)
            except Exception as e:
                raise ValueError(f"Failed to parse Protobuf message: {e}") from e

        elif annotation is type(None):
            message = None

        elif annotation is Any:
            message = data

        else:
            raise TypeError(f"Unsupported parameter annotation: {annotation!r}")

        # Return as dict or list depending on param kind
        if param.kind in (ParamKind.KEYWORD_ONLY, ParamKind.POSITIONAL_OR_KEYWORD):
            return {param.name: message}
        else:
            return [message]


class ProtobufCodec(ProtobufEncoder, ProtobufDecoder, Codec):
    """
    Protobuf codec combining encoder and decoder logic.
    Inherits from both Encoder and Decoder for unified use.
    """

    @property
    def encoding(self) -> str:
        return constants.PROTOBUF


class ProtobufCodecFactory(CodecFactory, SingletonBase):
    """
    Factory for creating ProtobufCodec, ProtobufEncoder, and ProtobufDecoder instances.

    Note:
        Returns the same stateless singleton instance of `ProtobufCodec` for all creation methods.
    """

    __slots__ = ("_codec",)

    def __init__(self) -> None:
        self._codec = ProtobufCodec()

    def create_encoder(self, descriptor: Any) -> Encoder:
        return self._codec

    def create_decoder(self, descriptor: Any) -> Decoder:
        return self._codec

    def create_codec(self, descriptor: Any) -> Codec:
        return self._codec
