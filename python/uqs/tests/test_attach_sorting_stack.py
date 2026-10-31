"""#1517 on a REAL TorQ 5.2.12 stack: a declared sort rule, attached, sorts the
day's partition at end of day.

The stack's sort.csv (its own copy, in the isolated starter pack's appconfig)
sorts by default on `securityId`, which the attached table lacks; the vendored
tables keep rows of their own. wdb1 saves, sort1 sorts at end of day - two
processes reading one file. Attaching writes the declared rows, reloads both,
and end of day then sorts the table by sym and time with `p#` on sym. Detach
before publication takes the rows out and both processes back to the default.

Opt-in like the other stack tests (`-m live_stack`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attach_stack_support import CORE, IsolatedStack, requires_stack
from attach_support import LEDGER_Q, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, plan, service, verify
from uqs.attach import detach as detaching
from uqs.paths import repo_root

pytestmark = requires_stack()
ROOT = repo_root()
TABLE = "piggybank_general_ledger"
SORT_CSV = (
    "tabname,att,column,sort\n"
    "default,p,securityId,1\n"
    "trade,p,sym,1\ntrade,,time,1\nquote,p,sym,1\nquote,,time,1\n"
)
DECLARED = f"sorting[`{TABLE};`sym`time!`p`]"


class Stopped:
    def running(self, att) -> bool:
        return False

    def start(self, att) -> None:
        raise AssertionError("the test publishes itself")

    def stop(self, att) -> None:
        pass


@pytest.fixture
def stack(tmp_path):
    s = IsolatedStack(tmp_path / "stack")
    (s.pack / "appconfig" / "sort.csv").write_text(SORT_CSV)
    try:
        s.start(*CORE)
        yield s
    finally:
        s.teardown()


def attachment(stack, tmp_path, tables_q: str, name: str = "pb-uat"):
    return service.create(
        name,
        "pb_general_ledger",
        make_bundle(tmp_path / name, tables_q),
        stack.port,
        ROOT,
        host="127.0.0.1",
        runtime_only=True,
        discovery=f"127.0.0.1:{stack.port + 1}",
    )


def live(stack, proc: str) -> list[str]:
    rows = stack.q(proc, f"{{exec string column from .sort.params where tabname=`{TABLE}}}", None)
    return [str(r) for r in rows]


def test_a_declared_rule_is_attached_and_end_of_day_sorts_by_it(stack, tmp_path):
    csv = stack.pack / "appconfig" / "sort.csv"
    bare = plan.build(attachment(stack, tmp_path, LEDGER_Q, "bare"), stack.ipc(), ROOT)
    assert any(f.code == "SORT_CONFIG" for f in bare.findings if f.level == "blocker")

    att = attachment(stack, tmp_path, LEDGER_Q + "\n" + DECLARED)
    p = plan.build(att, stack.ipc(), ROOT)
    assert p.ok, [f for f in p.findings if f.level == "blocker"]
    (change,) = p.sort_changes
    assert change.status == "add" and Path(change.path).resolve() == csv.resolve()
    assert {r.procname for r in change.readers} == {"wdb1", "sort1"}
    assert csv.read_text() == SORT_CSV, "planning wrote nothing"

    state = stack.root / "attachments"
    config.save(att, state)
    assert applying.apply(att, stack.ipc(), ROOT, state_dir=state).state == "applied"
    assert csv.read_text() == SORT_CSV + f"{TABLE},p,sym,1\n{TABLE},,time,1\n"
    assert live(stack, "wdb1") == ["sym", "time"] and live(stack, "sort1") == ["sym", "time"]
    checked = verify.verify(att, stack.ipc(), state, ROOT)
    assert checked.ok, [c for c in checked.checks if not c.ok]

    rows = "(`B`A`B`A;`a1`a2`a3`a4;1 2 3 4f;`n1`n2`n3`n4)"
    stack.q("stp1", "{.u.upd[`" + TABLE + ";" + rows + "]}", None)
    stack.flush()
    stack.end_of_day(25)
    days = sorted(p for p in stack.hdb_dir.iterdir() if (p / TABLE).is_dir())
    assert len(days) == 1
    part = days[0] / TABLE
    syms = stack.q("hdb1", "{string get hsym x}", str(part / "sym"))
    assert [str(s) for s in syms] == ["A", "A", "B", "B"], "sorted by sym, as declared"
    assert str(stack.q("hdb1", "{attr get hsym x}", str(part / "sym"))) == "p"


def test_detach_before_publication_puts_the_file_and_the_live_rules_back(stack, tmp_path):
    csv = stack.pack / "appconfig" / "sort.csv"
    state = stack.root / "attachments"
    att = attachment(stack, tmp_path, LEDGER_Q + "\n" + DECLARED)
    # saved with the storage the plan saw, as `uqs job attachment add` does: detach reads
    # it for evidence
    service.save_planned(att, plan.build(att, stack.ipc(), ROOT), state)
    att = config.load("pb-uat", state)
    applying.apply(att, stack.ipc(), ROOT, state_dir=state)
    result = detaching.detach(att, stack.ipc(), Stopped(), state_dir=state)
    assert result.outcome == "rolled_back", result.blockers
    assert csv.read_text() == SORT_CSV
    assert live(stack, "wdb1") == [] and live(stack, "sort1") == []
