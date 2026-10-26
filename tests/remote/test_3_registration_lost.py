"""A tickerplant restart on the stack's machine drops a runtime-only registration.

Nothing on the client is told (attach-existing-torq.md, "A publisher on this
machine, a stack on another"). The operator stops the publisher, the stack's
tickerplant and rdb are restarted, and then:

- `uqs job attach NAME --dry-run` reports the registration lost;
- a bare `uqs start` of the publisher is refused, naming the re-apply (#1673);
- `uqs job attach NAME` registers the table again, and rows flow.
"""

from __future__ import annotations

import pytest
from remote_world import PUBLISHER, TABLE, World, live_rows, stages, stp_tables, wait_for


@pytest.fixture(scope="module")
def restarted(world: World) -> World:
    stopped = world.client.uqs("stop", PUBLISHER, "--port", str(world.base))
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert world.stack_uqs("stop", "stp1").returncode == 0
    assert world.stack_uqs("stop", "rdb1").returncode == 0
    for proc in ("stp1", "rdb1"):
        started = world.stack_uqs("start", proc)
        assert started.returncode == 0, started.stdout + started.stderr
    world.wait_answering("stp1", "rdb1")
    assert TABLE not in stp_tables(world), "a runtime-only registration does not survive"
    return world


def test_a_dry_run_reports_the_registration_lost(restarted: World):
    code, doc = restarted.client.uqs_json("job", "attach", "feed-uat", "--dry-run")
    assert "registration-lost" in str(doc), doc
    assert TABLE not in stp_tables(restarted), "a dry run changes nothing"


def test_a_bare_start_is_refused_naming_the_reapply(restarted: World):
    done = restarted.client.uqs("start", PUBLISHER, "--port", str(restarted.base))
    assert done.returncode != 0, done.stdout
    assert "uqs job attach feed-uat" in done.stdout + done.stderr
    running = restarted.client.sh(f"ps -eo args | grep -q '[-]procname {PUBLISHER}'")
    assert running.returncode != 0, "the publisher was not started"
    assert TABLE not in stp_tables(restarted)


def test_reapplying_registers_the_table_again_and_rows_flow(restarted: World):
    code, doc = restarted.client.uqs_json("job", "attach", "feed-uat", "--yes", timeout=400)
    assert code == 0, doc
    assert stages(doc)["reach"]["status"] == "done", stages(doc)
    assert TABLE in stp_tables(restarted)
    wait_for(lambda: live_rows(restarted) >= 6, 60, "rdb1 receiving rows after the restart")
