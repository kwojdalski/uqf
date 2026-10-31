"""Detach, rollback, drift refusal and restart behaviour on a REAL TorQ 5.2.12 stack (#1424).

Opt-in (`uv run pytest -m live_stack`). Each
test has its own stack: the starter pack as it ships, its own ports, data and
copy of the pack - so a test may restart its tickerplant, edit its schema file
and publish, and nobody else's stack is involved.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from attach_stack_support import CORE, IsolatedStack, requires_stack
from attach_support import LEDGER_Q, FaultIpc, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, persist, service, verify
from uqs.attach import detach as detaching
from uqs.attach.config import AttachmentError, Storage
from uqs.attach.manifest import Manifest
from uqs.paths import UqsError, default_paths, repo_root
from uqs.stack import attach_state

pytestmark = requires_stack()
ROOT = repo_root()
TABLE = "piggybank_general_ledger"
ROWS = [["AAA", "BBB", "AAA"], ["acc1", "acc2", "acc1"], [1.5, 2.5, 3.5], ["n1", "n2", "n3"]]


class Publisher:
    """The attached job's process: a double that remembers it was stopped."""

    def __init__(self, running: bool = True) -> None:
        self.on, self.stopped = running, 0

    def running(self, att) -> bool:
        return self.on

    def start(self, att) -> None:
        self.on = True

    def stop(self, att) -> None:
        self.on, self.stopped = False, self.stopped + 1


def consumer_ownership(name, state_dir) -> str:
    op = Manifest.load(name, state_dir).find("consumer_attach", "rdb1", TABLE)
    assert op is not None
    return op.ownership


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


def state(stack) -> Path:
    return stack.root / "attachments"


def att_for(stack, bundle, name="pb-uat", **kw):
    kw.setdefault("runtime_only", False)
    kw.setdefault("discovery", f"127.0.0.1:{stack.port + 1}")
    kw.setdefault(
        "storage",
        Storage(str(stack.tplogs), str(stack.schema_file), str(stack.wdb_dir), str(stack.hdb_dir)),
    )
    att = service.create(
        name, "pb_general_ledger", bundle, stack.port, ROOT, host="127.0.0.1", **kw
    )
    config.save(att, state(stack))
    return att


def apply_(stack, att, ipc=None):
    return applying.apply(att, ipc or stack.ipc(), ROOT, state_dir=state(stack))


def detach_(stack, att, publisher=None, ipc=None, **kw):
    return detaching.detach(
        att, ipc or stack.ipc(), publisher or Publisher(), state_dir=state(stack), **kw
    )


def snapshot(stack) -> dict:
    """What a rollback has to put back, read from the processes and the disk."""
    tp = stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": [TABLE]})
    out = {
        "stp": sorted(tp["tables"]),
        "published": sorted(tp["published"]),
        "owned": sorted(tp["owned"]),
        "subscribers": json.loads(stack.q("stp1", ".j.j count each .stpps.subrequestall")),
        "currlog": json.loads(stack.q("stp1", ".j.j exec tbl!string logname from currlog")),
        "handles": json.loads(stack.q("stp1", ".j.j exec tbl!handle from currlog")),
        "metatable": int(stack.q("stp1", "count .stpm.metatable")),
        "schema_file": stack.schema_file.read_text(),
        "tplog_files": sorted(
            str(p.relative_to(stack.tplogs)) for p in stack.tplogs.rglob("*") if p.is_file()
        ),
    }
    for name in ("rdb1", "wdb1"):
        out[name] = json.loads(
            stack.q(
                name,
                ".j.j `tables`subs`subtables!(string asc tables[];"
                "string asc exec table from .sub.SUBSCRIPTIONS;"
                " string asc $[`subtables in key `.rdb;.rdb.subtables;`$()])",
            )
        )
    for key in ("subscribers", "currlog", "handles"):
        out[key] = {k: v for k, v in out[key].items() if k != TABLE}
    out["tplog_files"] = [f for f in out["tplog_files"] if TABLE not in f]
    return out


