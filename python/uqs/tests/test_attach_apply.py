"""Apply, verify and publish on a REAL TorQ 5.2.12 stack (#1424).

Opt-in (UQS_ATTACH_STACK_TEST=1 / `python3 scripts/test.py attach-stack`). Each
test gets its own stack - the starter pack as it ships, its own ports and data
- because applying changes it; a stack starts in a few seconds. The publisher
is what a job's process does: `.u.upd` on the tickerplant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from attach_stack_support import CORE, IsolatedStack, requires_stack
from attach_support import FaultIpc, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, persist, service, verify
from uqs.attach.manifest import Manifest
from uqs.paths import repo_root
from uqs.stack import attach_state

pytestmark = requires_stack()
ROOT = repo_root()
TABLE = "piggybank_general_ledger"
ROWS = [["AAA", "BBB", "AAA"], ["acc1", "acc2", "acc1"], [1.5, 2.5, 3.5], ["n1", "n2", "n3"]]


@pytest.fixture
def stack(tmp_path):
    s = IsolatedStack(tmp_path / "stack")
    try:
        s.start(*CORE)
        yield s
    finally:
        s.teardown()


@pytest.fixture
def bundle(tmp_path):
    return make_bundle(tmp_path / "piggybank")


def att_for(stack, bundle, name="pb-uat", **kw):
    kw.setdefault("runtime_only", False)
    kw.setdefault("discovery", f"127.0.0.1:{stack.port + 1}")
    return service.create(
        name, "pb_general_ledger", bundle, stack.port, ROOT, host="127.0.0.1", **kw
    )


def go(stack, att, ipc=None):
    config.save(att, stack.root / "attachments")
    return applying.apply(att, ipc or stack.ipc(), ROOT, state_dir=stack.root / "attachments")


def state(stack):
    return stack.root / "attachments"


def facts_of(stack) -> dict:
    """What the stack holds about the table, from the processes themselves."""
    tp = stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": [TABLE]})
    rdb = stack.ipc().call(stack.endpoint("rdb1"), "probe", {"tables": [TABLE]})
    wdb = stack.ipc().call(stack.endpoint("wdb1"), "probe", {"tables": [TABLE]})
    return {
        "tp_tables": sorted(tp["tables"]),
        "published": sorted(tp["published"]),
        "subscribers": tp["subscribers"][TABLE],
        "logs": [f["name"] for f in tp["logfiles"][TABLE]],
        "owned": sorted(tp["owned"]),
        "rdb": [sorted(rdb["tables"]), rdb["subscribed"], rdb["subtables"]],
        "wdb": [sorted(wdb["tables"]), wdb["subscribed"], wdb["tablelist"]],
    }


# --------------------------------------------------------- publication and persistence


def test_apply_verify_publish_and_the_rows_reach_every_consumer_and_the_hdb(stack, bundle):
    att = att_for(stack, bundle)
    assert TABLE not in stack.q("stp1", "string each tables[]")
    result = go(stack, att)
    assert result.state == "applied" and result.mutations() == 4
    for name in ("stp1", "rdb1", "wdb1"):
        assert stack.rows(name, TABLE) == 0
    checked = verify.verify(att, stack.ipc(), state(stack))
    assert checked.ok, [c for c in checked.checks if not c.ok]
    assert stack.rows("stp1", TABLE) == 0 and int(stack.q("stp1", f".stplg.msgcount`{TABLE}")) == 0

    stack.publish(TABLE, ROWS)
    stack.flush()
    assert stack.rows("rdb1", TABLE) == 3 and stack.rows("wdb1", TABLE) == 3
    tp = stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": [TABLE]})
    assert tp["msgcount"][TABLE] == 1 and tp["rowcount"][TABLE] == 3
    assert tp["logfiles"][TABLE][0]["size"] > 8, "the rows are in the tickerplant log"

    stack.end_of_day()
    assert stack.rows("rdb1", TABLE) == 0 and stack.rows("wdb1", TABLE) == 0
    partitions = sorted(p for p in stack.hdb_dir.iterdir() if (p / TABLE).is_dir())
    assert len(partitions) == 1, "the first end of day wrote the table's partition"
    total = int(stack.q("hdb1", f"count select from {TABLE} where date={partitions[0].name}"))
    assert total == 3, "downstream persistence: the three rows are in the HDB"
    day = partitions[0].name
    rows = stack.q("hdb1", f"select sym from {TABLE} where date={day}")  # exec is 'nyi on a p#
    assert sorted(set(rows["sym"].to_list())) == ["AAA", "BBB"], "they are the rows published"


def test_publication_is_replayable_from_the_tickerplant_log(stack, bundle):
    att = att_for(stack, bundle)
    go(stack, att)
    stack.publish(TABLE, ROWS)
    stack.flush()
    listed = stack.q("stp1", f".j.j .stplg.replaylog[enlist `{TABLE}]")
    (count, logname), *_ = json.loads(listed)
    assert logname.endswith(tuple("0123456789")) and Path(logname.lstrip(":")).is_file()
    replayed = stack.q("stp1", f'count -11!(-2;`$"{logname.lstrip(":")}")')
    assert int(replayed) == 1, "the log holds the one message a subscriber would replay"
    assert count == 1


# ------------------------------------------------------------- nothing existing moves


def test_existing_tables_subscriptions_buffered_rows_and_log_handles_are_untouched(stack, bundle):
    handles = lambda: stack.q("stp1", ".j.j exec tbl!handle from currlog")  # noqa: E731
    subs = lambda: stack.q("stp1", ".j.j count each .stpps.subrequestall")  # noqa: E731
    stack.q("stp1", 'system"t 0"')  # nothing is published while apply runs
    stack.q("stp1", '.u.upd[`trade;(`IBM`MSFT;10 20f;100 200i;00b;"aa";"NN";`x`y)]')
    buffered = int(stack.q("stp1", "count trade"))
    assert buffered == 2
    before = (json.loads(handles()), json.loads(subs()), stack.rows("rdb1", "trade"))
    go(stack, att_for(stack, bundle))
    after = (json.loads(handles()), json.loads(subs()), stack.rows("rdb1", "trade"))
    assert int(stack.q("stp1", "count trade")) == buffered, "buffered rows are still buffered"
    assert {k: v for k, v in after[0].items() if k != TABLE} == before[0], (
        "log handles are the same"
    )
    assert {k: v for k, v in after[1].items() if k != TABLE} == before[1], (
        "subscriptions are the same"
    )
    stack.q("stp1", 'system"t 1000"')
    stack.flush()
    assert stack.rows("rdb1", "trade") == before[2] + 2, "the buffered rows were published"
    assert stack.rows("wdb1", "trade") == before[2] + 2


def test_applying_twice_changes_nothing_the_second_time(stack, bundle):
    att = att_for(stack, bundle)
    go(stack, att)
    first_state, first_manifest = facts_of(stack), Manifest.load("pb-uat", state(stack))
    counted = FaultIpc(stack.ipc(), [])
    again = go(stack, att, counted)
    assert {s.outcome for s in again.steps} == {"already"}
    assert [op for op in counted.ops if op != "probe"] == []
    assert facts_of(stack) == first_state
    second = Manifest.load("pb-uat", state(stack))
    assert second.token == first_manifest.token and len(second.operations) == len(
        first_manifest.operations
    )
    assert first_state["subscribers"] == 2 and first_state["tp_tables"].count(TABLE) == 1


# ------------------------------------------------------------------ failure injection


STAGES = [
    ("stp_register", 0, "before"),
    ("stp_register", 0, "after"),
    ("consumer_attach", 0, "before"),
    ("consumer_attach", 0, "after"),
    ("consumer_attach", 1, "before"),
    ("consumer_attach", 1, "after"),
]


@pytest.mark.parametrize(("op", "nth", "when"), STAGES)
def test_failure_at_each_stage_is_reported_and_a_retry_reaches_the_same_state(
    stack, bundle, tmp_path, op, nth, when
):
    att = att_for(stack, bundle)
    config.save(att, state(stack))
    with pytest.raises(applying.ApplyFailed):
        applying.apply(att, FaultIpc(stack.ipc(), [(op, nth, when)]), ROOT, state_dir=state(stack))
    m = Manifest.load("pb-uat", state(stack))
    assert m.state == "partial" and any(o.status == "pending" for o in m.operations)
    assert not any(p.state == "verified" for p in attach_state.publishers(state(stack)))
    applying.apply(att, stack.ipc(), ROOT, state_dir=state(stack))
    final = Manifest.load("pb-uat", state(stack))
    assert final.state == "applied" and [o.status for o in final.operations] == ["done"] * 4
    assert facts_of(stack)["subscribers"] == 2
    assert facts_of(stack)["tp_tables"].count(TABLE) == 1
    assert verify.verify(att, stack.ipc(), state(stack)).ok

    stack.publish(TABLE, ROWS)
    stack.flush()
    assert stack.rows("rdb1", TABLE) == 3 and stack.rows("wdb1", TABLE) == 3


def test_a_failure_writing_the_schema_file_leaves_the_registration_and_says_so(
    stack, bundle, monkeypatch
):
    att = att_for(stack, bundle)
    original = stack.schema_file.read_text()
    monkeypatch.setattr(
        persist, "write", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(applying.ApplyFailed, match="disk full"):
        go(stack, att)
    assert stack.schema_file.read_text() == original
    assert stack.rows("stp1", TABLE) == 0, "registered live: the runtime half is in place"
    monkeypatch.undo()
    applying.apply(att, stack.ipc(), ROOT, state_dir=state(stack))
    assert "BEGIN uqs-attach pb-uat" in stack.schema_file.read_text()


# --------------------------------------------------------------------- refusals


def test_a_stack_with_a_blocker_is_refused_and_not_changed(tmp_path, bundle):
    s = IsolatedStack(tmp_path / "singular", settings="\\d .stplg\nmultilog:`singular;\n\\d .")
    try:
        s.start("discovery1", "stp1", "rdb1", "wdb1")
        att = att_for(s, bundle)
        with pytest.raises(applying.ApplyRefused, match="LOGGING_MODE"):
            go(s, att)
        assert TABLE not in s.q("stp1", "string each tables[]")
        assert not Manifest.exists("pb-uat", state(s))
    finally:
        s.teardown()


def test_the_start_gate_follows_the_attachments_real_state(stack, bundle, monkeypatch):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state(stack)))
    att = att_for(stack, bundle)
    go(stack, att)
    from uqs.paths import default_paths

    paths = default_paths()
    with pytest.raises(Exception, match="applied: it may start once"):
        attach_state.refuse_unverified_start(paths, att.publisher.procname, stack.port)
    verify.verify(att, stack.ipc(), state(stack))
    attach_state.refuse_unverified_start(paths, att.publisher.procname, stack.port)
    with pytest.raises(Exception, match=f"discovery at {paths.runtime_declaration.base_port + 1}"):
        attach_state.refuse_unverified_start(paths, att.publisher.procname, None)


def test_verify_does_not_publish(stack, bundle):
    att = att_for(stack, bundle)
    go(stack, att)
    for _ in range(2):
        assert verify.verify(att, stack.ipc(), state(stack)).ok
    tp = stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": [TABLE]})
    assert tp["msgcount"][TABLE] == 0 and tp["rowcount"][TABLE] == 0
    assert tp["logfiles"][TABLE][0]["size"] == 8, "the log still holds only its header"


def test_the_schema_block_is_what_apply_wrote_and_nothing_else_in_the_file_moved(stack, bundle):
    original = stack.schema_file.read_text()
    att = att_for(stack, bundle)
    go(stack, att)
    text = stack.schema_file.read_text()
    m = Manifest.load("pb-uat", state(stack))
    assert persist.outside(text, "pb-uat") == original
    assert f"/ BEGIN uqs-attach pb-uat {m.token}" in text


# ------------------------------------------------------ accepting an untested version


class _ReportsVersion:
    """The real stack, whose processes say they run another TorQ - what the site's
    TorQ 4.2.0 looks like to the preflight, without a 4.2.0 to hand."""

    def __init__(self, inner, version: str) -> None:
        self.inner, self.version = inner, version

    def call(self, endpoint, op, request):
        answer = self.inner.call(endpoint, op, request)
        if op == "probe":
            answer["torq"] = self.version
        return answer


def test_an_untested_version_is_refused_accepted_applied_and_the_acceptance_goes_stale(
    stack, bundle
):
    from uqs.attach import overrides, plan, preflight

    att = att_for(stack, bundle)
    site = _ReportsVersion(stack.ipc(), "4.2.0")
    with pytest.raises(applying.ApplyRefused, match="TORQ_VERSION"):
        applying.apply(att, site, ROOT, state_dir=state(stack))
    assert TABLE not in stack.q("stp1", ".j.j string tables[]")

    specs = [f"TORQ_VERSION:{p}" for p in ("discovery1", "stp1", "rdb1", "wdb1")]
    accepted = service.accept(att, site, ROOT, specs, "Validated against the site's TorQ 4.2.0")
    config.save(accepted, state(stack))
    with pytest.raises(applying.RisksNotConfirmed):
        applying.apply(accepted, site, ROOT, state_dir=state(stack))
    result = applying.apply(accepted, site, ROOT, state_dir=state(stack), confirmed_risks="tester")
    assert result.state == "applied" and stack.rows("rdb1", TABLE) == 0
    assert verify.verify(accepted, site, state(stack)).ok
    assert "4.2.0" not in preflight.SUPPORTED_TORQ, "accepting is not testing"

    # the same acceptance against what the stack says NOW: another version voids it
    later = _ReportsVersion(stack.ipc(), "4.2.1")
    p = plan.build(accepted, later, ROOT)
    assert not p.ok and any("stale" in x for x in p.override_problems)
    assert not verify.verify(accepted, later, state(stack)).ok
    assert overrides.POLICY["TORQ_VERSION"].overridable
    stack.publish(TABLE, ROWS)
    stack.flush()
    assert stack.rows("rdb1", TABLE) == 3


# ------------------------------------------------- the one command, end to end (#1457)


def test_the_cli_previews_executes_reruns_and_detaches_a_real_stack(stack, bundle):
    """`uqs job attachment add NAME --dry-run`, then the same without it, then again,
    then `uqs job attachment remove` - as subprocesses, the way an operator runs them."""
    cmd = ["job", "attachment", "add", "pb-uat", "--job", "pb_general_ledger",
           "--bundle", str(bundle), "--stack-id", str(stack.port), "--host", "127.0.0.1",
           "--discovery", f"127.0.0.1:{stack.port + 1}"]  # fmt: skip
    schema_before = stack.schema_file.read_bytes()

    preview = stack.uqs(*cmd, "--dry-run", "--json")
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert json.loads(preview.stdout)["plan"]["ok"] is True
    assert not state(stack).exists(), "a preview saves nothing"
    assert TABLE not in stack.q("stp1", "string each tables[]")
    assert stack.schema_file.read_bytes() == schema_before

    done = stack.uqs(*cmd, "--yes", "--no-start", "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    data = json.loads(done.stdout)
    assert [s["stage"] for s in data["stages"]] == ["save", "apply", "verify", "start"]
    assert data["verify"]["ok"] and Manifest.load("pb-uat", state(stack)).state == "verified"
    after = facts_of(stack)
    assert after["subscribers"] == 2 and stack.schema_file.read_bytes() != schema_before

    again = stack.uqs(*cmd, "--yes", "--no-start", "--json")
    assert again.returncode == 0, again.stdout + again.stderr
    assert {s["outcome"] for s in json.loads(again.stdout)["apply"]["steps"]} == {"already"}
    assert facts_of(stack) == after, "the re-run changed nothing"

    manifest = (state(stack) / "pb-uat" / "manifest.json").read_bytes()
    dry = stack.uqs("job", "attachment", "remove", "pb-uat", "--dry-run", "--json")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert json.loads(dry.stdout)["outcome"] == "rolled_back"
    assert (state(stack) / "pb-uat" / "manifest.json").read_bytes() == manifest
    assert facts_of(stack) == after

    gone = stack.uqs("job", "attachment", "remove", "pb-uat", "--yes", "--json")
    assert gone.returncode == 0, gone.stdout + gone.stderr
    assert json.loads(gone.stdout)["outcome"] == "rolled_back"
    assert TABLE not in stack.q("stp1", "string each tables[]")
    assert stack.schema_file.read_bytes() == schema_before, "the block went with it"
