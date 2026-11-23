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

"""How a plain HTTP request is refused."""

import re
from collections.abc import Sequence
from typing import Any

__all__ = ["HttpError", "problem"]

# The path msgspec puts at the end of a message, such as " - at `$.tags[1]`".
_AT = re.compile(r" - at `\$(.*)`$", re.DOTALL)
_STEP = re.compile(r"\.([^.\[]+)|\[(\d+|\.\.\.)\]")


class HttpError(Exception):
    """Ends a request with an error response, raised and caught within the handling of one request.

    Attributes:
        status: The HTTP status of the response.
        detail: What went wrong, the ``detail`` of the JSON body.
        headers: Fields the response adds.
    """

    def __init__(self, status: int, detail: Any, headers: Sequence[tuple[bytes, bytes]] = ()) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.headers = tuple(headers)


def problem(loc: tuple[Any, ...], error: Exception, kind: str = "value_invalid") -> dict[str, Any]:
    """Return ``error`` as a problem of a 422, located at ``loc`` and then at the path msgspec reports within the value."""
    message = str(error)
    steps: list[Any] = []
    if found := _AT.search(message):
        for name, index in _STEP.findall(found[1]):
            # msgspec names no key of a dict, which leaves the path in the message.
            if index == "...":
                break
            steps.append(name or int(index))
        else:
            message = message[: found.start()]
    return {"loc": [*loc, *steps], "msg": message, "type": kind}
