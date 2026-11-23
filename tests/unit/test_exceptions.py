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

import pickle

import anyio
import pytest

from dubbo import EndOfStream, RpcError, Status
from dubbo.exceptions import RemoteError


@pytest.mark.parametrize(
    "error",
    [
        RpcError(Status.NOT_FOUND, "no such user"),
        RemoteError("java.lang.IllegalStateException", "closed", fields={"code": 7}, status=Status.INTERNAL),
        EndOfStream(),
    ],
    ids=["rpc", "remote", "end-of-stream"],
)
def test_errors_survive_pickling(error: Exception) -> None:
    copy = pickle.loads(pickle.dumps(error))

    assert (type(copy), str(copy), vars(copy)) == (type(error), str(error), vars(error))


def test_the_end_of_a_stream_is_anyios() -> None:
    assert issubclass(EndOfStream, anyio.EndOfStream)