def wait_for(check, what: str, seconds: float = 150.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception:  # noqa: BLE001, S110 - the process is mid-restart
            pass
        time.sleep(1.0)
    pytest.fail(f"gave up waiting for {what}")


def restart(stack, name: str) -> None:
    stack.uqs("stop", name, "--base-port", str(stack.port))
    time.sleep(1.0)
    run = stack.uqs("start", name, "--base-port", str(stack.port))
    assert run.returncode == 0, run.stdout + run.stderr


def restart_consumers(stack) -> None:
    """A tickerplant restart drops its consumers' connections and TorQ does not
    reconnect them; the operator restarts them, and they subscribe to whatever the
    tickerplant now has - which is the point of the restart tests below."""
    for name in ("rdb1", "wdb1"):
        restart(stack, name)


def held_by_consumers(stack) -> bool:
    return all(
        TABLE in stack.ipc().call(stack.endpoint(n), "probe", {"tables": []})["subscribed"]
        for n in ("rdb1", "wdb1")
    )


# ------------------------------------------------------ rollback before publication


def test_rollback_before_publication_restores_the_stack_and_existing_tables_still_work(
    stack, bundle
):
    original = snapshot(stack)
    att = att_for(stack, bundle)
    apply_(stack, att)
    verify.verify(att, stack.ipc(), state(stack))
    assert snapshot(stack) != original
    publisher = Publisher()
    result = detach_(stack, att, publisher)
    assert result.outcome == "rolled_back", result.blockers
    assert publisher.stopped == 1
    assert snapshot(stack) == original, "every process, the log directory and the schema file"
    assert TABLE not in stack.q("stp1", ".j.j string tables[]")
    assert not list(stack.tplogs.rglob(f"*{TABLE}*")), "the empty log files went with it"
    m = Manifest.load("pb-uat", state(stack))
    assert m.state == "rolled_back" and {o.status for o in m.operations} == {"reverted"}
    # what was there before still publishes, and reaches its consumers
    before = stack.rows("rdb1", "trade")
    stack.q("stp1", '.u.upd[`trade;(`IBM`MSFT;10 20f;100 200i;00b;"aa";"NN";`x`y)]')
    stack.flush()
    assert stack.rows("rdb1", "trade") == before + 2 and stack.rows("wdb1", "trade") == before + 2


def test_the_stack_can_be_attached_again_after_a_rollback(stack, bundle):
    att = att_for(stack, bundle)
    apply_(stack, att)
    detach_(stack, att)
    apply_(stack, att)
    assert verify.verify(att, stack.ipc(), state(stack)).ok
    stack.publish(TABLE, ROWS)
    stack.flush()
    assert stack.rows("rdb1", TABLE) == 3


def test_a_dry_run_on_the_real_stack_changes_nothing_and_says_what_it_would_do(stack, bundle):
    att = att_for(stack, bundle)
    apply_(stack, att)
    snap = snapshot(stack)
    publisher = Publisher()
    result = detach_(stack, att, publisher, dry_run=True)
    assert result.outcome == "rolled_back" and result.dry_run and publisher.stopped == 0
    assert snapshot(stack) == snap


# --------------------------------------------------------- after publication


def test_detach_after_publication_keeps_rows_logs_and_replay_and_stops_the_publisher(stack, bundle):
    att = att_for(stack, bundle)
    apply_(stack, att)
    verify.verify(att, stack.ipc(), state(stack))
    stack.publish(TABLE, ROWS)
    stack.flush()
    publisher = Publisher()
    result = detach_(stack, att, publisher)
    assert result.outcome == "detached" and publisher.stopped == 1
    assert any("counted messages" in r for r in result.evidence.reasons)
    m = Manifest.load("pb-uat", state(stack))
    assert m.state == "detached" and {o.status for o in m.operations} == {"retained"}
    # nothing was deleted
    assert stack.rows("rdb1", TABLE) == 3 and stack.rows("wdb1", TABLE) == 3
    logs = stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": [TABLE]})["logfiles"][TABLE]
    assert logs and logs[0]["size"] > 8 and Path(logs[0]["name"].lstrip(":")).is_file()
    assert "BEGIN uqs-attach pb-uat" in stack.schema_file.read_text()
    # replayable: a consumer that restarts replays the log and holds the rows again
    restart(stack, "rdb1")
    wait_for(lambda: stack.rows("rdb1", TABLE) == 3, "rdb1 to replay the table's log")
    # and the rows reach the HDB at end of day, publisher or no publisher
    stack.end_of_day()
    day = sorted(p.name for p in stack.hdb_dir.iterdir() if (p / TABLE).is_dir())[-1]
    assert int(stack.q("hdb1", f"count select from {TABLE} where date={day}")) == 3


def test_a_detached_attachment_is_gated_off_and_applies_again_to_what_stayed(
    stack, bundle, monkeypatch
):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state(stack)))
    att = att_for(stack, bundle)
    apply_(stack, att)
    stack.publish(TABLE, ROWS)
    stack.flush()
    detach_(stack, att)
    from uqs.paths import UqsError, default_paths
    from uqs.stack import attach_state

    with pytest.raises(UqsError, match="which is detached"):
        attach_state.refuse_unverified_start(default_paths(), att.publisher.procname, stack.port)
    result = apply_(stack, att)
    assert {s.outcome for s in result.steps} == {"already"}
    assert verify.verify(att, stack.ipc(), state(stack)).ok


