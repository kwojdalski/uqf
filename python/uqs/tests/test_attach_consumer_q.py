"""consumer_attach.q and consumer_unsubscribe.q against TorQ's own subscriptions.q.

The lambdas are the exact text `uqs` sends (remote.source), run in one q that
loads the vendored lib/torq/code/common/subscriptions.q with the few TorQ globals
it needs stubbed. The "tickerplant" is the same process, reached on handle 0, so
`.sub.subscribe` runs end to end - reducesubs, createtables, replay and
updatesubscriptions - with no stack and no licence (it runs on PeachQ too).

#1445: updatesubscriptions deletes EVERY inactive subscription row, the rows
`.sub.autoreconnect` resubscribes from; attaching one table must leave them.
#1459: a table the consumer already held but had no subscription for is answered
`subscribed`, and consumer_unsubscribe takes back exactly that subscription.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from uqs.attach.remote import source
from uqs.paths import repo_root

SUBSCRIPTIONS_Q = repo_root() / "lib" / "torq" / "code" / "common" / "subscriptions.q"
COLUMNS = [["time", "p"], ["sym", "s"], ["amount", "f"]]
LEDGER = "L:([]time:`timestamp$();sym:`symbol$();amount:`float$())"

_PRELUDE = """.lg.o:{[x;y]};.lg.e:{[x;y]};
.proc.cp:{.z.p};.proc.lowpowermode:0b;
.dotz.set:{[x;y]};.dotz.getcommand:{x};
.timer.enabled:0b;.servers.enabled:0b;
system "l ",getenv`UQS_SUBSCRIPTIONS_Q;
.proc.proctype:`rdb;.proc.procname:`rdb1;.proc.params:()!();
.rdb.tickerplanttypes:enlist `segmentedtickerplant;.rdb.subtables:`quote`trade;
/ the tickerplant, answering on handle 0: a segmented one publishing quote, trade and L
tptype:`segmented;
tablelist:{`quote`trade`L};
K:`schemalist`logfilelist`rowcounts`date`logdir;
subdetails:{[t;i] K!(enlist (`L;TPSCHEMA);();()!();.z.d;`:/tmp)};
TPSCHEMA:([]time:`timestamp$();sym:`symbol$();amount:`float$());
H:([]procname:enlist `stp1;proctype:`segmentedtickerplant;w:enlist 0i);
.sub.getsubscriptionhandles:{[a;b;c] H};
/ quote and L were subscribed from stp2, whose connection then dropped (.z.pc marks them inactive);
/ trade is live from stp1
.sub.updatesubscriptions[`procname`proctype`w!(`stp2;`segmentedtickerplant;7i);;`] each `quote`L;
.sub.updatesubscriptions[`procname`proctype`w!(`stp1;`segmentedtickerplant;0i);`trade;`];
.sub.pc[::;7i];
op:{value "\\n" sv read0 hsym `$getenv x};
attach:op`UQS_ATTACH_Q;
unsubscribe:op`UQS_UNSUBSCRIBE_Q;
subs:{.j.j update string createdtime from .sub.SUBSCRIPTIONS};
out:{-1 "OUT ",.j.j x};
"""


def run_q(q_binary, tmp_path: Path, body: str) -> list[Any]:
    """Run the prelude then `body`; every `out x` line, decoded."""
    q, env = q_binary
    (tmp_path / "attach.q").write_text(source("consumer_attach") + "\n")
    (tmp_path / "unsubscribe.q").write_text(source("consumer_unsubscribe") + "\n")
    script = tmp_path / "run.q"
    script.write_text(_PRELUDE + body + "\nexit 0\n")
    run = subprocess.run(
        [q, str(script), "-q"],
        env={
            **env,
            "UQS_SUBSCRIPTIONS_Q": str(SUBSCRIPTIONS_Q),
            "UQS_ATTACH_Q": str(tmp_path / "attach.q"),
            "UQS_UNSUBSCRIBE_Q": str(tmp_path / "unsubscribe.q"),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = [json.loads(line[4:]) for line in run.stdout.splitlines() if line.startswith("OUT ")]
    assert lines, f"q printed nothing:\n{run.stdout}\n{run.stderr}"
    return lines


def qstr(obj: Any) -> str:
    """`obj` as JSON in a q string literal."""
    return '"' + json.dumps(obj).replace('"', '\\"') + '"'


def req(**extra: Any) -> str:
    base = {"table": "L", "columns": COLUMNS, "token": "tok", "attachment": "pb-uat", "join": True}
    return qstr(base | extra)


def rows(subs: str) -> list[dict]:
    return json.loads(subs)


def test_attaching_a_new_table_keeps_every_other_inactive_subscription(q_binary, tmp_path):
    """#1445: before the fix, stp2's inactive quote and L rows were deleted by the
    subscribe, so stp2 coming back would never resubscribe them."""
    before, answer, after = run_q(q_binary, tmp_path, f"out subs[];out attach {req()};out subs[];")
    assert json.loads(answer)["status"] == "created"
    assert any(not r["active"] for r in rows(before)), "the scenario has inactive rows"
    new = rows(after)
    assert new[: len(rows(before))] == rows(before), "every row as it was, in order"
    added = new[len(rows(before)) :]
    assert [(r["procname"], r["table"], r["active"]) for r in added] == [("stp1", "L", True)]


def test_a_failed_attach_leaves_the_subscriptions_exactly_as_they_were(q_binary, tmp_path):
    """The tickerplant hands back no usable schema: the subscribe ran its
    updatesubscriptions but the table did not take. Nothing may be lost."""
    before, answer, after = run_q(
        q_binary,
        tmp_path,
        f"TPSCHEMA:();out subs[];out attach {req()};out subs[];",
    )
    assert json.loads(answer)["status"] == "failed"
    assert rows(after) == rows(before)


def test_a_held_table_without_a_subscription_is_answered_subscribed(q_binary, tmp_path):
    """#1459: rdb1 holds L (one row) but no live subscription for it. Attaching
    subscribes it and says so - not `existing` - and reports it added L to
    .rdb.subtables, so the rollback knows what to take back."""
    body = f"{LEDGER};`L insert (.z.p;`AAA;1.5);out attach {req()};out count L;out .rdb.subtables;"
    answer, n, subtables = run_q(q_binary, tmp_path, body)
    got = json.loads(answer)
    assert got["status"] == "subscribed"
    assert got["subtables"] is True
    assert n == 1
    assert "L" in subtables


def test_unsubscribing_takes_back_exactly_the_subscription_attach_added(q_binary, tmp_path):
    """#1459: after attach answered `subscribed`, consumer_unsubscribe for stp1
    leaves the table, its rows, stp2's inactive L row and every other row as
    they were before the attach, and drops the .rdb.subtables entry attach added."""
    unsub = qstr({"table": "L", "tickerplant": "stp1", "dry_run": False, "subtables": True})
    dry = qstr({"table": "L", "tickerplant": "stp1", "dry_run": True, "subtables": True})
    body = (
        f"{LEDGER};`L insert (.z.p;`AAA;1.5);"
        "out subs[];out .rdb.subtables;"
        f"out attach {req()};"
        f"out unsubscribe {dry};out subs[];"
        f"out unsubscribe {unsub};"
        "out subs[];out .rdb.subtables;out count L;out 98h=type L;"
        f"out unsubscribe {unsub};"
    )
    before, st_before, attached, dry_ans, mid, ans, after, st_after, n, table, again = run_q(
        q_binary, tmp_path, body
    )
    assert json.loads(attached)["status"] == "subscribed"
    assert json.loads(dry_ans) == {"status": "dry_run", "problems": []}
    assert any(r["table"] == "L" and r["procname"] == "stp1" for r in rows(mid)), "dry run kept it"
    assert json.loads(ans) == {"status": "removed", "problems": []}
    assert rows(after) == rows(before)
    assert any(r["table"] == "L" and r["procname"] == "stp2" for r in rows(after))
    assert st_after == st_before
    assert (n, table) == (1, True)
    assert json.loads(again) == {"status": "absent", "problems": []}


def test_unsubscribing_without_a_tickerplant_is_refused(q_binary, tmp_path):
    bare = qstr({"table": "L", "dry_run": False})
    (answer,) = run_q(q_binary, tmp_path, f"out unsubscribe {bare};")
    assert json.loads(answer)["status"] == "refused"


@pytest.fixture(autouse=True)
def _vendored_torq() -> None:
    if not SUBSCRIPTIONS_Q.is_file():
        pytest.skip(f"{SUBSCRIPTIONS_Q} is not vendored in this checkout")
