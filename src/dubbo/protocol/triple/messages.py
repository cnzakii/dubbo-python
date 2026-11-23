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

"""How the values of a method travel as gRPC messages: in the format itself, or wrapped."""

from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

import attrs
from google.protobuf.message import DecodeError, Message

from dubbo._proto.org.apache.dubbo.triple.triple_wrapper_pb2 import TripleRequestWrapper
from dubbo.protocol.java import caller_error, java_exception, java_type_name
from dubbo.rpc.definition import Method
from dubbo.serialization import Codec, JavaSerialization, Serialization
from dubbo.serialization.protobuf import ProtobufSerialization
from dubbo.url import URL
from dubbo.utils.annotations import unannotated

__all__ = ["GrpcMessages", "grpc_messages", "method_codecs", "wrapped_codec", "wrapped_messages"]


@attrs.define(frozen=True, kw_only=True)
class GrpcMessages:
    """How the values of one method travel as gRPC messages, made once for each method.

    An error travels as the gRPC status of the call. In the wrapped messages
    of a format of Java objects, an exception the method threw may instead
    travel as the response message, flagged by ``tri-exception-code``.

    Attributes:
        content_subtype: Names the format of the messages in their content
            type, such as ``proto``.
        request: Codec of a request: the values of all the parameters.
        response: Codec of a response: the one value returned.
        exception: Codec of a response that is an exception the method
            threw, which encodes any exception and decodes as the error the
            caller receives; ``None`` where exceptions travel only as
            statuses.
    """

    content_subtype: str
    request: Codec[tuple[Any, ...]]
    response: Codec[tuple[Any, ...]]
    exception: Codec[tuple[Any, ...]] | None = None


def grpc_messages(serialization: Serialization, method: Method) -> GrpcMessages:
    """Return how the values of ``method`` travel in ``serialization``.

    The values of a format of Java objects, such as ``hessian2``, each travel
    wrapped in a protobuf message, unless the method takes and returns
    protobuf messages, which travel as themselves; the values of another
    format, such as protobuf, travel as its messages.

    Raises:
        TypeError: ``serialization`` cannot carry the request or response of ``method``.
    """
    # shortcut: only formats of Java objects travel wrapped; one wrapped without Java types needs the format to say so.
    if isinstance(serialization, JavaSerialization):
        if not _takes_messages(method):
            return wrapped_messages(serialization, method)
        serialization = _PROTOBUF

    request, response = method_codecs(serialization.codec, method)
    return GrpcMessages(content_subtype=serialization.content_subtype, request=request, response=response)


_PROTOBUF = ProtobufSerialization(URL(protocol="tri"))


def _takes_messages(method: Method) -> bool:
    """Return whether ``method`` takes one protobuf message and returns one."""
    types = [unannotated(each) for each in (*(parameter.type for parameter in method.request), method.response)]
    return len(types) == 2 and all(isinstance(t, type) and issubclass(t, Message) for t in types)


def method_codecs(
    codec: Callable[[Sequence[Any]], Codec[tuple[Any, ...]]], method: Method
) -> tuple[Codec[tuple[Any, ...]], Codec[tuple[Any, ...]]]:
    """Return the codecs ``codec`` makes of the requests and of the responses of ``method``.

    Raises:
        TypeError: ``codec`` cannot carry the request or response of ``method``.
    """
    try:
        request = codec(tuple(parameter.type for parameter in method.request))
    except TypeError as error:
        raise TypeError(f"the request of {method.name}: {error}") from None

    try:
        response = codec((method.response,))
    except TypeError as error:
        raise TypeError(f"the response of {method.name}: {error}") from None

    return request, response


# The other names peers give a format in a wrapper.
_ALIASES = {"hessian2": "hessian4"}


def wrapped_messages(serialization: JavaSerialization, method: Method) -> GrpcMessages:
    """Return how the values of ``method`` travel wrapped, each in ``serialization``.

    An exception the method throws travels as a response of one value too,
    which ``tri-exception-code`` flags: as `dubbo.protocol.java.java_exception`
    names it, and decoded as `dubbo.protocol.java.caller_error` takes it.

    Raises:
        TypeError: ``serialization`` cannot carry the request or response of
            ``method``, or one of its types has no Java type.
    """
    request, response = method_codecs(partial(wrapped_codec, serialization), method)
    thrown = wrapped_codec(serialization, (Exception,))

    def encode_exception(values: tuple[Any, ...]) -> bytes:
        return thrown.encode((java_exception(serialization, values[0]),))

    def decode_exception(data: bytes) -> tuple[Any, ...]:
        return (caller_error(thrown.decode(data)[0]),)

    exception = Codec(encode=encode_exception, decode=decode_exception)
    return GrpcMessages(content_subtype="proto", request=request, response=response, exception=exception)


def wrapped_codec(serialization: JavaSerialization, types: Sequence[Any]) -> Codec[tuple[Any, ...]]:
    """Return the codec of one value of each of ``types``, carried in a ``TripleRequestWrapper``.

    Each value is encoded alone in ``serialization``, and travels beside the
    Java type of its annotation. A wrapper of one value has the bytes of a
    ``TripleResponseWrapper`` of that value, as their fields have the same
    numbers and kinds, so the codec of one value is that of a response.

    Raises:
        TypeError: ``serialization`` cannot carry a type, or it has no Java type.
    """
    name = serialization.content_subtype
    names = {name, _ALIASES.get(name, name)}
    codecs = [serialization.codec((annotation,)) for annotation in types]
    try:
        java_types = [java_type_name(serialization, annotation) for annotation in types]
    except TypeError as error:
        raise TypeError(f"a wrapped value needs a Java type: {error}") from None

    def encode(values: tuple[Any, ...]) -> bytes:
        arguments = [codec.encode((value,)) for codec, value in zip(codecs, values, strict=True)]
        return TripleRequestWrapper(serializeType=name, args=arguments, argTypes=java_types).SerializeToString()

    def decode(data: bytes) -> tuple[Any, ...]:
        try:
            wrapper = TripleRequestWrapper.FromString(data)
        except DecodeError as error:
            raise ValueError(f"not a wrapped message: {error}") from None
        if wrapper.serializeType not in names:
            raise ValueError(f"the values are in {wrapper.serializeType!r}, not {name!r}")
        if len(wrapper.args) != len(codecs):
            raise ValueError(f"expected {len(codecs)} values, not {len(wrapper.args)}")

        return tuple(codec.decode(argument)[0] for codec, argument in zip(codecs, wrapper.args, strict=True))

    return Codec(encode=encode, decode=decode)