# ----------------------------------------------- drift, ownership and shared state


def refuse_without_changing(stack, att, snap):
    publisher = Publisher()
    result = detach_(stack, att, publisher)
    assert result.outcome == "refused" and publisher.stopped == 1
    assert snapshot(stack) == snap, "a refused reversal changed nothing"
    assert Manifest.load(att.name, state(stack)).state == "applied"
    return " | ".join(f"{b.component}: {b.reason}" for b in result.blockers)


@pytest.mark.parametrize(("rows", "outcome"), [(0, "rolled_back"), (1, "detached")])
def test_a_consumer_table_held_before_the_attachment_survives_detach_untouched(
    stack, bundle, rows, outcome
):
    """#1437: rdb1 already holds the table (with a row) and a live subscription to a
    second tickerplant; ours does not. Apply adopts it; a pre-publication rollback must
    leave the table, its rows, both subscriptions and the subtables entry as they were."""
    stack.q("rdb1", LEDGER_Q)
    if rows:  # rows on a consumer are evidence of publication: detach keeps, not rolls back
        stack.q("rdb1", f"`{TABLE} insert (.z.p;`AAA;`acc1;1.5;`held)")
    # a LIVE handle: TorQ marks (and a later subscribe purges) rows whose handle is not open
    other = stack.endpoint("discovery1").port
    stack.q("rdb1", f"h2:hopen `::{other}:admin:admin")
    stack.q(
        "rdb1",
        f"`.sub.SUBSCRIPTIONS insert (`stp2;`segmentedtickerplant;h2;`{TABLE};`;.z.p;1b)",
    )
    stack.q("rdb1", f".rdb.subtables:distinct .rdb.subtables,`{TABLE}")
    exprs = (
        f"-3!`{TABLE} in tables[]",
        f"-3!select from {TABLE}",
        "-3!asc .rdb.subtables",
        "-3!exec (procname;table;w;active) from .sub.SUBSCRIPTIONS",
    )

    def read():
        return [stack.q("rdb1", e) for e in exprs]

    held = read()
    att = att_for(stack, bundle)
    apply_(stack, att)
    assert consumer_ownership("pb-uat", state(stack)) == "existing"
    result = detach_(stack, att)
    assert result.outcome == outcome, result.blockers
    assert read() == held, "table, rows, both subscriptions and subtables"
    if outcome == "rolled_back":
        assert TABLE not in stack.q("stp1", ".j.j string tables[]")
        assert TABLE not in stack.q("wdb1", ".j.j string tables[]")


