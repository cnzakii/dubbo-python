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

import h11r
import pytest

from dubbo.transport.http.h1.aio.connection import AioH1Connection


@pytest.mark.parametrize(("tls", "kept_open"), [(False, True), (True, False)])
def test_a_tunnel_stays_open_to_send_after_the_peer_finished_unless_tls_closes_it(tls, kept_open) -> None:
    connection = AioH1Connection(h11r.Role.SERVER, max_head_bytes=1024)
    connection.tls = tls
    connection.tunneled = True

    assert connection.eof_received() is kept_open
