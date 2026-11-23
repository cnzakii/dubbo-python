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

import anyio
import pytest

from dubbo import Result, RpcError, Status
from dubbo.filter import chain_filters
from dubbo.filter.activecount import active_counts
from dubbo.filter.actives import ActivesFilter

pytestmark = pytest.mark.anyio


async def test_actives_queues_calls_per_method_until_a_slot_is_released(gate, call, url):
    invoker = chain_filters(gate, [ActivesFilter(url.update_parameters({"actives": "1", "timeout": "60000"}))])
    results: list[Result] = []

    async def invoke() -> None:
        results.append(await invoker.invoke(call("wait")))

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(invoke)
            tasks.start_soon(invoke)
            await anyio.wait_all_tasks_blocked()
            assert (results, gate.entered) == ([], ["wait"])
            assert (await invoker.invoke(call("other"))).value == "other"
            gate.opened.set()

    assert [result.value for result in results] == ["wait", "wait"]
    assert gate.entered == ["wait", "other", "wait"]


async def test_actives_returns_unavailable_when_the_method_queue_timeout_expires(gate, call, url):
    invoker = chain_filters(
        gate, [ActivesFilter(url.update_parameters({"actives": "1", "timeout": "60000", "wait_briefly.timeout": "50"}))]
    )

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(invoker.invoke, call("wait_briefly"))
            await anyio.wait_all_tasks_blocked()
            refused = await invoker.invoke(call("wait_briefly"))
            gate.opened.set()

    assert isinstance(refused.error, RpcError)
    assert (refused.error.status, refused.error.message, gate.entered) == (
        Status.UNAVAILABLE,
        "com.example.UserService.wait_briefly makes 1 calls at once to 127.0.0.1:50051, and none ended within 0.05 seconds",
        ["wait_briefly"],
    )


async def test_actives_made_again_holds_calls_beyond_those_under_way_through_the_one_before(gate, call, url):
    limited = url.update_parameters({"actives": "1", "timeout": "50"})
    before = chain_filters(gate, [ActivesFilter(limited)])
    again = chain_filters(gate, [ActivesFilter(limited)])
    counts = active_counts(url)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(before.invoke, call("wait"))
            await anyio.wait_all_tasks_blocked()
            under_way = counts.get(url, "wait")
            held = await again.invoke(call("wait"))
            gate.opened.set()

    assert under_way == 1
    assert isinstance(held.error, RpcError)
    assert (held.error.status, gate.entered, counts.get(url, "wait")) == (Status.UNAVAILABLE, ["wait"], 0)


async def test_actives_gives_a_call_one_timeout_for_waiting_and_running(url, call):
    class Slow:
        url = None
        available = True

        async def invoke(self, invocation):
            # The time left reaches the protocol as the deadline of the call.
            budgets.append(anyio.current_effective_deadline() - anyio.current_time())
            await anyio.sleep(0.18)
            return Result(value="done")

        async def aclose(self):
            pass

    budgets: list[float] = []
    invoker = chain_filters(Slow(), [ActivesFilter(url.update_parameters({"actives": "1", "timeout": "250"}))])
    results: list[Result] = []

    async def invoke() -> None:
        results.append(await invoker.invoke(call("slow")))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(invoke)
        await anyio.wait_all_tasks_blocked()
        tasks.start_soon(invoke)

    first, second = results
    assert first.value == "done"
    assert isinstance(second.error, RpcError)
    assert second.error.status is Status.DEADLINE_EXCEEDED
    assert budgets[0] == pytest.approx(0.25)
    assert budgets[1] == pytest.approx(0.07, abs=0.01)