def test_consumer_detach_answers_removed_when_the_consumer_never_kept_an_ownership_record(stack):
    """#1449: a restarted consumer has no `.uqfattach.owned`; dropping everything is still
    `removed`, and only the rows of the named tickerplant go."""
    stack.q("rdb1", LEDGER_Q)
    stack.q(
        "rdb1",
        f"`.sub.SUBSCRIPTIONS insert (`stp1;`segmentedtickerplant;99i;`{TABLE};`;.z.p;1b)",
    )
    stack.q("rdb1", f".rdb.subtables:distinct .rdb.subtables,`{TABLE}")
    assert stack.q("rdb1", "`owned in key `.uqfattach") in (False, 0)
    req = {"table": TABLE, "token": "t", "dry_run": False, "follows_plant": True}
    request = {**req, "tickerplant": "stp1"}
    answer = stack.ipc().call(stack.endpoint("rdb1"), "consumer_detach", request)
    assert answer == {"status": "removed", "problems": []}
    assert TABLE not in stack.q("rdb1", ".j.j string tables[]")
    assert TABLE not in stack.q("rdb1", ".j.j string .rdb.subtables")


def test_consumer_detach_refuses_a_table_also_subscribed_from_another_tickerplant(stack):
    stack.q("rdb1", LEDGER_Q)
    for plant in ("stp1", "stp2"):
        stack.q(
            "rdb1",
            f"`.sub.SUBSCRIPTIONS insert (`{plant};`segmentedtickerplant;99i;`{TABLE};`;.z.p;1b)",
        )
    req = {"table": TABLE, "token": "t", "dry_run": False, "follows_plant": True}
    request = {**req, "tickerplant": "stp1"}
    answer = stack.ipc().call(stack.endpoint("rdb1"), "consumer_detach", request)
    assert answer["status"] == "refused"
    assert answer["problems"] == ["also subscribed from another tickerplant: stp2"]
    assert TABLE in stack.q("rdb1", ".j.j string tables[]")
    assert int(stack.q("rdb1", f"count select from .sub.SUBSCRIPTIONS where table=`{TABLE}")) == 2


def test_consumer_detach_on_a_restarted_rdb_removes_everything_and_says_removed(stack, bundle):
    """#1449: the restarted rdb rebuilt the table from our tickerplant and has no
    `.uqfattach.owned`; detaching it is `removed`, not `partial`. (Through the whole
    detach the restart's empty HDB partition reads as publication - safe, documented -
    so the operation is driven directly with what detach would send.)"""
    att = att_for(stack, bundle)
    apply_(stack, att)
    restart(stack, "rdb1")
    wait_for(
        lambda: (
            TABLE in stack.ipc().call(stack.endpoint("rdb1"), "probe", {"tables": []})["subscribed"]
        ),
        "rdb1 to resubscribe",
    )
    assert stack.q("rdb1", "`owned in key `.uqfattach") in (False, 0)
    token = Manifest.load("pb-uat", state(stack)).token
    request = {
        "table": TABLE,
        "token": token,
        "dry_run": False,
        "follows_plant": True,
        "tickerplant": "stp1",
    }
    answer = stack.ipc().call(stack.endpoint("rdb1"), "consumer_detach", request)
    assert answer == {"status": "removed", "problems": []}
    assert TABLE not in stack.q("rdb1", ".j.j string tables[]")


def test_a_subscriber_that_is_not_ours_blocks_the_rollback_until_it_goes(stack, bundle):
    import kola

    att = att_for(stack, bundle)
    apply_(stack, att)
    foreign = kola.Q("127.0.0.1", stack.port, user="admin", passwd="admin", timeout=15000)
    try:
        foreign.sync(".u.sub", TABLE, "")
        text = refuse_without_changing(stack, att, snapshot(stack))
        assert "subscribers other than the attached consumers" in text
    finally:
        foreign.disconnect()
    wait_for(
        lambda: int(stack.q("stp1", f"count .stpps.subrequestall`{TABLE}")) == 2,
        "the handle to close",
    )
    assert detach_(stack, att).outcome == "rolled_back"


