"""#1650: a backfill extracted on uat-client and written by the stack's own receiver.

The receiver runs on uat-stack (`uqs job receive`), the extractor on uat-client
(`uqs job backfill --writer uat-stack:PORT`), both under the attachment of the
same name. The rows must end up in the STACK's HDB, served by its hdb1, and the
client must hold no HDB of its own.

The receiver writes the HDB the attachment pinned from the stack's wdb, so it
needs a running wdb. PeachQ cannot run TorQ's wdb yet (wdb.q fails to load on
`.Q.chk`), and on a PeachQ stack this skips, naming the wdb's own error.
"""

from __future__ import annotations

import pytest
from remote_world import BUNDLE, FEED_JOB, TABLE, World

WORKER = "remote_trades_backfill"
DAY, NEXT = "2026-01-05", "2026-01-06"
#: one trade per pair every ten minutes, for a whole day
ROWS = 3 * 6 * 24


@pytest.fixture(scope="module")
def receiving(world: World) -> World:
    if not world.answers("wdb1"):
        err = world.stack.sh("grep -h ERR /data/uqs-peachq/logs/err_wdb1*.log | tail -1")
        pytest.skip(
            "the stack has no wdb, so its attachment pins no HDB for a receiver to write: "
            f"{err.stdout.strip() or 'wdb1 does not answer'}"
        )
    local = world.stack.uqs(
        "job", "attach", "feed-uat", "--job", FEED_JOB, "--bundle", BUNDLE,
        "--stack-id", str(world.base), "--consumer", "rdb1", "--consumer", "wdb1",
        "--runtime-only", "--no-start", "--yes",
    )  # fmt: skip
    assert local.returncode == 0, local.stdout + local.stderr
    started = world.stack.uqs(
        "job", "receive", WORKER, "--attach", "feed-uat", "--port", str(world.receiver_port)
    )
    assert started.returncode == 0, started.stdout + started.stderr
    return world


def test_the_extracted_rows_are_written_into_the_stacks_hdb(receiving: World):
    done = receiving.client.uqs(
        "job", "backfill", WORKER, "--attach", "feed-uat",
        "--writer", f"uat-stack:{receiving.receiver_port}",
        "--from", DAY, "--to", NEXT, "--version", "remote-lane-v1", "--wait",
        env={"UQF_SOURCE_CRED_REMOTE_TRADES": "42"}, timeout=600,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    day = DAY.replace("-", ".")
    served = receiving.q("hdb1", f"exec count i from {TABLE} where date={day}")
    assert int(served) == ROWS, "hdb1 serves the backfilled day"
    assert receiving.stack.sh(f"ls -d /data/*/hdb/{day}/{TABLE}").returncode == 0


def test_the_extracting_machine_holds_no_hdb(receiving: World):
    day = DAY.replace("-", ".")
    assert receiving.client.sh(f"ls -d /data/*/hdb/{day}/{TABLE}").returncode != 0
