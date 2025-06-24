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

from typing import Any
from unittest.mock import Mock

import pytest

from dubbo.codec.pb_codec import (
    ProtobufCodec,
    ProtobufCodecFactory,
    ProtobufDecoder,
    ProtobufEncoder,
)
from dubbo.common import constants
from dubbo.common.descriptor import ParamDetail, ParamKind

from .greet_pb2 import HelloReply, HelloRequest


class TestProtobufEncoder:
    """Test cases for ProtobufEncoder class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.encoder = ProtobufEncoder()

    def test_encoding_property(self):
        """Test that encoder returns correct encoding format."""
        assert self.encoder.encoding == constants.PROTOBUF

    def test_encode_empty_params(self):
        """Test encoding with empty parameters."""
        result = self.encoder.encode([], [])
        assert result == b""

    def test_encode_single_protobuf_message_list_values(self):
        """Test encoding a single protobuf message with list values."""
        # Arrange
        request = HelloRequest(name="test_user")
        params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act
        result = self.encoder.encode([request], params)

        # Assert
        assert isinstance(result, bytes)
        assert result == request.SerializeToString()

    def test_encode_single_protobuf_message_dict_values(self):
        """Test encoding a single protobuf message with dict values."""
        # Arrange
        request = HelloRequest(name="test_user")
        params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act
        result = self.encoder.encode({"request": request}, params)

        # Assert
        assert isinstance(result, bytes)
        assert result == request.SerializeToString()

    def test_encode_multiple_params_raises_error(self):
        """Test that encoding with multiple parameters raises ValueError."""
        # Arrange
        params = [
            ParamDetail(name="param1", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD),
            ParamDetail(name="param2", annotation=HelloReply, kind=ParamKind.POSITIONAL_OR_KEYWORD),
        ]

        # Act & Assert
        with pytest.raises(ValueError, match="PbEncoder supports only one parameter"):
            self.encoder.encode([HelloRequest(), HelloReply()], params)

    def test_encode_list_wrong_length_raises_error(self):
        """Test that encoding with wrong list length raises ValueError."""
        # Arrange
        params = [ParamDetail(name="param1", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act & Assert
        with pytest.raises(ValueError, match="Expected a single value in list"):
            self.encoder.encode([HelloRequest(), HelloReply()], params)

    def test_encode_dict_missing_param_raises_error(self):
        """Test that encoding with missing parameter in dict raises ValueError."""
        # Arrange
        params = [ParamDetail(name="missing_param", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act & Assert
        with pytest.raises(ValueError, match="Missing parameter 'missing_param'"):
            self.encoder.encode({"other_param": HelloRequest()}, params)

    def test_encode_non_protobuf_message_raises_error(self):
        """Test that encoding non-protobuf message raises TypeError."""
        # Arrange
        params = [ParamDetail(name="param1", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act & Assert
        with pytest.raises(TypeError, match="Expected a Protobuf message"):
            self.encoder.encode(["not_a_protobuf_message"], params)

    def test_encode_protobuf_serialization_error(self):
        """Test handling of protobuf serialization errors."""
        # Arrange
        mock_message = Mock()
        mock_message.SerializeToString.side_effect = Exception("Serialization failed")
        params = [ParamDetail(name="param1", annotation=type(mock_message), kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act & Assert
        with pytest.raises(ValueError, match="Failed to serialize Protobuf message"):
            self.encoder.encode([mock_message], params)


class TestProtobufDecoder:
    """Test cases for ProtobufDecoder class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.decoder = ProtobufDecoder()

    def test_encoding_property(self):
        """Test that decoder returns correct encoding format."""
        assert self.decoder.encoding == constants.PROTOBUF

    def test_decode_empty_params_empty_data(self):
        """Test decoding with empty parameters and empty data."""
        result = self.decoder.decode(data=b"", params=[])
        assert result == []

    def test_decode_empty_params_with_data_raises_error(self):
        """Test that decoding with data but no parameters raises ValueError."""
        with pytest.raises(ValueError, match="Received data with no parameters defined"):
            self.decoder.decode(data=b"some_data", params=[])

    def test_decode_multiple_params_raises_error(self):
        """Test that decoding with multiple parameters raises ValueError."""
        params = [
            ParamDetail(name="param1", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD),
            ParamDetail(name="param2", annotation=HelloReply, kind=ParamKind.POSITIONAL_OR_KEYWORD),
        ]

        with pytest.raises(ValueError, match="Protobuf decoding supports only one parameter"):
            self.decoder.decode(data=b"", params=params)

    def test_decode_protobuf_message_positional_param(self):
        """Test decoding protobuf message with positional parameter."""
        # Arrange
        original_request = HelloRequest(name="test_user")
        data = original_request.SerializeToString()
        params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_ONLY)]

        # Act
        result = self.decoder.decode(data=data, params=params)

        # Assert
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], HelloRequest)
        assert result[0].name == "test_user"

    def test_decode_protobuf_message_keyword_param(self):
        """Test decoding protobuf message with keyword parameter."""
        # Arrange
        original_request = HelloRequest(name="test_user")
        data = original_request.SerializeToString()
        params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.KEYWORD_ONLY)]

        # Act
        result = self.decoder.decode(data=data, params=params)

        # Assert
        assert isinstance(result, dict)
        assert "request" in result
        assert isinstance(result["request"], HelloRequest)
        assert result["request"].name == "test_user"

    def test_decode_none_annotation(self):
        """Test decoding with None annotation."""
        # Arrange
        params = [ParamDetail(name="param", annotation=type(None), kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act
        result = self.decoder.decode(data=b"", params=params)

        # Assert
        assert isinstance(result, dict)
        assert result["param"] is None

    def test_decode_any_annotation(self):
        """Test decoding with Any annotation."""
        # Arrange
        data = b"raw_bytes_data"
        params = [ParamDetail(name="param", annotation=Any, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act
        result = self.decoder.decode(data=data, params=params)

        # Assert
        assert isinstance(result, dict)
        assert result["param"] == data

    def test_decode_unsupported_annotation_raises_error(self):
        """Test that decoding with unsupported annotation raises TypeError."""
        # Arrange
        params = [ParamDetail(name="param", annotation=str, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act & Assert
        with pytest.raises(TypeError, match="Unsupported parameter annotation"):
            self.decoder.decode(data=b"", params=params)

    def test_decode_protobuf_parsing_error(self):
        """Test handling of protobuf parsing errors."""
        # Arrange
        invalid_data = b"invalid_protobuf_data"
        params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act & Assert
        with pytest.raises(ValueError, match="Failed to parse Protobuf message"):
            self.decoder.decode(data=invalid_data, params=params)

    def test_decode_different_param_kinds(self):
        """Test decoding with different parameter kinds."""
        # Test POSITIONAL_OR_KEYWORD
        original_request = HelloRequest(name="test")
        data = original_request.SerializeToString()

        params_pos_or_kw = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]
        result = self.decoder.decode(data=data, params=params_pos_or_kw)
        assert isinstance(result, dict)
        assert "request" in result

        # Test POSITIONAL_ONLY
        params_pos_only = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_ONLY)]
        result = self.decoder.decode(data=data, params=params_pos_only)
        assert isinstance(result, list)
        assert len(result) == 1


class TestProtobufCodec:
    """Test cases for ProtobufCodec class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.codec = ProtobufCodec()

    def test_encoding_property(self):
        """Test that codec returns correct encoding format."""
        assert self.codec.encoding == constants.PROTOBUF

    def test_codec_inherits_encoder_decoder(self):
        """Test that codec properly inherits from both encoder and decoder."""
        assert isinstance(self.codec, ProtobufEncoder)
        assert isinstance(self.codec, ProtobufDecoder)

    def test_round_trip_encoding_decoding(self):
        """Test complete round-trip encoding and decoding."""
        # Arrange
        original_request = HelloRequest(name="round_trip_test")
        encode_params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]
        decode_params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Act
        encoded_data = self.codec.encode([original_request], encode_params)
        decoded_result = self.codec.decode(data=encoded_data, params=decode_params)

        # Assert
        assert isinstance(decoded_result, dict)
        assert "request" in decoded_result
        decoded_request = decoded_result["request"]
        assert isinstance(decoded_request, HelloRequest)
        assert decoded_request.name == original_request.name


class TestProtobufCodecFactory:
    """Test cases for ProtobufCodecFactory class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = ProtobufCodecFactory()

    def test_singleton_behavior(self):
        """Test that factory follows singleton pattern."""
        factory1 = ProtobufCodecFactory()
        factory2 = ProtobufCodecFactory()
        assert factory1 is factory2

    def test_create_encoder(self):
        """Test encoder creation."""
        mock_descriptor = Mock()
        encoder = self.factory.create_encoder(mock_descriptor)

        assert isinstance(encoder, ProtobufEncoder)
        assert encoder.encoding == constants.PROTOBUF

    def test_create_decoder(self):
        """Test decoder creation."""
        mock_descriptor = Mock()
        decoder = self.factory.create_decoder(mock_descriptor)

        assert isinstance(decoder, ProtobufDecoder)
        assert decoder.encoding == constants.PROTOBUF

    def test_create_codec(self):
        """Test codec creation."""
        mock_descriptor = Mock()
        codec = self.factory.create_codec(mock_descriptor)

        assert isinstance(codec, ProtobufCodec)
        assert codec.encoding == constants.PROTOBUF

    def test_factory_returns_same_instance(self):
        """Test that factory returns the same codec instance."""
        mock_descriptor = Mock()

        encoder = self.factory.create_encoder(mock_descriptor)
        decoder = self.factory.create_decoder(mock_descriptor)
        codec = self.factory.create_codec(mock_descriptor)

        # All should be the same instance
        assert encoder is decoder
        assert decoder is codec
        assert encoder is codec

    def test_multiple_factory_instances_share_codec(self):
        """Test that multiple factory instances share the same codec."""
        factory1 = ProtobufCodecFactory()
        factory2 = ProtobufCodecFactory()

        mock_descriptor = Mock()
        codec1 = factory1.create_codec(mock_descriptor)
        codec2 = factory2.create_codec(mock_descriptor)

        assert codec1 is codec2


class TestProtobufCodecIntegration:
    """Integration tests for the complete protobuf codec system."""

    def test_complex_protobuf_message(self):
        """Test with a more complex protobuf message."""
        # Test with HelloReply
        codec = ProtobufCodec()
        original_reply = HelloReply(message="Hello, World! This is a test message.")

        encode_params = [ParamDetail(name="reply", annotation=HelloReply, kind=ParamKind.POSITIONAL_OR_KEYWORD)]
        decode_params = [ParamDetail(name="reply", annotation=HelloReply, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Encode
        encoded_data = codec.encode({"reply": original_reply}, encode_params)

        # Decode
        decoded_result = codec.decode(data=encoded_data, params=decode_params)

        # Verify
        assert isinstance(decoded_result, dict)
        decoded_reply = decoded_result["reply"]
        assert isinstance(decoded_reply, HelloReply)
        assert decoded_reply.message == original_reply.message

    def test_empty_protobuf_message(self):
        """Test with empty protobuf message."""
        codec = ProtobufCodec()
        original_request = HelloRequest()  # Empty message

        params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        # Encode
        encoded_data = codec.encode([original_request], params)

        # Decode
        decoded_result = codec.decode(data=encoded_data, params=params)

        # Verify
        assert isinstance(decoded_result, dict)
        decoded_request = decoded_result["request"]
        assert isinstance(decoded_request, HelloRequest)
        assert decoded_request.name == ""  # Default empty string

    def test_factory_integration(self):
        """Test integration between factory and codec instances."""
        factory = ProtobufCodecFactory()
        mock_descriptor = Mock()

        encoder = factory.create_encoder(mock_descriptor)
        decoder = factory.create_decoder(mock_descriptor)

        # Test encoding with factory-created encoder
        original_request = HelloRequest(name="factory_test")
        params = [ParamDetail(name="request", annotation=HelloRequest, kind=ParamKind.POSITIONAL_OR_KEYWORD)]

        encoded_data = encoder.encode([original_request], params)
        decoded_result = decoder.decode(data=encoded_data, params=params)

        assert isinstance(decoded_result, dict)
        assert decoded_result["request"].name == "factory_test"
