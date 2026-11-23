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

pytest.importorskip("ngquic")

from dubbo.transport.http.h3.aio.stream import AioH3ServerStream


def test_an_abort_reported_before_the_handler_starts_cancels_it_as_it_starts() -> None:
    stream = AioH3ServerStream(None, 0, (), receive_ended=True)
    cancelled: list[bool] = []

    stream._report_abort()
    stream._cancel_on_abort(lambda: cancelled.append(True))

    assert cancelled == [True]
