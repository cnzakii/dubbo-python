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

import pytest
import yaml

from dubbo.utils.yaml import load_yaml


@pytest.mark.parametrize(("text", "value"), [("", None), ("a: 1", {"a": 1}), ("- 1", [1]), ("x", "x")])
def test_any_plain_value_is_read(text, value):
    assert load_yaml(text) == value


def test_malformed_yaml_is_a_value_error_without_the_text():
    with pytest.raises(ValueError, match=r"^malformed YAML$") as caught:
        load_yaml("token: [secret")
    assert isinstance(caught.value.__cause__, yaml.YAMLError)
