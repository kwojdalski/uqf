"""#1651, criterion 1: a publisher on this machine attached to a stack on another.

`uqs job attach` runs on uat-client and starts the fixture feed there, with
runtime-only persistence; the stack is on uat-stack. Whether the rows arrived is
asked of the stack's own tickerplant and rdb, from this host.
"""

from __future__ import annotations

from typing import Any

import pytest
from remote_world import (
    PUBLISHER,
    TABLE,
    World,
    attach_args,
    listed,
    live_rows,
    stages,
    stp_tables,
    wait_for,
)


@pytest.fixture(scope="module")
def attached(world: World) -> dict[str, Any]:
    code, doc = world.client.uqs_json(*attach_args(world, "feed-uat"), timeout=400)
    assert code == 0, doc
    return doc


def test_the_attachment_is_verified_and_its_publisher_started_and_reached(attached):
    assert attached["ok"] is True
    done = stages(attached)
    assert [done[s]["status"] for s in ("apply", "verify", "start", "reach")] == [
        "done",
        "done",
        "done",
        "done",
    ], done
    # Both directions since #1672: the publisher opens discovery and the
    # tickerplant with its own credential, and discovery opens the publisher.
    assert done["reach"]["detail"] == "the publisher and the stack reach each other", done["reach"]
    assert attached["verify"]["state"] == "verified"


def test_the_rows_reach_the_stacks_tickerplant_and_rdb(world: World, attached):
    assert TABLE in stp_tables(world), "registered on the stack's tickerplant"
    before = live_rows(world)
    wait_for(lambda: live_rows(world) >= before + 6, 60, "rdb1 receiving the feed's rows")
    rows = world.q("stp1", f"@[value;`.stplg.rowcount;()!()]`{TABLE}")
    assert int(rows) >= 6, f"the tickerplant counted {rows} {TABLE} rows"
    syms = sorted(str(s) for s in world.q("rdb1", f"exec distinct sym from {TABLE}"))
    assert syms == ["EURUSD", "GBPUSD", "USDJPY"]


def test_discovery_holds_a_handle_to_the_publisher_on_the_client(world: World, attached):
    port = attached["plan"]["publisher_port"]
    assert (PUBLISHER, f":uat-client:{port}", True) in listed(world, "uat-client")


def test_the_client_runs_no_stack_of_its_own(world: World, attached):
    """So the rows could only have gone to uat-stack."""
    procs = world.client.sh("ps -eo args").stdout
    assert f"-procname {PUBLISHER}" in procs
    for name in ("discovery1", "stp1", "rdb1"):
        assert f"-procname {name} " not in procs, f"{name} runs on the client"
