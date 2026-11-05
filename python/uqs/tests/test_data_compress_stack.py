"""`uqs data compress`, end to end against a running stack's HDB on KDB-X (#2147, #2148).

A uqf-runtime stack of its own - its own data root, a free base port - with
the three processes the command needs: discovery1, hdb1 (the reader that must
reload) and housekeeping1, where TorQ's .cmp runs with the overlay
stack/procs.py gives it. Its HDB is what bootstrap makes: the starter pack's
two sample days (2015), filled with every table this tree declares, so every
partition is older than any policy's minimum age.

The plan is checked to change nothing; --apply to compress through .cmp,
partition by partition, with hdb1 reloaded and still answering the same rows,
and one audited row in the activity history; a second plan to find nothing
left. Opt-in (`-m live_stack`, conftest.py's OPT_IN): it starts processes and
needs a KDB-X licence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from attach_stack_support import pick_base_port, requires_stack

pytestmark = requires_stack()

REPO = Path(__file__).resolve().parents[3]
PROCS = ("discovery1", "hdb1", "housekeeping1")
TABLE = "market_data"


class Stack:
    def __init__(self, root: Path) -> None:
        self.port = pick_base_port()
        self.data = root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.env: dict[str, str] = {
            **os.environ,
            "UQS_DATA_ROOT": str(self.data),
            "UQS_RUNTIME": "uqf",
            "UQF_FRONTEND_ACTIVITY_DB": str(root / "activity.sqlite3"),
        }
        for k in ("UQS_TARGET", "UQF_PEACHQ", "UQF_Q_IMPL", "UQF_PEACHQ_REASON"):
            self.env.pop(k, None)

    @property
    def hdb(self) -> Path:
        return self.data / "uqs" / "hdb"

    def uqs(self, *args: str, timeout: float = 600) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", "from uqs.cli import main; main()", *args],
            cwd=REPO, env=self.env, capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip

    def compress(self, *args: str) -> dict:
        done = self.uqs("data", "compress", "--base-port", str(self.port), "--json", *args)
        assert done.stdout, done.stderr
        return json.loads(done.stdout)

    def q(self, offset: int, expr: str):
        import kola

        conn = kola.Q("127.0.0.1", self.port + offset, user="admin", passwd="admin", timeout=15000)
        try:
            return conn.sync(expr)
        finally:
            conn.disconnect()

    def wait(self, offset: int, seconds: float = 90) -> None:
        deadline = time.monotonic() + seconds
        while True:
            try:
                self.q(offset, "1")
                return
            except Exception:  # noqa: BLE001 - kola's errors are untyped
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def stop(self) -> None:
        self.uqs("stop", "all", "--base-port", str(self.port), timeout=120)


@pytest.fixture
def stack(tmp_path, q_binary):
    q, env = q_binary
    s = Stack(tmp_path)
    s.env.update({k: env[k] for k in ("QHOME", "QLIC") if env.get(k)})
    s.env["QCMD"] = q
    started = s.uqs("start", *PROCS, "--base-port", str(s.port))
    try:
        assert started.returncode == 0, started.stdout + started.stderr
        s.wait(3)  # hdb1
        s.wait(11)  # housekeeping1
        yield s
    finally:
        s.stop()


def _files(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_plan_then_the_run_on_a_live_hdb(stack):
    before = _files(stack.hdb)
    plan = stack.compress("--table", TABLE)
    assert plan["ok"] is True and plan["result"] is None
    assert _files(stack.hdb) == before, "the plan writes nothing"
    assert [p["policy"] for p in plan["policies"]] == ["declared"]
    todo = sorted({p["partition"] for p in plan["partitions"] if p["compress"]})
    assert todo == ["2015-01-07", "2015-01-08"]
    assert all(d["ok"] for d in plan["disk"])
    count = f"count select from {TABLE}"
    rows = stack.q(3, count)

    done = stack.compress("--table", TABLE, "--apply", "--yes")
    assert done["ok"] is True, done
    assert [p["partition"] for p in done["result"]["partitions"]] == todo
    assert done["result"]["failed"] is None

    cols = stack.q(3, f"1_cols {TABLE}")  # not the partition column, which has no file
    for day in ("2015.01.07", "2015.01.08"):
        for c in cols:
            f = stack.hdb / day / TABLE / str(c)
            state = stack.q(3, f'count -21!hsym `$"{f}"')
            assert state, f"{f} is compressed"
    assert stack.q(3, count) == rows, "hdb1 reloaded and reads the same rows"

    history = json.loads(
        stack.uqs("activity", "list", "--action", "hdb.compress", "--json").stdout
    )["events"]
    (event,) = history
    assert (event["outcome"], event["method"]) == ("succeeded", "CLI")
    assert [i["target"] for i in event["items"]] == todo

    again = stack.compress("--table", TABLE)
    assert not [p for p in again["partitions"] if p["compress"]], "nothing is left to compress"
