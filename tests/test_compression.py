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

import pytest

from dubbo.compression.bzip2 import Bzip2
from dubbo.compression.gzip import Gzip
from dubbo.compression.identity import Identity


class TestCompressionBase:
    """Base test class for compression classes."""

    @pytest.fixture
    def test_data(self):
        """
        Returns sample test data for compression tests.

        :return: Sample data in different formats
        :rtype: dict
        """
        return {
            "str": "Hello World",
            "bytes": b"Hello World",
            "bytearray": bytearray(b"Hello World"),
            "empty": b"",
            "large": b"a" * 10000,  # Test with larger data
        }

    def _test_compress_decompress(self, compressor, test_data):
        """
        Test that compressing and then decompressing recovers the original data.

        :param compressor: Compressor/decompressor instance
        :param test_data: Dictionary of test data
        """
        for data_name, data in test_data.items():
            compressed = compressor.compress(data)
            decompressed = compressor.decompress(compressed)

            # Check that we get bytes back
            assert isinstance(compressed, bytes)
            assert isinstance(decompressed, bytes)

            # Check that decompressing after compressing gives the original data
            if isinstance(data, str):
                assert decompressed == data.encode("utf-8")
            else:
                assert decompressed == bytes(data)


class TestIdentity(TestCompressionBase):
    """Test the Identity compression class."""

    @pytest.fixture
    def compressor(self):
        """
        Returns an Identity compressor instance.

        :return: Identity compressor
        :rtype: Identity
        """
        return Identity()

    def test_encoding(self, compressor):
        """
        Test that the encoding method returns the correct value.

        :param compressor: Identity compressor instance
        """
        assert compressor.encoding() == "identity"
        assert Identity.encoding() == "identity"

    def test_singleton(self):
        """Test that Identity follows the singleton pattern."""
        instance1 = Identity()
        instance2 = Identity()
        assert instance1 is instance2

    def test_compress_decompress(self, compressor, test_data):
        """
        Test compression and decompression with Identity.

        :param compressor: Identity compressor instance
        :param test_data: Dictionary of test data
        """
        self._test_compress_decompress(compressor, test_data)

    def test_identity_no_change(self, compressor, test_data):
        """
        Test that Identity compression doesn't actually change the data.

        :param compressor: Identity compressor instance
        :param test_data: Dictionary of test data
        """
        for data_name, data in test_data.items():
            expected = data.encode("utf-8") if isinstance(data, str) else bytes(data)
            compressed = compressor.compress(data)
            assert compressed == expected


class TestGzip(TestCompressionBase):
    """Test the Gzip compression class."""

    @pytest.fixture
    def compressor(self):
        """
        Returns a Gzip compressor instance.

        :return: Gzip compressor
        :rtype: Gzip
        """
        return Gzip()

    def test_encoding(self, compressor):
        """
        Test that the encoding method returns the correct value.

        :param compressor: Gzip compressor instance
        """
        assert compressor.encoding() == "gzip"
        assert Gzip.encoding() == "gzip"

    def test_singleton(self):
        """Test that Gzip follows the singleton pattern."""
        instance1 = Gzip()
        instance2 = Gzip()
        assert instance1 is instance2

    def test_compress_decompress(self, compressor, test_data):
        """
        Test compression and decompression with Gzip.

        :param compressor: Gzip compressor instance
        :param test_data: Dictionary of test data
        """
        self._test_compress_decompress(compressor, test_data)

    def test_gzip_compression_ratio(self, compressor):
        """
        Test that Gzip actually compresses data with repeating content.

        :param compressor: Gzip compressor instance
        """
        data = b"a" * 1000  # Highly compressible data
        compressed = compressor.compress(data)
        # The compressed data should be smaller than the original for repeating content
        assert len(compressed) < len(data)


class TestBzip2(TestCompressionBase):
    """Test the Bzip2 compression class."""

    @pytest.fixture
    def compressor(self):
        """
        Returns a Bzip2 compressor instance.

        :return: Bzip2 compressor
        :rtype: Bzip2
        """
        return Bzip2()

    def test_encoding(self, compressor):
        """
        Test that the encoding method returns the correct value.

        :param compressor: Bzip2 compressor instance
        """
        assert compressor.encoding() == "bzip2"
        assert Bzip2.encoding() == "bzip2"

    def test_singleton(self):
        """Test that Bzip2 follows the singleton pattern."""
        instance1 = Bzip2()
        instance2 = Bzip2()
        assert instance1 is instance2

    def test_compress_decompress(self, compressor, test_data):
        """
        Test compression and decompression with Bzip2.

        :param compressor: Bzip2 compressor instance
        :param test_data: Dictionary of test data
        """
        self._test_compress_decompress(compressor, test_data)

    def test_bzip2_compression_ratio(self, compressor):
        """
        Test that Bzip2 actually compresses data with repeating content.

        :param compressor: Bzip2 compressor instance
        """
        data = b"a" * 1000  # Highly compressible data
        compressed = compressor.compress(data)
        # The compressed data should be smaller than the original for repeating content
        assert len(compressed) < len(data)


def test_cross_compatibility():
    """Test that data compressed with one algorithm can't be decompressed with another."""
    data = b"Hello World"

    # Compress with each compressor
    gzip_compressed = Gzip().compress(data)
    bzip2_compressed = Bzip2().compress(data)

    # These should fail - gzip data can't be decompressed with bzip2 and vice versa
    with pytest.raises(Exception):
        Bzip2().decompress(gzip_compressed)

    with pytest.raises(Exception):
        Gzip().decompress(bzip2_compressed)
