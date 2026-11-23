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

from dubbo.url import URL


def _url(port: int, parameters: dict[str, str]) -> URL:
    """Underscores in parameter names stand for hyphens."""
    return URL(
        protocol="tri",
        host="127.0.0.1",
        port=port,
        parameters={name.replace("_", "-"): value for name, value in parameters.items()},
    )


@pytest.fixture
def h1_url():
    """Return a function that gives the URL of a server or client on 127.0.0.1."""

    def h1_url(port: int = 0, **parameters: str) -> URL:
        return _url(port, parameters)

    return h1_url