def test_an_ownership_record_taken_off_the_tickerplant_blocks_the_rollback(stack, bundle):
    att = att_for(stack, bundle)
    apply_(stack, att)
    stack.q("stp1", f".uqfattach.owned:(enlist `{TABLE})_.uqfattach.owned")
    text = refuse_without_changing(stack, att, snapshot(stack))
    assert f"stp1: {TABLE} carries no ownership record of this attachment" in text
    assert TABLE in stack.q("stp1", ".j.j string tables[]"), "and the table was left alone"


def test_a_table_someone_widened_blocks_the_rollback(stack, bundle):
    att = att_for(stack, bundle)
    apply_(stack, att)
    stack.q("rdb1", f"update extra:0N from `{TABLE}")
    text = refuse_without_changing(stack, att, snapshot(stack))
    assert "rdb1: " + TABLE + " no longer has the shape this attachment created" in text


def test_a_schema_file_edited_by_someone_else_blocks_the_rollback(stack, bundle):
    att = att_for(stack, bundle)
    apply_(stack, att)
    block_edit = stack.schema_file.read_text().replace("persisted", "hand edited")
    stack.schema_file.write_text(block_edit)
    text = refuse_without_changing(stack, att, snapshot(stack))
    assert "was edited" in text
    stack.schema_file.write_text(persist.outside(block_edit, "pb-uat"))  # a different edit
    assert "is gone from" in refuse_without_changing(stack, att, snapshot(stack))


def test_another_attachment_using_the_table_blocks_the_creator_until_it_detaches(stack, bundle):
    a = att_for(stack, bundle, "pb-a")
    b = att_for(stack, bundle, "pb-b")
    apply_(stack, a)
    shared = apply_(stack, b)
    assert {s.outcome for s in shared.steps if s.op != "schema_uses"} == {"shared"}
    assert [s.outcome for s in shared.steps if s.op == "schema_uses"] == ["created"]
    ledger = json.loads(stack.q("stp1", f".j.j .j.k .uqfattach.owned`{TABLE}"))
    assert ledger["attachment"] == "pb-a" and ledger["users"] == ["pb-b"]
    snap = snapshot(stack)
    publisher = Publisher()
    refused = detach_(stack, a, publisher)
    assert (
        refused.outcome == "refused" and "used by attachment(s) pb-b" in refused.blockers[0].reason
    )
    assert snapshot(stack) == snap
    assert detach_(stack, b).outcome == "rolled_back", (
        "the dependent leaves without removing anything"
    )
    # but its +uses block: a restart must not make it a user again
    assert snapshot(stack) == {
        **snap,
        "schema_file": persist.outside(snap["schema_file"], "pb-b+uses"),
    }
    assert detach_(stack, a, Publisher()).outcome == "rolled_back"
    assert TABLE not in stack.q("stp1", ".j.j string tables[]")


def test_another_attachment_using_the_table_still_blocks_the_creator_after_a_restart(stack, bundle):
    """#1446: the user's dependency is in the schema file, so a restarted
    tickerplant still knows it, and the creator's detach still refuses."""
    original = stack.schema_file.read_bytes()
    a = att_for(stack, bundle, "pb-a")
    b = att_for(stack, bundle, "pb-b")
    apply_(stack, a)
    apply_(stack, b)
    restart(stack, "stp1")
    wait_for(
        lambda: TABLE in stack.q("stp1", ".j.j string tables[]"), "stp1 to come back with the table"
    )
    ledger = json.loads(stack.q("stp1", f".j.j .j.k .uqfattach.owned`{TABLE}"))
    assert ledger["attachment"] == "pb-a" and ledger["users"] == ["pb-b"]
    restart_consumers(stack)
    wait_for(lambda: held_by_consumers(stack), "the restarted consumers to subscribe")
    # the stopping wdb wrote its (empty) tables down; tidy that up so this is still
    # before publication, where a detach would remove the table
    for root in (stack.wdb_dir, stack.hdb_dir):
        for leftover in root.glob(f"*/{TABLE}"):
            shutil.rmtree(leftover)
    assert verify.verify(b, stack.ipc(), state(stack)).ok
    refused = detach_(stack, a)
    assert refused.outcome == "refused", refused.evidence
    assert any("used by attachment(s) pb-b" in x.reason for x in refused.blockers)
    assert TABLE in stack.q("stp1", ".j.j string tables[]"), "the table pb-b needs stays"
    assert detach_(stack, b).outcome == "rolled_back"
    assert "pb-b+uses" not in stack.schema_file.read_text()
    ledger = json.loads(stack.q("stp1", f".j.j .j.k .uqfattach.owned`{TABLE}"))
    assert ledger["users"] == [], "pb-b left, so pb-a is free to go"
    assert detach_(stack, a, dry_run=True).blockers == []
    assert stack.schema_file.read_bytes() != original, "pb-a's own block is still there"


