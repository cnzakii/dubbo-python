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
import abc
from typing import Any, Union

from dubbo.common import constants
from dubbo.common.descriptor import MethodDescriptor, ParamDetail

__all__ = ["Encoder", "Decoder", "Codec", "CodecFactory"]


class Encoder(abc.ABC):
    """
    Abstract base class for encoding parameters into bytes.
    """

    @property
    @abc.abstractmethod
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this encoder.

        Examples:
            "json", "protobuf", "hessian"

        Returns:
            str: The encoding format identifier.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def encode(
        self, values: Union[list[Any], dict[str, Any]], params: list[ParamDetail], *, encoding: str = constants.UTF_8
    ) -> bytes:
        """
        Encode the given values based on parameter metadata.

        Args:
            values (list or dict): Input values as positional (list) or keyword (dict) arguments.
            params (list): Parameter metadata.
            encoding (str): Character encoding, if applicable.

        Returns:
            bytes: Serialized data.

        Example:
            # Using positional arguments
            encoder.encode(
                values=[42, "hello"],
                params=[
                    ParamDetail(name="age", annotation=int),
                    ParamDetail(name="name", annotation=str),
                ],
                encoding="utf-8"
            )

            # Using keyword arguments
            encoder.encode(
                values={"age": 42, "name": "hello"},
                params=[
                    ParamDetail(name="age", annotation=int),
                    ParamDetail(name="name", annotation=str),
                ],
                encoding="utf-8"
            )
        """
        raise NotImplementedError()


class Decoder(abc.ABC):
    """
    Abstract base class for decoding bytes into parameters.
    """

    @property
    @abc.abstractmethod
    def encoding(self) -> str:
        """
        Returns the name of the serialization format used by this decoder.

        Examples:
            "json", "protobuf", "hessian"

        Returns:
            str: The encoding format identifier.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def decode(
        self, *, data: bytes, params: list[ParamDetail], encoding: str = constants.UTF_8
    ) -> Union[list[Any], dict[str, Any]]:
        """
        Decode bytes into positional (list) or keyword (dict) arguments.

        Args:
            data (bytes): Serialized input data.
            params (list): Parameter metadata.
            encoding (str): Character encoding, if applicable.

        Returns:
            list or dict: Decoded values as positional or keyword arguments.

        Example:
            # Decode arguments
            decoded_values = decoder.decode(
                data=b'...',
                params=[
                    ParamDetail(name="age", annotation=int),
                    ParamDetail(name="name", annotation=str),
                ],
                encoding="utf-8"
            )
        """
        raise NotImplementedError()


class Codec(Encoder, Decoder, abc.ABC):
    """
    Base class combining Encoder and Decoder interfaces.
    """

    pass


class CodecFactory(abc.ABC):
    """
    Abstract factory for creating instances of Encoder, Decoder, and Codec.
    """

    @abc.abstractmethod
    def create_encoder(self, descriptor: MethodDescriptor) -> Encoder:
        """
        Create an encoder instance based on the method descriptor.

        Args:
            descriptor (MethodDescriptor): The method descriptor containing parameter metadata.

        Returns:
            Encoder: An instance of the Encoder for the specified method.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_decoder(self, descriptor: MethodDescriptor) -> Decoder:
        """
        Create a decoder instance based on the method descriptor.

        Args:
            descriptor (MethodDescriptor): The method descriptor containing parameter metadata.

        Returns:
            Decoder: An instance of the Decoder for the specified method.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def create_codec(self, descriptor: MethodDescriptor) -> Codec:
        """
        Create a codec instance based on the method descriptor.

        Args:
            descriptor (MethodDescriptor): The method descriptor containing parameter metadata.

        Returns:
            Codec: An instance of the Codec for the specified method.
        """
        raise NotImplementedError()
