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

from ngquic import CongestionControl

from dubbo.transport.http.h3.aio.settings import h3_settings
from dubbo.url import URL


def _url(**parameters: str) -> URL:
    """Underscores in parameter names stand for hyphens."""
    return URL(
        protocol="tri",
        host="localhost",
        port=443,
        parameters={"ssl-enabled": "true"} | {name.replace("_", "-"): value for name, value in parameters.items()},
    )


def test_the_quic_parameters_reach_the_transport() -> None:
    transport = h3_settings(
        _url(
            max_concurrent_uni_streams="5",
            initial_uni_window_size="1024",
            max_ack_delay="40",
            ack_delay_exponent="4",
            disable_active_migration="true",
            congestion_control="BBR",
        ),
        server=False,
    ).transport

    assert (transport.max_streams_uni, transport.max_stream_data_uni) == (5, 1024)
    assert (transport.max_ack_delay, transport.ack_delay_exponent) == (0.04, 4)
    assert transport.disable_active_migration
    assert transport.congestion_control is CongestionControl.BBR


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        pytest.param({"max_concurrent_uni_streams": "2"}, "'max-concurrent-uni-streams' must be at least 3", id="uni"),
        pytest.param({"initial_uni_window_size": "0"}, "'initial-uni-window-size' must be positive", id="uni-window"),
        pytest.param({"max_ack_delay": "16384"}, "'max-ack-delay' must be below 16384", id="ack-delay"),
        pytest.param({"ack_delay_exponent": "21"}, "'ack-delay-exponent' must be from 0 to 20", id="exponent"),
        pytest.param({"congestion_control": "vegas"}, "'congestion-control' must be one of reno, cubic, bbr", id="cc"),
    ],
)
def test_an_invalid_quic_parameter_is_refused(parameters, message) -> None:
    with pytest.raises(ValueError, match=message):
        h3_settings(_url(**parameters), server=False)
