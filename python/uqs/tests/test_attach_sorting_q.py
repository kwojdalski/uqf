"""The declared sort rule through TorQ's OWN sort code (#1517).

One q process loads the vendored lib/torq/code/common/dbwriteutils.q - the
`.sort` namespace every TorQ writer sorts with at end of day - with the few
TorQ globals it needs stubbed, and starts the way a wdb does: its sort.csv read
into `.sort.params` by `.sort.getsortcsv`. The file's only rule is a default on
`securityId`, which the attached table does not have.

Against that process, over IPC and with the lambdas `uqs` sends: plan the
declared rule, write it, reload it with sort_reload.q, and then have TorQ's
`.sort.sorttab` sort a partition of the table on disk - which it could not do
before - and take the rule back out. Skips when there is no q.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs.attach import sort_plan, sorting
from uqs.attach.config import Attachment, Publisher, Stack
from uqs.attach.facts import Facts
from uqs.attach.manifest import Manifest
from uqs.attach.remote import Endpoint, KolaIpc
from uqs.attach.schema import TableSpec
from uqs.attach.topology import Observation, Proc
from uqs.paths import repo_root

DBWRITEUTILS = repo_root() / "lib" / "torq" / "code" / "common" / "dbwriteutils.q"
TABLE = "ledger_book"
ORIGINAL = "tabname,att,column,sort\ndefault,p,securityId,1\n"
SPEC = TableSpec(
    TABLE,
    (("time", "p"), ("sym", "s"), ("amount", "f")),
    (("sym", "p"), ("time", "")),
)
#: A wdb as far as `.sort` and the attach lambdas are concerned.
_WDB = """.lg.o:{[x;y]};.lg.e:{[x;y] -1 "ERROR ",string[x],": ",y};
.proc.getconfigfile:{enlist `$getenv`UQS_SORT_CSV};
.proc.procname:`wdb1;.proc.proctype:`wdb;.proc.params:enlist[`stackid]!enlist enlist "6000";
.proc.getversion:{"5.2.12"};
system "l ",getenv`UQS_DBWRITEUTILS;
.wdb.sortcsv:hsym `$getenv`UQS_SORT_CSV;
.wdb.sorttypes:enlist `sort;.wdb.sortworkertypes:`$();.wdb.mode:`saveandsort;
.wdb.tablelist:{tables[]};
.sort.getsortcsv .wdb.sortcsv;
"""


@pytest.fixture
def wdb(tmp_path, monkeypatch, start_q, q_binary):
    csv = tmp_path / "config" / "sort.csv"
    csv.parent.mkdir()
    csv.write_text(ORIGINAL)
    boot = tmp_path / "wdb.q"
    boot.write_text(_WDB)
    monkeypatch.setenv("UQS_SORT_CSV", str(csv))
    monkeypatch.setenv("UQS_DBWRITEUTILS", str(DBWRITEUTILS))
    with start_q(str(boot)) as port:
        yield Endpoint("wdb1", "127.0.0.1", port), csv, tmp_path


def q(endpoint: Endpoint, expr: str, *args):
    import kola

    conn = kola.Q(endpoint.host, endpoint.port, user="", passwd="")
    try:
        return conn.sync(expr, *args)
    finally:
        conn.disconnect()


def sort_partition(endpoint: Endpoint, db: Path) -> tuple[list[str], str, bool]:
    """Write an unsorted day of the table to disk, sort it with TorQ's `.sort.sorttab`,
    and read back: its syms in order, sym's attribute, and whether time rises per sym."""
    expr = (
        "{[db] d:hsym db; p:` sv d,`2026.10.21,`" + TABLE + ",`;"
        "t:([] time:2026.10.21D10:00 2026.10.21D09:00 2026.10.21D11:00 2026.10.21D08:00;"
        " sym:`B`A`B`A; amount:1 2 3 4f);"
        "p set .Q.en[d;t];"
        ".sort.sorttab (`" + TABLE + ";p);"
        "r:get p;"
        "(string r`sym;attr r`sym;all {x~asc x} each value exec time by sym from r)}"
    )
    syms, att, rising = q(endpoint, expr, str(db))
    return [str(s) for s in syms], str(att), bool(rising)


def attachment() -> Attachment:
    stack = Stack(6000, "localhost", "localhost", 6001)
    return Attachment("pb-uat", stack, Publisher("pb_general_ledger", "pb_ledger1"), (SPEC,))


def observe(ipc: KolaIpc, endpoint: Endpoint) -> Observation:
    disc = Proc("discovery", "discovery", Endpoint("discovery", "localhost", 6001), {})
    wdb = Proc("wdb1", "wdb", endpoint, ipc.call(endpoint, "probe", {"tables": [TABLE]}))
    return Observation(disc, consumers=[wdb])


def test_the_declared_rule_is_written_reloaded_and_sorts_with_torqs_own_code(wdb):
    endpoint, csv, tmp = wdb
    ipc = KolaIpc("", "")
    # The premise: with only the default rule, TorQ cannot sort the table at all.
    syms, att, _ = sort_partition(endpoint, tmp / "before")
    assert syms == ["B", "A", "B", "A"] and att == "", "left as it arrived: unsorted, no p#"

    att_ = attachment()
    changes, found = sort_plan.assess(att_, observe(ipc, endpoint), ipc, Facts(), repo_root())
    assert not [f for f in found if f.level == "blocker"], found
    (change,) = changes
    assert change.status == "add" and [r.procname for r in change.reload] == ["wdb1"]
    assert change.path == str(csv)

    m = Manifest.new(att_, tmp / "state" / "manifest.json")
    steps = sorting.apply_change(m, ipc, change, change.sha, tmp / "state" / "backups")
    assert [s[3] for s in steps] == ["created", "reloaded"]
    assert csv.read_text() == ORIGINAL + f"{TABLE},p,sym,1\n{TABLE},,time,1\n"
    assert sorting.checks(att_, observe(ipc, endpoint), ipc) and all(
        ok for _, ok, _ in sorting.checks(att_, observe(ipc, endpoint), ipc)
    )

    # End of day's sort, as TorQ runs it, with the rule the attachment added.
    syms, att, rising = sort_partition(endpoint, tmp / "after")
    assert syms == ["A", "A", "B", "B"] and att == "p" and rising

    # Detach: the rows go, the reload is confirmed live, and the file is as it was.
    op = m.find("sort_rows", str(csv), TABLE)
    assert op is not None
    reverted = sorting.revert(att_, observe(ipc, endpoint), ipc, m, op, tmp / "state" / "backups")
    assert [s[3] for s in reverted] == ["removed", "reloaded"] and op.status == "reverted"
    assert csv.read_text() == ORIGINAL
    live = ipc.call(endpoint, "probe", {"tables": []})["sortrows"]
    assert [r["col"] for r in live] == ["securityId"]


def test_a_reload_that_fails_keeps_the_rules_the_process_had(wdb):
    """`.sort.getsortcsv` validates the whole file before it replaces `.sort.params`:
    an unreadable file is reported, and the process keeps sorting as before."""
    endpoint, csv, _ = wdb
    ipc = KolaIpc("", "")
    csv.write_text("tabname,att,column,sort\ndefault,z,sym,1\n")  # z: not an attribute
    answer = ipc.call(endpoint, "sort_reload", {"table": "default"})
    assert answer["status"] == "failed" and "unrecognised type of attribute" in answer["detail"]
    live = ipc.call(endpoint, "probe", {"tables": []})["sortrows"]
    assert [(r["att"], r["col"]) for r in live] == [("p", "securityId")]
