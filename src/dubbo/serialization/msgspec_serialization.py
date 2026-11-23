# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.


"""What the ``json`` and ``msgpack`` serializations share: msgspec, with the classes it does not know converted by the installed models."""

from collections.abc import Callable, Sequence
from typing import Any, ClassVar

import msgspec

from dubbo.url import URL

from .base import Codec, Serialization
from .models import Models

__all__ = ["MsgspecSerialization"]

# What msgspec raises for a value of a known type it still cannot write: an integer too large for MessagePack, a
# value that holds itself, a lone surrogate in a string.
_UNWRITABLE = (msgspec.EncodeError, OverflowError, RecursionError, UnicodeEncodeError)


class MsgspecSerialization(Serialization):
    """A format msgspec writes and validates, such as JSON, whose subclass names msgspec's encoder and decoder of it.

    A message of one value is that value, and of any other number of values
    an array of them in order. Decoding validates each value against its
    type, with the constraints of its ``Annotated`` metadata that msgspec
    knows, raising `msgspec.ValidationError`, a `ValueError`.

    Attributes:
        models: The installed models, which convert the classes msgspec does
            not know.
    """

    shared = True
    encoder: ClassVar[Callable[..., Any]]
    decoder: ClassVar[Callable[..., Any]]

    def __init__(self, url: URL) -> None:
        self.models = Models(url)

    def codec(self, types: Sequence[Any]) -> Codec[tuple[Any, ...]]:
        """Return the codec of one value of each of ``types``.

        Raises:
            TypeError: msgspec cannot carry a type, or no installed model, or
                more than one, converts a class within one.
        """
        models = self.models
        models.prepare(types)
        write = self.encoder(enc_hook=models.encode).encode
        single = len(types) == 1
        read = self.decoder(
            types[0] if single else tuple.__class_getitem__(tuple(types)), dec_hook=models.decode
        ).decode

        def encode(values: tuple[Any, ...]) -> bytes:
            try:
                return write(values[0] if single else values)
            except _UNWRITABLE as error:
                raise TypeError(f"cannot encode the value: {error}") from error

        def decode(data: bytes) -> tuple[Any, ...]:
            try:
                value = read(data)
            except RecursionError:
                raise ValueError("the data is nested too deeply") from None
            return (value,) if single else value

        return Codec(encode=encode, decode=decode)