def test_a_consumer_that_is_down_blocks_the_rollback_naming_it(stack, bundle):
    att = att_for(stack, bundle)
    apply_(stack, att)
    stack.stop("wdb1")
    # a stopping wdb writes its tables down, empty ones included: tidy that up so the
    # only reason left to refuse is that wdb1 cannot be reached
    for root in (stack.wdb_dir, stack.hdb_dir):
        for leftover in root.glob(f"*/{TABLE}"):
            shutil.rmtree(leftover)
    publisher = Publisher()
    result = detach_(stack, att, publisher)
    assert result.outcome == "refused", result.evidence
    assert any(b.component == "wdb1" and "not reachable" in b.reason for b in result.blockers)
    assert TABLE in stack.q("stp1", ".j.j string tables[]")


# ------------------------------------------------------------- partial failure


@pytest.mark.parametrize(
    ("op", "nth", "when"),
    [
        ("consumer_detach", 2, "before"),
        ("consumer_detach", 2, "after"),
        ("consumer_detach", 3, "after"),
        ("stp_unregister", 1, "before"),
        ("stp_unregister", 1, "after"),
    ],
)
def test_a_lost_connection_during_rollback_is_partial_and_a_retry_restores_the_stack(
    stack, bundle, op, nth, when
):
    original = snapshot(stack)
    att = att_for(stack, bundle)
    apply_(stack, att)
    with pytest.raises(AttachmentError, match="Cross-process atomicity is not claimed"):
        detach_(stack, att, ipc=FaultIpc(stack.ipc(), [(op, nth, when)]))
    assert Manifest.load("pb-uat", state(stack)).state == "rollback_partial"
    assert detach_(stack, att).outcome == "rolled_back"
    assert snapshot(stack) == original


@pytest.mark.parametrize("op", ["stp_register", "consumer_attach"])
def test_an_apply_step_with_an_unknown_outcome_is_probed_and_removed_by_detach(stack, bundle, op):
    """#1447: the connection died after the process acted; detach asks the process
    instead of calling the table pre-existing and reporting a rollback that left it."""
    original = snapshot(stack)
    att = att_for(stack, bundle)
    with pytest.raises(applying.ApplyFailed):
        apply_(stack, att, ipc=FaultIpc(stack.ipc(), [(op, 0, "after")]))
    pending = [o for o in Manifest.load("pb-uat", state(stack)).operations if o.status == "pending"]
    assert [(o.op, o.ownership) for o in pending] == [(op, "")]
    assert snapshot(stack) != original
    assert detach_(stack, att).outcome == "rolled_back"
    assert snapshot(stack) == original, "the table the unknown step made is gone"


def test_a_detach_that_errors_after_stopping_the_publisher_keeps_the_start_gate_closed(
    stack, bundle, monkeypatch
):
    """#1443: the manifest must not stay `verified` once the publisher is stopped."""
    att = att_for(stack, bundle)
    apply_(stack, att)
    assert verify.verify(att, stack.ipc(), root=ROOT, state_dir=state(stack)).ok
    pub = Publisher()
    with pytest.raises(AttachmentError, match="start gate stays closed"):
        detach_(stack, att, pub, ipc=FaultIpc(stack.ipc(), [("consumer_detach", 0, "before")]))
    assert pub.stopped == 1
    assert Manifest.load("pb-uat", state(stack)).state == "detaching"
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state(stack)))
    with pytest.raises(UqsError, match="detaching"):
        attach_state.refuse_unverified_start(default_paths(), att.publisher.procname, stack.port)
    assert detach_(stack, att).outcome == "rolled_back"


