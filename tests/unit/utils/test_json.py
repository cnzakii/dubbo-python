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

import math

import pytest

from dubbo.utils.json import dump_json, load_json


def test_json_is_compact_and_keeps_characters_outside_ascii() -> None:
    assert dump_json({"name": "名字", "tags": ("a", 1)}) == '{"name":"名字","tags":["a",1]}'
    assert load_json('{"name":"名字"}'.encode()) == {"name": "名字"}


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_a_number_json_has_no_text_for_is_refused(number: float) -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        dump_json({"value": number})


def test_json_that_nests_too_deeply_is_no_json() -> None:
    # Deeper than Python 3.14 parses too, which bounds the stack rather than the calls.
    with pytest.raises(ValueError, match="nests too deeply"):
        load_json("[" * 1_000_000 + "]" * 1_000_000)
