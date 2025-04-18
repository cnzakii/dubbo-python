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
import os
import tempfile

import pytest

from dubbo.config.parser._ini import IniConfigParser, _to_nested_dict
from dubbo.config.parser._json import JsonConfigParser
from dubbo.config.parser._toml import TomlConfigParser
from dubbo.config.parser._yaml import YamlConfigParser

# Get the path to the test data directory
TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


class TestIniConfigParser:
    def test_parse_str(self):
        parser = IniConfigParser()
        content = """
        [section1]
        key1 = value1
        key2 = value2

        [section2]
        key3 = value3
        """
        result = parser.parse_str(content)
        assert result == {"section1": {"key1": "value1", "key2": "value2"}, "section2": {"key3": "value3"}}

    def test_parse_str_nested(self):
        parser = IniConfigParser()
        content = """
        [section1]
        key1 = value1
        key2 = value2
        """
        result = parser.parse_str(content, return_nested=True)
        assert result == {"section1": {"key1": "value1", "key2": "value2"}}

    def test_parse_str_invalid(self):
        parser = IniConfigParser()
        content = """
        [section1
        key1 = value1
        """
        with pytest.raises(ValueError):
            parser.parse_str(content)

    def test_parse_file(self):
        parser = IniConfigParser()
        file_path = os.path.join(TEST_DATA_DIR, "dubbo.ini")
        result = parser.parse_file(file_path)
        assert "dubbo.application" in result
        assert result["dubbo.application"]["name"] == "dubbo-python"

    def test_parse_file_nested(self):
        parser = IniConfigParser()
        file_path = os.path.join(TEST_DATA_DIR, "dubbo.ini")
        result = parser.parse_file(file_path, return_nested=True)
        assert "dubbo" in result
        assert "application" in result["dubbo"]
        assert result["dubbo"]["application"]["name"] == "dubbo-python"

    def test_parse_file_not_found(self):
        parser = IniConfigParser()
        with pytest.raises(FileNotFoundError):
            parser.parse_file("non_existent_file.ini")

    def test_parse_file_invalid(self):
        parser = IniConfigParser()
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".ini", delete=False) as tmp:
            tmp.write("[section1\nkey1=value1")
            tmp_path = tmp.name

        try:
            with pytest.raises(ValueError):
                parser.parse_file(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_to_nested_dict(self):
        flat_dict = {"a.b.c": 1, "a.b.d": 2, "e.f": 3}
        nested_dict = _to_nested_dict(flat_dict)
        assert nested_dict == {"a": {"b": {"c": 1, "d": 2}}, "e": {"f": 3}}

        # Test custom delimiter
        flat_dict = {"a:b:c": 1, "a:b:d": 2}
        nested_dict = _to_nested_dict(flat_dict, split_symbol=":")
        assert nested_dict == {"a": {"b": {"c": 1, "d": 2}}}


class TestJsonConfigParser:
    def test_parse_str(self):
        parser = JsonConfigParser()
        content = '{"key1": "value1", "key2": {"nested": "value2"}}'
        result = parser.parse_str(content)
        assert result == {"key1": "value1", "key2": {"nested": "value2"}}

    def test_parse_str_invalid(self):
        parser = JsonConfigParser()
        content = '{"key1": "value1", "key2": {"nested": "value2"'
        with pytest.raises(ValueError):
            parser.parse_str(content)

    def test_parse_file(self):
        parser = JsonConfigParser()
        file_path = os.path.join(TEST_DATA_DIR, "dubbo.json")
        result = parser.parse_file(file_path)
        assert "dubbo" in result
        assert "application" in result["dubbo"]
        assert result["dubbo"]["application"]["name"] == "dubbo-python"

    def test_parse_file_not_found(self):
        parser = JsonConfigParser()
        with pytest.raises(FileNotFoundError):
            parser.parse_file("non_existent_file.json")

    def test_parse_file_invalid(self):
        parser = JsonConfigParser()
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
            tmp.write('{"key1": "value1", "key2": {"nested": "value2"')
            tmp_path = tmp.name

        try:
            with pytest.raises(ValueError):
                parser.parse_file(tmp_path)
        finally:
            os.unlink(tmp_path)


class TestYamlConfigParser:
    def test_parse_str(self):
        parser = YamlConfigParser()
        content = """
        key1: value1
        key2:
          nested: value2
        """
        result = parser.parse_str(content)
        assert result == {"key1": "value1", "key2": {"nested": "value2"}}

    def test_parse_str_invalid(self):
        parser = YamlConfigParser()
        content = """
        key1: value1
          key2: value2
         bad_indent: value3
        """
        with pytest.raises(ValueError):
            parser.parse_str(content)

    def test_parse_file(self):
        parser = YamlConfigParser()
        file_path = os.path.join(TEST_DATA_DIR, "dubbo.yaml")
        result = parser.parse_file(file_path)
        assert "dubbo" in result
        assert "application" in result["dubbo"]
        assert result["dubbo"]["application"]["name"] == "dubbo-python"

    def test_pyyaml_missing(self):
        # Temporarily remove the PyYAML module
        import importlib
        import sys

        if "yaml" in sys.modules:
            del sys.modules["yaml"]

        parser = YamlConfigParser()
        with pytest.raises(ImportError):
            parser.parse_str("key: value")

        # Re-import the PyYAML module
        importlib.import_module("yaml")

    def test_parse_file_not_found(self):
        parser = YamlConfigParser()
        with pytest.raises(FileNotFoundError):
            parser.parse_file("non_existent_file.yaml")

    def test_parse_file_invalid(self):
        parser = YamlConfigParser()
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".yaml", delete=False) as tmp:
            tmp.write("""
            key1: value1
              key2: value2
             bad_indent: value3
            """)
            tmp_path = tmp.name

        try:
            with pytest.raises(ValueError):
                parser.parse_file(tmp_path)
        finally:
            os.unlink(tmp_path)


class TestTomlConfigParser:
    def test_parse_str(self):
        parser = TomlConfigParser()
        content = """
        [section]
        key1 = "value1"
        key2 = 42

        [nested.section]
        key3 = "value3"
        """
        result = parser.parse_str(content)
        assert result == {"section": {"key1": "value1", "key2": 42}, "nested": {"section": {"key3": "value3"}}}

    def test_parse_str_invalid(self):
        parser = TomlConfigParser()
        content = """
        [section
        key1 = "value1"
        """
        with pytest.raises(ValueError):
            parser.parse_str(content)

    def test_parse_file(self):
        parser = TomlConfigParser()
        file_path = os.path.join(TEST_DATA_DIR, "dubbo.toml")
        result = parser.parse_file(file_path)
        assert "dubbo" in result
        assert "application" in result["dubbo"]
        assert result["dubbo"]["application"]["name"] == "dubbo-python"

    def test_parse_file_not_found(self):
        parser = TomlConfigParser()
        with pytest.raises(FileNotFoundError):
            parser.parse_file("non_existent_file.toml")

    def test_parse_file_invalid(self):
        parser = TomlConfigParser()
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".toml", delete=False) as tmp:
            tmp.write("""
            [section
            key1 = "value1"
            """)
            tmp_path = tmp.name

        try:
            with pytest.raises(ValueError):
                parser.parse_file(tmp_path)
        finally:
            os.unlink(tmp_path)
