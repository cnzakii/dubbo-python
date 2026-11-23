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

"""JSON text as the framework writes and reads it everywhere."""

import json
from typing import Any

__all__ = ["dump_json", "load_json"]

_ENCODER = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def dump_json(value: Any) -> str:
    """Return ``value``, made of dicts with string keys, lists, tuples, strings, numbers, booleans and ``None``, as compact JSON text.

    Characters outside ASCII stay as they are; the text is meant to be
    encoded in UTF-8.

    Raises:
        TypeError: ``value`` holds something else.
        ValueError: ``value`` holds a NaN or an infinity, which JSON has no
            text for, or holds itself.
    """
    return _ENCODER.encode(value)


def load_json(data: str | bytes) -> Any:
    """Return the value the JSON text ``data`` holds, bytes in UTF-8, UTF-16 or UTF-32.

    Raises:
        ValueError: ``data`` is no JSON, or nests too deeply to read.
    """
    try:
        return json.loads(data)
    except RecursionError:
        raise ValueError("the JSON nests too deeply") from None
