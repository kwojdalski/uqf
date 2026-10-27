"""#1650: a backfill extracted on uat-client and written by the stack's own receiver.

The receiver runs on uat-stack (`uqs job receive`), the extractor on uat-client
(`uqs job backfill --writer uat-stack:PORT`), both under the attachment of the
same name. The rows must end up in the STACK's HDB - counted off uat-stack's
disk - coverage must be filed on the client, a rerun must write nothing, and the
client must hold no HDB of its own. hdb1 must serve the day where it can map a
partitioned HDB; a PeachQ hdb1 maps none, and there the run must say it is
written but not queryable, apart from the durable write.

uat-stack has NO copy of the job (#1717): its checkout lacks the bundle that
defines remote_trades' source, transform and worker. The worker's receiver
contract is exported on uat-client, carried to uat-stack with the attachment,
approved there, and the receiver is started by that contract's identity.

The receiver writes the HDB the attachment pinned from the stack's wdb, and is
coordinated with that wdb, so it needs one running. TorQ's wdb calls `.Q.chk`
as it starts, which PeachQ lacks: the stack's PeachQ runtime defines it from
scripts/peachq_init.q (#1650). A wdb that does not answer fails the scenario,
with its own error log, rather than skipping it.
"""

from __future__ import annotations

import pytest
from remote_world import BUNDLE, TABLE, World, copy_between

WORKER = "remote_trades_backfill"
DAY, NEXT = "2026-01-05", "2026-01-06"
DAY_Q = DAY.replace("-", ".")
#: one trade per pair every ten minutes, for a whole day
ROWS = 3 * 6 * 24


@pytest.fixture(scope="module")
def receiving(world: World) -> World:
    # Without a wdb the attachment pins no HDB for a receiver to write: fails,
    # with the tail of the wdb's error log.
    world.wait_answering("wdb1", seconds=60)
    # the attachment the client made, carried to the stack's machine as it stands
    copy_between(world.client, world.stack, "/data/attachments", "feed-uat")
    exported = world.client.uqs(
        "job", "contract", "export", WORKER, "--output", "/tmp/remote_trades.contract.json"
    )
    assert exported.returncode == 0, exported.stdout + exported.stderr
    copy_between(world.client, world.stack, "/tmp", "remote_trades.contract.json")
    code, approved = world.stack.uqs_json(
        "job", "contract", "approve", "/tmp/remote_trades.contract.json", "--attach", "feed-uat"
    )
    assert code == 0, approved
    world.contract = approved["contract"]["identity"]
    started = world.stack.uqs(
        "job", "receive", "--contract", world.contract, "--attach", "feed-uat",
        "--port", str(world.receiver_port),
    )  # fmt: skip
    assert started.returncode == 0, started.stdout + started.stderr
    return world


def backfill(world: World):
    return world.client.uqs(
        "job", "backfill", WORKER, "--attach", "feed-uat",
        "--writer", f"uat-stack:{world.receiver_port}",
        "--from", DAY, "--to", NEXT, "--version", "remote-lane-v1", "--wait",
        env={"UQF_SOURCE_CRED_REMOTE_TRADES": "42"}, timeout=600,
    )  # fmt: skip


def rows_on_stack_disk(world: World) -> int:
    """The day's rows in the stack's HDB partition, read off its disk by a q on
    uat-stack - not through hdb1, and not through anything uqs wrote."""
    script = """'-1 string count get hsym `$(getenv `D),"/sym";' 'exit 0'"""
    found = world.stack.sh(
        f"export D=$(ls -d /data/*/hdb/{DAY_Q}/{TABLE}) && "
        f"printf '%s\\n' {script} > /tmp/rows.q && peachq /tmp/rows.q"
    )
    assert found.returncode == 0, found.stdout + found.stderr
    return int(found.stdout.strip().splitlines()[-1])


#: The rows the first run wrote, and what it said.
@pytest.fixture(scope="module")
def backfilled(receiving: World):
    return backfill(receiving)


def test_the_extracted_rows_are_written_into_the_stacks_hdb(receiving: World, backfilled):
    # 0: written and queryable; 3: written and covered, not queryable (below)
    assert backfilled.returncode in (0, 3), backfilled.stdout + backfilled.stderr
    assert rows_on_stack_disk(receiving) == ROWS, "the stack's own HDB holds the whole day"


def test_coverage_is_filed_on_the_extracting_machine(receiving: World, backfilled):
    filed = receiving.client.sh("ls /data/*/status/attached/feed-uat-*/etl_coverage")
    assert filed.returncode == 0, "coverage moved once the receiver committed"


def test_hdb1_serves_the_day_or_the_reload_is_reported_apart(receiving: World, backfilled):
    """Queryable is hdb1's to say. A PeachQ hdb1 maps no partitioned table at all
    (`\\l` of a partitioned database leaves .Q.pt empty: not even the starter
    pack's trade and quote, which its sample partitions hold), so there the run
    must end written-but-not-queryable, reported apart from the durable write.
    A hdb1 that maps partitioned tables must serve the day."""
    if len(receiving.q("hdb1", ".Q.pt")):
        assert backfilled.returncode == 0, backfilled.stdout
        count = receiving.q("hdb1", f"exec count i from {TABLE} where date={DAY_Q}")
        assert int(count) == ROWS, "hdb1 serves the backfilled day"
        return
    held = receiving.stack.sh("ls -d /data/*/hdb/2015.01.07/trade")
    assert held.returncode == 0, "the sample partitions are there; hdb1 maps none of them"
    assert backfilled.returncode == 3, backfilled.stdout
    said = backfilled.stdout
    assert "written_not_queryable" in said and "WRITTEN and covered but NOT queryable" in said
    assert f"hdb1: reloaded, but its HDB does not define {TABLE}" in said


def test_a_rerun_rewrites_nothing(receiving: World, backfilled):
    again = backfill(receiving)
    assert again.returncode == backfilled.returncode, again.stdout + again.stderr
    assert rows_on_stack_disk(receiving) == ROWS, "a covered day is not sent again"


def test_the_extracting_machine_holds_no_hdb(receiving: World, backfilled):
    assert receiving.client.sh(f"ls -d /data/*/hdb/{DAY_Q}/{TABLE}").returncode != 0


def test_the_stacks_machine_has_none_of_the_jobs_code(receiving: World, backfilled):
    """What wrote the rows had no source, transform or worker of the job to load."""
    assert receiving.stack.sh(f"ls {BUNDLE}").returncode != 0, "no bundle in its checkout"
    found = receiving.stack.sh("grep -rl remote_trades_backfill src scripts/dev || true")
    assert found.stdout.strip() == "", f"no definition of the worker: {found.stdout}"
    logs = "/data/*/status/attached/feed-uat-*/logs"
    log = receiving.stack.sh(f"cat {logs}/remote_trades.receiver-*.log")
    assert f"under contract {receiving.contract}" in log.stdout, log.stdout + log.stderr
