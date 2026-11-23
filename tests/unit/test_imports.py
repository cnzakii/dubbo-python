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

import subprocess
import sys

import pytest


def test_importing_the_package_imports_none_of_the_generated_contracts() -> None:
    # Generated contracts import the package itself, so the package reaches them only from functions.
    code = "import sys, dubbo; print(sorted(name for name in sys.modules if '._proto.' in name and name.endswith('_dubbo')))"

    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout

    assert output.strip() == "[]"


@pytest.mark.parametrize(
    "module",
    [
        "dubbo.protocol.triple.server",
        "dubbo.metadata.service.v2",
        "dubbo._proto.grpc.health.v1.health_dubbo",
        "dubbo._proto.org.apache.dubbo.metadata.metadata_service_v2_dubbo",
    ],
)
def test_a_module_imports_first_in_a_fresh_interpreter(module) -> None:
    subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, check=True)