# ------------------------------------------------------------ restart behaviour


def test_a_persisted_registration_survives_a_tickerplant_restart_and_publishing_resumes(
    stack, bundle
):
    att = att_for(stack, bundle)
    apply_(stack, att)
    token = Manifest.load("pb-uat", state(stack)).token
    restart(stack, "stp1")
    wait_for(
        lambda: TABLE in stack.q("stp1", ".j.j string tables[]"), "stp1 to come back with the table"
    )
    owned = json.loads(stack.q("stp1", f".j.j .j.k .uqfattach.owned`{TABLE}"))
    assert owned["token"] == token, "the block restored the ownership record, not just the table"
    restart_consumers(stack)
    wait_for(lambda: held_by_consumers(stack), "the restarted consumers to subscribe")
    assert verify.verify(att, stack.ipc(), state(stack)).ok
    stack.publish(TABLE, ROWS)
    stack.flush()
    assert stack.rows("rdb1", TABLE) == 3 and stack.rows("wdb1", TABLE) == 3


def test_a_runtime_only_registration_is_lost_on_restart_reported_and_restored_by_apply(
    stack, bundle
):
    att = att_for(stack, bundle, runtime_only=True)
    apply_(stack, att)
    assert "BEGIN uqs-attach" not in stack.schema_file.read_text()
    restart(stack, "stp1")
    wait_for(lambda: stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": []}), "stp1")
    assert TABLE not in stack.q("stp1", ".j.j string tables[]"), (
        "a restart drops a runtime-only table"
    )
    planned = service.make_plan(att, stack.ipc(), ROOT)
    health = planned.health(Manifest.load("pb-uat", state(stack)))
    assert any("registration-lost: stp1 no longer holds" in line for line in health)
    assert not verify.verify(att, stack.ipc(), state(stack)).ok
    apply_(stack, att)
    wait_for(lambda: held_by_consumers(stack), "the consumers to hold the table again")
    assert verify.verify(att, stack.ipc(), state(stack)).ok
    stack.publish(TABLE, ROWS)
    stack.flush()
    assert stack.rows("rdb1", TABLE) == 3


def test_after_a_rollback_a_restart_does_not_bring_the_table_back(stack, bundle):
    original = stack.schema_file.read_text()
    att = att_for(stack, bundle)
    apply_(stack, att)
    detach_(stack, att)
    assert stack.schema_file.read_text() == original
    restart(stack, "stp1")
    wait_for(lambda: stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": []}), "stp1")
    assert TABLE not in stack.q("stp1", ".j.j string tables[]")
    assert TABLE not in stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": []})["owned"]


def test_after_a_post_publication_detach_a_restart_keeps_the_table_and_its_log_replayable(
    stack, bundle
):
    att = att_for(stack, bundle)
    apply_(stack, att)
    stack.publish(TABLE, ROWS)
    stack.flush()
    detach_(stack, att)
    restart(stack, "stp1")
    wait_for(
        lambda: TABLE in stack.q("stp1", ".j.j string tables[]"), "stp1 to come back with the table"
    )
    logs = json.loads(stack.q("stp1", f".j.j .stplg.replaylog[enlist `{TABLE}]"))
    assert logs, "a subscriber that restarts is still pointed at the table's logs"
    restart_consumers(stack)
    wait_for(lambda: held_by_consumers(stack), "the restarted consumers to subscribe")
    assert stack.rows("rdb1", TABLE) == 3 and stack.rows("wdb1", TABLE) == 3, (
        "and they kept their rows"
    )
    stack.end_of_day()
    day = sorted(p.name for p in stack.hdb_dir.iterdir() if (p / TABLE).is_dir())[-1]
    assert int(stack.q("hdb1", f"count select from {TABLE} where date={day}")) == 3
