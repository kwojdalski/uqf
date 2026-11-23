"""Detach against a scripted stack that keeps state (#1424).

The contract is the issue's reversibility section, one test per sentence: only
what was created is removed; before publication newly created empty state goes;
after publication nothing is deleted; drift and unknown ownership refuse; a
partial failure is reported as partial and a retry finishes it.
"""

from __future__ import annotations

import copy
import dataclasses
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from attach_support import FakeStack, FaultIpc, make_bundle, registered_at_runtime

from uqs.attach import apply as applying
from uqs.attach import (
    backfill_owner,
    backfill_run,
    block_text,
    config,
    facts,
    live,
    persist,
    plan,
    retain,
    schema_files,
    service,
    verify,
)
from uqs.attach import detach as detaching
from uqs.attach.config import AttachmentError, Storage
from uqs.attach.manifest import Manifest
from uqs.attach.preflight import Host
from uqs.paths import UqsError, default_paths, repo_root
from uqs.stack import attach_state

ROOT = repo_root()
TABLE = "piggybank_general_ledger"
LOCAL = Host(lambda h, p: False, ("localhost",))


class Publisher:
    """The attached job's process, as detach sees it."""

    def __init__(self, log: list[str] | None = None, running: bool = False, stuck: bool = False):
        self.on, self.stuck, self.log = running, stuck, log if log is not None else []

    def running(self, att) -> bool:
        return self.on

    def start(self, att) -> None:
        self.on = True

    def stop(self, att) -> None:
        self.log.append("publisher stopped")
        self.on = self.stuck and self.on


class Spy(FakeStack):
    """A FakeStack that notes every changing call in the publisher's log."""

    def __init__(self, *a, log: list[str], **kw):
        super().__init__(*a, **kw)
        self.log = log

    def call(self, endpoint, op, request):
        if op != "probe" and not request.get("dry_run"):
            self.log.append(f"{op}@{endpoint.procname}")
        return super().call(endpoint, op, request)


def consumer_ownership(name, state_dir) -> str:
    op = Manifest.load(name, state_dir).find("consumer_attach", "rdb1", TABLE)
    assert op is not None
    return op.ownership


@pytest.fixture
def world(tmp_path: Path):
    bundle = make_bundle(tmp_path / "piggybank")
    for name in ("tplogs", "wdb", "hdb"):
        (tmp_path / name).mkdir()
    schema = tmp_path / "database.q"
    schema.write_text("trade:([] time:`timestamp$())\n")
    log: list[str] = []
    fake = Spy(6000, schema_file=str(schema), log=log)
    storage = Storage(
        str(tmp_path / "tplogs"), str(schema), str(tmp_path / "wdb"), str(tmp_path / "hdb")
    )
    fake.named("stp1")["tplogdir"] = str(tmp_path / "tplogs")
    fake.named("wdb1")["savedir"] = f":{tmp_path / 'wdb'}"
    fake.named("wdb1")["hdbdir"] = f":{tmp_path / 'hdb'}"
    state = tmp_path / "attachments"

    def make(name: str = "pb-uat", **kw):
        kw.setdefault("runtime_only", False)
        kw.setdefault("storage", storage)
        att = service.create(name, "pb_general_ledger", bundle, 6000, ROOT, **kw)
        config.save(att, state)
        return att

    return fake, make, state, schema, log


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)


def apply_(att, fake, state):
    return applying.apply(att, fake, ROOT, state_dir=state)


def detach_(att, fake, state, publisher=None, **kw):
    return detaching.detach(att, fake, publisher or Publisher(), host=LOCAL, state_dir=state, **kw)


def stack_state(fake: FakeStack) -> dict:
    """The parts of the stack a detach must put back or leave: every process's
    data, minus the call log."""
    return copy.deepcopy({port: proc for port, proc in fake.procs.items()})


# ------------------------------------------------- before publication: rollback


def test_rollback_before_publication_puts_the_stack_back_as_it_was(world):
    fake, make, state, schema, log = world
    before, text = stack_state(fake), schema.read_text()
    att = make()
    apply_(att, fake, state)
    assert stack_state(fake) != before and "uqs-attach" in schema.read_text()
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back" and not result.blockers
    assert stack_state(fake) == before, "every process holds what it held before"
    assert schema.read_text() == text, "and the schema file is byte for byte what it was"
    m = Manifest.load("pb-uat", state)
    assert m.state == "rolled_back"
    assert [o.status for o in m.operations] == ["reverted"] * 4


def test_rollback_undoes_in_the_reverse_of_the_order_it_was_applied_in(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    log.clear()
    detach_(att, fake, state, Publisher(log, running=True))
    assert log[0] == "publisher stopped", "the publisher is stopped before anything is touched"
    changed = [e for e in log if e.startswith(("consumer_detach", "stp_unregister"))]
    assert changed[-1] == "stp_unregister@stp1" and "consumer_detach@wdb1" in changed[:2]


def test_only_what_was_created_is_removed_a_pre_existing_table_is_left(world):
    """The table was already on the tickerplant and both consumers - someone
    else's. Attaching adopts it; detaching must leave every bit of it."""
    fake, make, state, schema, log = world
    from attach_support import LEDGER_COLUMNS

    fake.named("stp1")["schemas"] = {TABLE: LEDGER_COLUMNS}
    fake.named("stp1")["published"] = [TABLE]
    for name in ("rdb1", "wdb1"):
        fake.named(name)["schemas"] = {TABLE: LEDGER_COLUMNS}
        fake.named(name)["subscribed"].append(TABLE)
    before = stack_state(fake)
    att = make()
    apply_(att, fake, state)
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back"
    assert stack_state(fake) == before, "every pre-existing piece is exactly as it was"
    assert not [e for e in log if e.startswith(("stp_unregister", "consumer_detach"))]
    m = Manifest.load("pb-uat", state)
    assert {o.ownership for o in m.operations} == {"existing"}


def test_a_consumer_table_held_before_the_attachment_is_not_ours_and_survives_rollback(world):
    """#1437: the consumer already held the table (and subscribes to it from another
    tickerplant); our tickerplant did not. Apply adopts it; a rollback must leave it,
    its rows and every subscription exactly as they were."""
    fake, make, state, schema, log = world
    from attach_support import LEDGER_COLUMNS

    rdb = fake.named("rdb1")
    rdb["schemas"] = {TABLE: [list(c) for c in LEDGER_COLUMNS]}
    rdb["subscribed"].append(TABLE)
    rdb["subtables"] = [*rdb.get("subtables", []), TABLE]
    rdb["foreign_subs"] = {TABLE: ["stp2"]}
    before = stack_state(fake)
    att = make()
    apply_(att, fake, state)
    assert consumer_ownership("pb-uat", state) == "existing"
    log.clear()
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back", result.blockers
    assert "consumer_detach@rdb1" not in log, "a table that was not ours is never asked to go"
    after = stack_state(fake)
    for port in after:
        if fake.procs[port]["procname"] == "rdb1":
            assert after[port] == before[port], "table, rows, subscriptions, subtables"


def held_unsubscribed(fake: FakeStack) -> dict:
    """rdb1 already holds the table - empty, the attachment's shape - but has no
    subscription for it and it is not in .rdb.subtables."""
    from attach_support import LEDGER_COLUMNS

    rdb = fake.named("rdb1")
    rdb["schemas"] = {TABLE: [list(c) for c in LEDGER_COLUMNS]}
    return rdb


def test_a_subscription_added_to_a_held_table_is_ours_and_rollback_completes(world):
    """#1459: apply subscribed rdb1's pre-existing table to our tickerplant. The table
    stays `existing`, the subscription is recorded as this attachment's, and a
    pre-publication rollback takes back exactly it - so the tickerplant's unregister
    counts its handle and completes, instead of refusing on a subscriber it cannot
    account for and leaving the rollback partial."""
    fake, make, state, schema, log = world
    rdb = held_unsubscribed(fake)
    before = stack_state(fake)
    att = make()
    result = apply_(att, fake, state)
    assert ("consumer_attach", "rdb1", "subscribed") in [
        (s.op, s.process, s.outcome) for s in result.steps
    ]
    m = Manifest.load("pb-uat", state)
    assert consumer_ownership("pb-uat", state) == "existing"
    sub = m.find("consumer_subscribe", "rdb1", TABLE)
    assert sub is not None and (sub.ownership, sub.status) == ("created", "done")
    assert sub.post["subtables"] is True
    log.clear()
    detached = detach_(att, fake, state)
    assert detached.outcome == "rolled_back", detached.blockers
    assert "consumer_unsubscribe@rdb1" in log and "consumer_detach@rdb1" not in log
    assert log.index("consumer_unsubscribe@rdb1") < log.index("stp_unregister@stp1")
    assert TABLE in rdb["schemas"], "the pre-existing table is never removed"
    after = stack_state(fake)
    for port in after:
        if fake.procs[port]["procname"] == "rdb1":
            assert after[port] == before[port], "table, subscriptions and subtables as before"
    assert TABLE not in fake.named("stp1")["schemas"]
    assert Manifest.load("pb-uat", state).state == "rolled_back"


def test_a_subscription_added_to_a_held_table_stays_after_publication(world):
    """#1459: after publication nothing is deleted - the subscription included."""
    fake, make, state, schema, log = world
    rdb = held_unsubscribed(fake)
    att = make()
    apply_(att, fake, state)
    fake.publish(TABLE)
    result = detach_(att, fake, state)
    assert result.outcome == "detached"
    assert TABLE in rdb["subscribed"]
    sub = Manifest.load("pb-uat", state).find("consumer_subscribe", "rdb1", TABLE)
    assert sub is not None and sub.status == "retained"


def test_a_subscription_added_through_a_pre_existing_tickerplant_table_refuses_rollback(world):
    """#1527: the tickerplant table already existed (not ours), and rdb1 held the table
    unsubscribed. Apply subscribed it through that table, so the subscription is this
    attachment's and is recorded; detach cannot take it back (the tickerplant side is not
    ours), so it refuses and says so - never `rolled_back` with rdb1 still subscribed."""
    fake, make, state, schema, log = world
    from attach_support import LEDGER_COLUMNS

    fake.named("stp1")["schemas"] = {TABLE: LEDGER_COLUMNS}
    fake.named("stp1")["published"] = [TABLE]
    fake.named("wdb1")["schemas"] = {TABLE: LEDGER_COLUMNS}
    fake.named("wdb1")["subscribed"].append(TABLE)
    rdb = held_unsubscribed(fake)
    att = make()
    result = apply_(att, fake, state)
    assert ("consumer_attach", "rdb1", "subscribed") in [
        (s.op, s.process, s.outcome) for s in result.steps
    ]
    sub = Manifest.load("pb-uat", state).find("consumer_subscribe", "rdb1", TABLE)
    assert sub is not None and (sub.ownership, sub.status) == ("created", "done")
    detached = detach_(att, fake, state)
    assert TABLE in rdb["subscribed"], "nothing takes the subscription back"
    assert detached.outcome == "refused"
    assert any(b.component == "rdb1" and "does not own" in b.reason for b in detached.blockers), (
        detached.blockers
    )
    assert Manifest.load("pb-uat", state).state != "rolled_back"


def test_a_consumer_table_first_found_absent_stays_ours_when_a_rerun_finds_it_again(world):
    """The upgrade existing -> created is for a table the consumer did NOT hold before the
    first attempt (a restart rebuilt it from our tickerplant table)."""
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    assert consumer_ownership("pb-uat", state) == "created"
    # the consumer restarts: it comes back holding the table, with no record of its own
    rdb = fake.named("rdb1")
    rdb["owned"].pop(TABLE)
    rdb["subscribed"].remove(TABLE)
    fake.named("stp1")["subscribers"][TABLE] = 0
    apply_(att, fake, state)
    assert consumer_ownership("pb-uat", state) == "created"
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back", result.blockers
    assert TABLE not in rdb["schemas"]


def test_dry_run_changes_nothing_not_even_the_publisher(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    snapshot, text = stack_state(fake), schema.read_text()
    pub = Publisher(log, running=True)
    log.clear()
    result = detach_(att, fake, state, pub, dry_run=True)
    assert result.outcome == "rolled_back" and result.dry_run
    assert pub.on and log == []
    assert stack_state(fake) == snapshot and schema.read_text() == text
    assert Manifest.load("pb-uat", state).state == "applied"


def test_a_publisher_that_will_not_stop_blocks_detach_before_the_stack_is_touched(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    log.clear()
    with pytest.raises(AttachmentError, match="still listening after stop"):
        detach_(att, fake, state, Publisher(log, running=True, stuck=True))
    assert log == ["publisher stopped"], "nothing else was done"


def test_detaching_what_was_never_applied_is_refused(world):
    fake, make, state, schema, log = world
    att = make()
    with pytest.raises(AttachmentError, match="has not been applied"):
        detach_(att, fake, state)


def test_detaching_twice_says_it_is_already_done(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    detach_(att, fake, state)
    again = detach_(att, fake, state)
    assert again.outcome == "already" and "rolled_back" in again.retained[0]


# --------------------------------------------------------- after publication


def test_after_publication_nothing_is_deleted_and_the_publisher_is_stopped(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    verify.verify(att, fake, state)
    fake.publish(TABLE, 3)
    text, snapshot = schema.read_text(), stack_state(fake)
    log.clear()
    result = detach_(att, fake, state, Publisher(log, running=True))
    assert result.outcome == "detached"
    assert log == ["publisher stopped"], "stopped, and no call to any process changed anything"
    assert stack_state(fake) == snapshot, "rows, logs and registrations are all still there"
    assert schema.read_text() == text, "and so is the block a restart needs"
    assert any("counted messages" in r for r in result.evidence.reasons)
    assert any("holds 3 row(s)" in r for r in result.evidence.reasons)
    assert len(result.retained) == 4
    m = Manifest.load("pb-uat", state)
    assert m.state == "detached" and [o.status for o in m.operations] == ["retained"] * 4


def test_evidence_that_cannot_be_read_counts_as_publication(world):
    """Absence is unproven when the storage is somewhere this cannot look."""
    fake, make, state, schema, log = world
    att = make(storage=Storage())
    apply_(att, fake, state)
    result = detach_(att, fake, state)
    assert result.outcome == "detached" and result.evidence.unknown
    assert fake.named("stp1")["schemas"][TABLE], "so nothing was removed"


def test_a_log_left_by_an_earlier_day_is_publication_too(world, tmp_path):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    day = tmp_path / "tplogs" / "stp1_2026.10.15"
    day.mkdir()
    (day / f"stp1_{TABLE}20261015120000").write_bytes(b"x" * 200)
    result = detach_(att, fake, state)
    assert result.outcome == "detached"
    assert any("an earlier log" in r for r in result.evidence.reasons)


def test_a_partition_in_the_hdb_or_the_wdb_is_publication_too(world, tmp_path):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    (tmp_path / "hdb" / "2026.10.15" / TABLE).mkdir(parents=True)
    result = detach_(att, fake, state)
    assert result.outcome == "detached" and any(
        "HDB holds a partition" in r for r in result.evidence.reasons
    )


def test_after_detaching_a_published_attachment_applying_again_adopts_what_stayed(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    fake.publish(TABLE)
    detach_(att, fake, state)
    before = Manifest.load("pb-uat", state).token
    result = apply_(att, fake, state)
    assert result.state == "applied" and {s.outcome for s in result.steps} == {"already"}
    assert Manifest.load("pb-uat", state).token == before, "the same token: the same ownership"
    assert verify.verify(att, fake, state).ok


def restart_without_the_table(fake: FakeStack) -> None:
    """What a tickerplant restart does to a table no block defines, rdb and wdb
    restarted after it: gone from all three."""
    for name in ("stp1", "rdb1", "wdb1"):
        fake.named(name)["schemas"].pop(TABLE, None)
    fake.named("stp1")["owned"].pop(TABLE, None)


def test_a_runtime_only_detach_after_publication_says_a_restart_drops_the_table(world):
    """#2766: nothing is deleted, as for any published attachment - but what is
    kept lasts only until the tickerplant restarts, and detach says so instead of
    promising replay and restart."""
    fake, make, state, schema, log = world
    att = make(runtime_only=True)
    apply_(att, fake, state)
    fake.publish(TABLE, 3)
    snapshot = stack_state(fake)
    result = detach_(att, fake, state)
    assert result.outcome == "detached" and stack_state(fake) == snapshot, "nothing deleted"
    [loss] = [" ".join(line.split()) for line in result.restart_loss]
    assert loss.startswith(f"{TABLE}: kept only until stp1 restarts - no block in the schema")
    assert "rows not yet written by end of day exist only in the rdb/wdb and in" in loss
    assert "let end of day run before stp1 restarts" in loss
    assert "stp1_" in loss, "the log file is named"
    assert all(r.endswith("(until the tickerplant restarts)") for r in result.retained)
    m = Manifest.load("pb-uat", state)
    notes = {o.note for o in m.operations}
    assert m.state == "detached" and notes == {retain.KEPT_UNTIL_RESTART}
    assert "kept for replay and restart" not in retain.KEPT_UNTIL_RESTART


def test_a_durable_detach_after_publication_still_promises_replay_and_restart(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    fake.publish(TABLE, 3)
    result = detach_(att, fake, state)
    assert result.outcome == "detached" and result.restart_loss == []
    assert not any("until" in r for r in result.retained)
    assert {o.note for o in Manifest.load("pb-uat", state).operations} == {retain.KEPT}


def test_a_detached_attachment_whose_table_a_restart_dropped_is_not_told_to_apply_again(world):
    """#2766: the registration-lost advice is for an applied attachment. Detached,
    a runtime-only table a restart dropped is what detach said would happen."""
    fake, make, state, schema, log = world
    att = make(runtime_only=True)
    apply_(att, fake, state)
    fake.publish(TABLE, 3)
    detach_(att, fake, state)
    restart_without_the_table(fake)
    health = service.make_plan(att, fake, ROOT).health(Manifest.load("pb-uat", state))
    assert health, "the loss is still shown"
    assert not any("apply it again" in line or "registration-lost" in line for line in health)
    assert all(line.startswith("detached: ") for line in health)
    assert f"stp1 no longer holds {TABLE} - no block defines it" in health[0]
    assert "pb-uat is detached, so there is nothing to apply" in health[0]


def test_after_a_rollback_applying_again_is_a_new_attachment_with_a_new_token(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    first = Manifest.load("pb-uat", state).token
    detach_(att, fake, state)
    apply_(att, fake, state)
    second = Manifest.load("pb-uat", state)
    assert second.token != first and second.state == "applied"
    assert [h["event"] for h in second.history].count("rolled_back") == 1, "history is kept"


# ------------------------------------------------- drift and ownership: refusals


def refused(world, tamper) -> detaching.DetachResult:
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    tamper(fake, schema, att, state)
    snapshot, text = stack_state(fake), schema.read_text()
    log.clear()
    pub = Publisher(log, running=True)
    result = detach_(att, fake, state, pub)
    assert result.outcome == "refused"
    assert [e for e in log if e != "publisher stopped"] == [], "no call changed anything"
    assert stack_state(fake) == snapshot and schema.read_text() == text
    assert Manifest.load("pb-uat", state).state == "applied", "and the manifest says so"
    return result


def reasons(result) -> str:
    return " | ".join(f"{b.component}: {b.reason} -> {b.action}" for b in result.blockers)


def test_a_subscriber_that_is_not_ours_refuses_the_reversal(world):
    result = refused(world, lambda fake, *a: fake.named("stp1")["subscribers"].update({TABLE: 3}))
    assert "subscribers other than the attached consumers" in reasons(result)


def test_a_filtered_subscription_refuses_the_reversal(world):
    result = refused(world, lambda fake, *a: fake.named("stp1")["filtered"].update({TABLE: 1}))
    assert "a filtered subscription exists" in reasons(result)


def test_an_ownership_record_that_is_not_ours_refuses_the_reversal(world):
    def swap(fake, *a):
        fake.named("stp1")["owned"][TABLE]["token"] = "somebody-else"

    assert "carries no ownership record of this attachment" in reasons(refused(world, swap))


def test_another_attachments_record_on_a_consumer_refuses_the_reversal(world):
    def swap(fake, *a):
        fake.named("rdb1")["owned"][TABLE]["token"] = "somebody-else"

    assert "rdb1: " + TABLE + " carries no ownership record" in reasons(refused(world, swap))


def test_a_consumer_that_restarted_and_lost_its_record_still_follows_the_plant(world):
    """The consumer rebuilt the table from our tickerplant table; its in-memory
    record is gone, the plant's is ours, so the copy is ours to remove."""
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    for name in ("rdb1", "wdb1"):
        fake.named(name)["owned"].pop(TABLE)
    assert verify.verify(att, fake, state).ok
    assert detach_(att, fake, state).outcome == "rolled_back"
    assert TABLE not in fake.named("rdb1")["schemas"]


def test_a_table_someone_changed_refuses_the_reversal(world):
    def widen(fake, *a):
        fake.named("wdb1")["schemas"][TABLE] = fake.named("wdb1")["schemas"][TABLE] + [
            ["extra", "j"]
        ]

    assert "no longer has the shape this attachment created" in reasons(refused(world, widen))


def test_another_attachment_still_using_the_table_refuses_the_reversal(world):
    fake, make, state, schema, log = world
    a, b = make("pb-a"), make("pb-b")
    apply_(a, fake, state)
    apply_(b, fake, state)
    before = stack_state(fake)
    result = detach_(a, fake, state)
    assert result.outcome == "refused" and "used by attachment(s) pb-b" in reasons(result)
    assert stack_state(fake) == before
    # B detaches first, leaving the table to A, which can then go
    assert detach_(b, fake, state).outcome == "rolled_back"
    assert fake.named("stp1")["owned"][TABLE].get("users") == []
    assert detach_(a, fake, state).outcome == "rolled_back"
    assert TABLE not in fake.named("stp1")["schemas"]


def restart_from_schema_file(fake, schema: Path, scratch: Path, q_binary) -> None:
    """What a stack restart leaves: the plant's ownership records are what q gets
    from loading the schema file, and the consumers, restarted after it, rebuild
    the table from the plant with no record of their own."""
    from attach_support import q_load

    got = q_load(q_binary, schema, scratch)
    assert got["loaded"], got["error"]
    fake.named("stp1")["owned"] = got["owned"]
    for c in ("rdb1", "wdb1"):
        fake.named(c)["owned"].pop(TABLE, None)
    if TABLE not in got["tables"]:  # no block defines it: the restarted plant lacks it
        tp = fake.named("stp1")
        for key in ("schemas", "logged", "updtab", "logfiles", "replay", "subscribers"):
            tp[key].pop(TABLE, None)
        for key in ("tables", "published", "names"):
            if TABLE in tp[key]:
                tp[key].remove(TABLE)


def test_a_user_of_the_table_still_holds_the_creator_back_after_a_restart(
    world, tmp_path, q_binary
):
    """#1446: the dependency is in the schema file, so it survives the restart."""
    fake, make, state, schema, log = world
    a, b = make("pb-a"), make("pb-b")
    apply_(a, fake, state)
    apply_(b, fake, state)
    assert persist.present(str(schema), "pb-b+uses")
    restart_from_schema_file(fake, schema, tmp_path, q_binary)
    assert fake.named("stp1")["owned"][TABLE]["users"] == ["pb-b"]
    before = stack_state(fake)
    result = detach_(a, fake, state)
    assert result.outcome == "refused" and "used by attachment(s) pb-b" in reasons(result)
    assert stack_state(fake) == before and TABLE in fake.named("stp1")["schemas"]
    # B leaves - its +uses block with it, so the next restart does not make it a user again
    assert detach_(b, fake, state).outcome == "rolled_back"
    assert not persist.present(str(schema), "pb-b+uses")
    restart_from_schema_file(fake, schema, tmp_path, q_binary)
    assert "users" not in fake.named("stp1")["owned"][TABLE]
    assert detach_(a, fake, state).outcome == "rolled_back"
    assert schema.read_text() == "trade:([] time:`timestamp$())\n"


@pytest.mark.parametrize("then", ["rejoins", "detaches"])
def test_a_transient_user_a_restart_forgot_is_refused_and_holds_the_creator_back(
    world, tmp_path, q_binary, monkeypatch, then
):
    """#2764: B joins A's durable table with --transient, so it writes no +uses
    block, and a restart rebuilds the ledger record from A's block without B. B's
    start is refused as verify would fail it, and A's rollback - which the ledger
    alone no longer stops - is refused naming B, until B rejoins or leaves."""
    fake, make, state, schema, log = world
    a, b = make("pb-a"), make("pb-b", runtime_only=True)
    apply_(a, fake, state)
    apply_(b, fake, state)
    assert not persist.present(str(schema), "pb-b+uses")
    assert verify.verify(a, fake, root=ROOT, state_dir=state, host=LOCAL).ok  # A's gate too
    assert verify.verify(b, fake, root=ROOT, state_dir=state, host=LOCAL).ok
    assert start_asks(b, state, monkeypatch, fake) == ["pb-b"]  # B is asked, A restart-safe

    restart_from_schema_file(fake, schema, tmp_path, q_binary)
    assert (
        TABLE in fake.named("stp1")["tables"] and "users" not in fake.named("stp1")["owned"][TABLE]
    )
    with pytest.raises(UqsError) as refused:
        live.check("pb-b", fake, LOCAL)
    said = " ".join(str(refused.value).split())
    assert f"is the publisher of attachment pb-b, and {TABLE} is still registered on stp1" in said
    assert (
        "no longer lists pb-b among the users: the tickerplant restarted and forgot this "
        "attachment's use" in said
    )
    assert "Re-apply with `uqs job attachment add pb-b` to join again. Nothing was started." in said

    before = stack_state(fake)
    for dry_run in (True, False):
        result = detach_(a, fake, state, dry_run=dry_run)
        assert result.outcome == "refused"
        (blocker,) = result.blockers
        assert blocker.component == "stp1"
        assert blocker.reason == (
            f"attachment pb-b (verified) uses {TABLE}, but the tickerplant no longer lists it "
            "among the table's users: a restart forgot its use"
        )
        assert blocker.action == (
            "detach pb-b first, or let it re-apply (`uqs job attachment add pb-b`), which joins "
            "the table again"
        )
        assert stack_state(fake) == before and TABLE in fake.named("stp1")["schemas"]

    if then == "rejoins":
        apply_(b, fake, state)
        assert fake.named("stp1")["owned"][TABLE]["users"] == ["pb-b"]
        live.check("pb-b", fake, LOCAL)
        # the ledger names B again, so the tickerplant's own check holds A back
        result = detach_(a, fake, state)
        assert result.outcome == "refused" and "used by attachment(s) pb-b" in reasons(result)
        assert "forgot" not in reasons(result)
    assert detach_(b, fake, state).outcome == "rolled_back"
    assert detach_(a, fake, state).outcome == "rolled_back"
    assert TABLE not in fake.named("stp1")["schemas"]


def test_a_user_on_another_stack_does_not_hold_the_creator_back(world):
    """#2764's guard looks only at attachments of the same stack id."""
    fake, make, state, schema, log = world
    a, b = make("pb-a"), make("pb-b", runtime_only=True)
    apply_(a, fake, state)
    apply_(b, fake, state)
    for proc in ("stp1", "rdb1", "wdb1"):  # forgotten everywhere, as after a restart
        fake.named(proc)["owned"][TABLE].pop("users")
    config.save(dataclasses.replace(b, stack=dataclasses.replace(b.stack, id=7000)), state)
    result = detach_(a, fake, state)
    assert result.outcome == "rolled_back", reasons(result)


def test_a_one_character_user_is_a_list_of_one_after_a_restart(world, tmp_path, q_binary):
    """#2604: `enlist "b"` is a char atom made a string, so the users read back as
    "b" rather than ["b"], and the creator's detach could not name them."""
    fake, make, state, schema, log = world
    a, b = make("pb-a"), make("b")
    apply_(a, fake, state)
    apply_(b, fake, state)
    restart_from_schema_file(fake, schema, tmp_path, q_binary)
    assert fake.named("stp1")["owned"][TABLE]["users"] == ["b"]
    result = detach_(a, fake, state)
    assert result.outcome == "refused" and "used by attachment(s) b" in reasons(result)


def codes(shown) -> dict[str, str]:
    return {f.code: f.message for f in shown.findings}


def schema_row(shown) -> dict:
    (row,) = [c for c in shown.changes() if c["process"] == "schema file"]
    return row


def start_asks(att, state, monkeypatch, fake) -> list[str]:
    """Run the start gate for `att`'s publisher; the stack is `fake`, and every
    time the live check asks it is noted. Raises the gate's refusal."""
    asked: list[str] = []

    def ipc_for(a):
        asked.append(a.name)
        return fake

    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    monkeypatch.setattr(live, "ipc_for", ipc_for)
    monkeypatch.setattr(live, "Host", lambda: LOCAL)
    attach_state.refuse_unverified_start(default_paths(), att.publisher.procname, 6000)
    return asked


def test_a_durable_user_of_a_transient_creators_table_is_runtime_only(
    world, tmp_path, q_binary, monkeypatch
):
    """#2759: B shares a table A attached with --transient. B's +uses block defines
    no table, so a restart drops it: the plan must not promise it survives, verify
    says so, and every start of B's publisher asks the stack - so once the table is
    gone, B's start is refused instead of publishing into a plant that drops its rows."""
    fake, make, state, schema, log = world
    a, b = make("pb-a", runtime_only=True), make("pb-b")
    apply_(a, fake, state)
    assert verify.verify(a, fake, root=ROOT, state_dir=state, host=LOCAL).ok  # A's gate too
    shown = plan.build(b, fake, ROOT, facts.Facts(host=LOCAL))
    found = codes(shown)
    assert "WILL_PERSIST" not in found
    said = found["SHARED_RUNTIME_ONLY"]
    assert f"{TABLE} is runtime-only: attachment pb-a created it with --transient" in said
    assert "attach pb-a without --transient, or detach pb-a and apply pb-b again" in said
    assert schema_row(shown)["table"] == "(none restored by a restart)"
    assert f"({TABLE} stays runtime-only)" in schema_row(shown)["action"]

    apply_(b, fake, state)
    assert persist.present(str(schema), "pb-b+uses")  # the dependency is still recorded
    checked = verify.verify(b, fake, root=ROOT, state_dir=state, host=LOCAL)
    assert checked.ok and checked.warnings == [said]
    # the publisher is A's too (one job), so the gate asks for both; B is asked now
    assert start_asks(b, state, monkeypatch, fake) == ["pb-a", "pb-b"]

    restart_from_schema_file(fake, schema, tmp_path, q_binary)
    assert TABLE not in fake.named("stp1")["tables"]
    with pytest.raises(UqsError) as refused:
        live.check("pb-b", fake, LOCAL)
    said = " ".join(str(refused.value).split())
    assert (
        f"is the publisher of attachment pb-b, and {TABLE} is no longer registered on stp1" in said
    )
    assert "Nothing was started" in said


@pytest.mark.parametrize("user", [True, False], ids=["user of a durable creator", "creator"])
def test_a_table_a_block_defines_needs_no_live_check(world, monkeypatch, user):
    """The cases #2759 leaves alone: a durable creator, and a durable user of its
    table, are restart-safe - the plan says WILL_PERSIST, verify warns of nothing,
    and a start on this machine asks no stack."""
    fake, make, state, schema, log = world
    a = make("pb-a")
    apply_(a, fake, state)
    assert verify.verify(a, fake, root=ROOT, state_dir=state, host=LOCAL).ok  # A's gate too
    att = make("pb-b") if user else a
    shown = plan.build(att, fake, ROOT, facts.Facts(host=LOCAL))
    assert "SHARED_RUNTIME_ONLY" not in codes(shown)
    assert schema_row(shown)["table"] == TABLE
    if user:
        assert codes(shown)["WILL_PERSIST"].endswith("a marked block will be added")
        apply_(att, fake, state)
    checked = verify.verify(att, fake, root=ROOT, state_dir=state, host=LOCAL)
    assert checked.ok and checked.warnings == []
    assert start_asks(att, state, monkeypatch, fake) == []


@pytest.mark.parametrize(
    ("text", "defines"),
    [
        ("t:([] a:`long$())\n", True),
        ("t :([] a:`long$())\n", True),
        ("tt:([] a:`long$())\nt2:([] a:`long$())\n", False),
        ("/ t:([] a:`long$())\n", False),
        ("/\nt:([] a:`long$())\n\\\n", False),  # a block comment
        ("\\\nt:([] a:`long$())\n", False),  # after the end of the script
        ("/ BEGIN uqs-attach a x\nt:([] a:`long$())\n/ END uqs-attach a\n", False),
    ],
)
def test_the_stacks_own_definition_is_read_as_q_loads_the_file(text, defines):
    """#2763: only a definition q would run, outside every attachment's block."""
    assert block_text.stack_defines(text, "t") is defines


def test_adopting_a_table_the_stacks_schema_file_defines_writes_no_block(
    world, tmp_path, q_binary, monkeypatch
):
    """#2763 (a): the stack's own schema file defines the table it adopts, outside
    any uqs-attach block - as database.q defines `trade`. A restart restores it
    with no block: the plan says so (no `a marked block will be added`), apply
    writes nothing, verify warns of nothing, and a start asks no stack."""
    from attach_support import LEDGER_Q

    fake, make, state, schema, log = world
    schema.write_text(f"trade:([] time:`timestamp$())\n{LEDGER_Q}\n")
    text = schema.read_text()
    registered_at_runtime(fake, TABLE)
    att = make()
    shown = plan.build(att, fake, ROOT, facts.Facts(host=LOCAL))
    found = codes(shown)
    assert found["STACK_DEFINES"] == (
        f"{schema} defines {TABLE} outside any uqs-attach block, so a restart restores it; "
        "no block is added for it"
    )
    assert not {"WILL_PERSIST", "ADOPTED_RUNTIME_ONLY"} & set(found)
    assert not [c for c in shown.changes() if c["process"] == "schema file"]

    apply_(att, fake, state)
    assert schema.read_text() == text, "no block for a table this attachment did not create"
    checked = verify.verify(att, fake, root=ROOT, state_dir=state, host=LOCAL)
    assert checked.ok and checked.warnings == []
    assert start_asks(att, state, monkeypatch, fake) == []

    restart_from_schema_file(fake, schema, tmp_path, q_binary)
    assert TABLE in fake.named("stp1")["tables"], "the plan's promise held"
    assert TABLE not in fake.named("stp1")["owned"], "and the table is still nobody's"
    live.check("pb-uat", fake, LOCAL)  # nothing to refuse


def test_adopting_a_runtime_only_table_is_reported_and_asked_at_every_start(
    world, tmp_path, q_binary, monkeypatch
):
    """#2763 (b): the table was only registered at runtime, so a restart drops it.
    A block would hold an ownership record that makes it this attachment's after
    a restart - and its detach would then remove it - so none is written; the plan
    warns instead of promising WILL_PERSIST, verify says the same, every start
    asks the stack, and once a restart has dropped the table the start is refused."""
    fake, make, state, schema, log = world
    text = schema.read_text()
    registered_at_runtime(fake, TABLE)
    att = make()
    shown = plan.build(att, fake, ROOT, facts.Facts(host=LOCAL))
    found = codes(shown)
    assert "WILL_PERSIST" not in found and "STACK_DEFINES" not in found
    said = found["ADOPTED_RUNTIME_ONLY"]
    assert said.startswith(f"{TABLE} is runtime-only: it was registered on the tickerplant ")
    assert "pb-uat writes no block for a table it did not create" in said
    assert f"Every start of {att.publisher.procname} asks the stack" in said
    assert said.endswith(f"outside any uqs-attach block, before applying (an edit there "
                         f"afterwards makes a block's removal refuse), then plan again: "
                         f"{att.tables[0].definition()}")  # fmt: skip
    assert not [c for c in shown.changes() if c["process"] == "schema file"]

    apply_(att, fake, state)
    assert schema.read_text() == text
    checked = verify.verify(att, fake, root=ROOT, state_dir=state, host=LOCAL)
    assert checked.ok and checked.warnings == [said]
    assert start_asks(att, state, monkeypatch, fake) == ["pb-uat"]

    restart_from_schema_file(fake, schema, tmp_path, q_binary)
    assert TABLE not in fake.named("stp1")["tables"], "as the plan said"
    with pytest.raises(UqsError) as refused:
        live.check("pb-uat", fake, LOCAL)
    said = " ".join(str(refused.value).split())
    assert f"{TABLE} is no longer registered on stp1" in said and "Nothing was started" in said


def test_a_transient_attachment_is_still_asked_at_every_start(world, monkeypatch):
    fake, make, state, schema, log = world
    a = make("pb-a", runtime_only=True)
    apply_(a, fake, state)
    checked = verify.verify(a, fake, root=ROOT, state_dir=state, host=LOCAL)
    assert checked.ok and checked.warnings[0].startswith("runtime-only: restarting")
    assert start_asks(a, state, monkeypatch, fake) == ["pb-a"]


def test_a_user_the_plant_forgot_fails_verify_and_applying_again_joins_again(world):
    """A restart without the +uses block (runtime-only, or written before it
    existed) drops the user: verify closes the gate, and apply puts it back."""
    fake, make, state, schema, log = world
    a, b = make("pb-a"), make("pb-b")
    apply_(a, fake, state)
    apply_(b, fake, state)
    assert verify.verify(b, fake, root=ROOT, state_dir=state).ok
    fake.named("stp1")["owned"][TABLE].pop("users")
    checks = verify.verify(b, fake, root=ROOT, state_dir=state)
    assert not checks.ok and Manifest.load("pb-b", state).state == "applied"
    assert "stp1: piggybank_general_ledger lists pb-b among its users" in [
        c.name for c in checks.checks if not c.ok
    ]
    again = apply_(b, fake, state)
    assert [s.outcome for s in again.steps if s.op == "stp_register"] == ["shared"]
    assert fake.named("stp1")["owned"][TABLE]["users"] == ["pb-b"]
    assert verify.verify(b, fake, root=ROOT, state_dir=state).ok


def test_a_schema_file_edited_inside_or_outside_the_block_refuses_the_reversal(world):
    def inside(fake, schema, *a):
        schema.write_text(schema.read_text().replace("persisted", "hand edited"))

    def outside(fake, schema, *a):
        schema.write_text("extra:([] time:`timestamp$())\n" + schema.read_text())

    assert "was edited" in reasons(refused(world, inside))


def test_a_schema_file_changed_outside_the_block_refuses_the_reversal(world):
    def outside(fake, schema, *a):
        schema.write_text("extra:([] time:`timestamp$())\n" + schema.read_text())

    result = refused(world, outside)
    assert "changed outside the block" in reasons(result)
    assert "edit the block out by hand" in reasons(result)


def test_a_consumer_that_cannot_be_reached_refuses_the_reversal_naming_it(world):
    result = refused(world, lambda fake, *a: fake.down.add(6005))
    assert "wdb1: not reachable" in reasons(result)


def test_a_table_a_consumer_created_through_a_table_we_do_not_own_refuses(world):
    fake, make, state, schema, log = world
    from attach_support import LEDGER_COLUMNS

    fake.named("stp1")["schemas"] = {TABLE: LEDGER_COLUMNS}
    fake.named("stp1")["published"] = [TABLE]
    att = make()
    apply_(att, fake, state)
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    assert "through a tickerplant table this attachment does not own" in reasons(result)


def test_a_refusal_lists_what_a_person_has_to_do_and_the_attachment_can_be_detached_after(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    fake.named("stp1")["subscribers"][TABLE] = 3
    first = detach_(att, fake, state)
    assert first.outcome == "refused" and all(b.action for b in first.blockers)
    fake.named("stp1")["subscribers"][TABLE] = 2
    assert detach_(att, fake, state).outcome == "rolled_back"


# ------------------------------------------------------------- partial failure


#: The first calls of each removal operation are its dry runs - the checks that precede
#: any change - so the calls that remove are numbered after them.
@pytest.mark.parametrize(
    ("op", "nth", "when"),
    [
        ("consumer_detach", 2, "before"),
        ("consumer_detach", 2, "after"),
        ("consumer_detach", 3, "before"),
        ("consumer_detach", 3, "after"),
        ("stp_unregister", 1, "before"),
        ("stp_unregister", 1, "after"),
    ],
)
def test_a_lost_connection_during_rollback_is_partial_and_a_retry_finishes_it(world, op, nth, when):
    fake, make, state, schema, log = world
    before = stack_state(fake)
    att = make()
    apply_(att, fake, state)
    with pytest.raises(AttachmentError) as caught:
        detaching.detach(
            att,
            FaultIpc(fake, [(op, nth, when)]),
            Publisher(),
            host=LOCAL,
            state_dir=state,
        )
    assert "Cross-process atomicity is not claimed" in str(caught.value)
    assert "detach" in str(caught.value)
    m = Manifest.load("pb-uat", state)
    assert m.state == "rollback_partial"
    assert any(o.status == "pending" for o in m.operations) or any(
        o.status == "reverted" for o in m.operations
    )
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back"
    assert stack_state(fake) == before
    assert [o.status for o in Manifest.load("pb-uat", state).operations] == ["reverted"] * 4


def test_a_call_that_fails_while_still_checking_changes_nothing(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    snapshot, text = stack_state(fake), schema.read_text()
    with pytest.raises(AttachmentError, match="injected"):
        detaching.detach(
            att,
            FaultIpc(fake, [("consumer_detach", 0, "after")]),
            Publisher(),
            host=LOCAL,
            state_dir=state,
        )
    assert stack_state(fake) == snapshot and schema.read_text() == text
    assert Manifest.load("pb-uat", state).state == "applied", "no rollback had begun"


def test_a_failure_removing_the_block_is_partial_and_names_what_is_still_in_place(
    world, monkeypatch
):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)

    def die(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(persist, "remove", die)
    with pytest.raises(AttachmentError, match="read-only file system") as caught:
        detach_(att, fake, state)
    assert "schema_block schema-file" in str(caught.value).split("still in place")[1]
    assert Manifest.load("pb-uat", state).state == "rollback_partial"
    assert TABLE in fake.named("stp1")["schemas"], "nothing else was undone before the block"
    monkeypatch.undo()
    assert detach_(att, fake, state).outcome == "rolled_back"


# ------------------------------------- a failed detach keeps the start gate closed (#1443)


def verified(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    assert verify.verify(att, fake, state).ok
    assert Manifest.load("pb-uat", state).state == "verified"
    return att


def gate_open(att, state, monkeypatch) -> bool:
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    try:
        attach_state.refuse_unverified_start(default_paths(), att.publisher.procname, 6000)
    except UqsError:
        return False
    return True


@pytest.mark.parametrize(
    ("op", "nth", "when"),
    [
        ("consumer_detach", 0, "before"),  # a dry-run removal check
        ("stp_unregister", 0, "after"),
    ],
)
def test_an_ipc_error_after_the_publisher_stopped_leaves_the_start_gate_closed(
    world, monkeypatch, op, nth, when
):
    fake, make, state, schema, log = world
    att = verified(world)
    with pytest.raises(AttachmentError, match="start gate stays closed") as caught:
        detaching.detach(
            att,
            FaultIpc(fake, [(op, nth, when)]),
            Publisher(log, running=True),
            host=LOCAL,
            state_dir=state,
        )
    assert "uqs job attachment remove pb-uat" in str(caught.value), (
        "the message says how to recover"
    )
    assert "publisher stopped" in log
    assert Manifest.load("pb-uat", state).state in ("detaching", "rollback_partial")
    assert not gate_open(att, state, monkeypatch)
    assert detach_(att, fake, state).outcome == "rolled_back", "a retry continues"


def test_a_stack_that_cannot_be_read_after_the_publisher_stopped_leaves_the_gate_closed(
    world, monkeypatch
):
    fake, make, state, schema, log = world
    att = verified(world)
    fake.down.add(6000)
    with pytest.raises(AttachmentError, match="cannot be read") as caught:
        detach_(att, fake, state, Publisher(log, running=True))
    assert "uqs job attachment remove pb-uat" in str(caught.value)
    assert Manifest.load("pb-uat", state).state == "detaching"
    assert not gate_open(att, state, monkeypatch)
    fake.down.clear()
    assert detach_(att, fake, state).outcome == "rolled_back"


def test_an_ipc_error_in_ledger_leave_after_publication_leaves_the_gate_closed(world, monkeypatch):
    fake, make, state, schema, log = world
    a, att = make("pb-a"), make("pb-b")
    apply_(a, fake, state)
    apply_(att, fake, state)
    assert verify.verify(att, fake, root=ROOT, state_dir=state).ok
    fake.publish(TABLE, 3)
    with pytest.raises(AttachmentError, match="start gate stays closed"):
        detaching.detach(
            att,
            FaultIpc(fake, [("ledger_leave", 0, "before")]),
            Publisher(log, running=True),
            host=LOCAL,
            state_dir=state,
        )
    assert Manifest.load("pb-b", state).state == "detaching"
    assert not gate_open(att, state, monkeypatch)
    assert detach_(att, fake, state).outcome == "detached"


# ------------------------- a step with an unknown outcome is probed, not abandoned (#1447)


@pytest.mark.parametrize("op", ["stp_register", "consumer_attach"])
def test_detach_removes_what_an_unknown_outcome_apply_step_made(world, op):
    fake, make, state, schema, log = world
    before = stack_state(fake)
    att = make()
    with pytest.raises(applying.ApplyFailed):
        applying.apply(att, FaultIpc(fake, [(op, 0, "after")]), ROOT, state_dir=state)
    pending = [o for o in Manifest.load("pb-uat", state).operations if o.status == "pending"]
    assert [(o.op, o.ownership) for o in pending] == [(op, "")], "outcome unknown, no ownership"
    assert stack_state(fake) != before
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back", [b.reason for b in result.blockers]
    assert stack_state(fake) == before, "the table the unknown step made is gone"
    assert "pre-existing" not in " ".join(o.note for o in Manifest.load("pb-uat", state).operations)


def test_an_unknown_outcome_step_that_never_took_effect_is_reverted_as_such(world):
    fake, make, state, schema, log = world
    att = make()
    with pytest.raises(applying.ApplyFailed):
        applying.apply(att, FaultIpc(fake, [("stp_register", 0, "before")]), ROOT, state_dir=state)
    assert detach_(att, fake, state).outcome == "rolled_back"
    note = Manifest.load("pb-uat", state).operations[0].note
    assert "never took effect" in note


def test_an_unknown_outcome_step_on_an_unreachable_process_refuses_with_the_manual_action(world):
    fake, make, state, schema, log = world
    att = make()
    with pytest.raises(applying.ApplyFailed):
        applying.apply(
            att, FaultIpc(fake, [("consumer_attach", 0, "after")]), ROOT, state_dir=state
        )
    port = next(p for p, d in fake.procs.items() if d.get("procname") == "rdb1")
    fake.down.add(port)
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    assert any(
        "unknown outcome" in b.reason or "not reachable" in b.reason for b in result.blockers
    )
    assert Manifest.load("pb-uat", state).state != "rolled_back"
    assert TABLE in fake.named("stp1")["schemas"], "nothing was abandoned or removed"


def _stopped_after_the_block_was_written(att, fake, state, monkeypatch) -> None:
    """Apply, stopped between the schema file's write and its journal entry."""
    replace = schema_files.LocalFile._replace

    def stop(self, before: str, text: str) -> None:
        replace(self, before, text)
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(schema_files.LocalFile, "_replace", stop)
        with pytest.raises(KeyboardInterrupt):
            apply_(att, fake, state)


def test_detach_reads_the_schema_file_for_a_block_of_unknown_outcome(world, monkeypatch):
    """#2905: the block step is settled from the file, here one on this machine."""
    fake, make, state, schema, log = world
    before, text = stack_state(fake), schema.read_text()
    att = make()
    _stopped_after_the_block_was_written(att, fake, state, monkeypatch)
    m = Manifest.load("pb-uat", state)
    pending = m.find("schema_block", "schema-file", TABLE)
    assert pending is not None and (pending.status, pending.ownership) == ("pending", "")
    assert block_text.begin_marker("pb-uat", m.token) in schema.read_text()
    assert detach_(att, fake, state, dry_run=True).outcome == "rolled_back"
    assert block_text.begin_marker("pb-uat", m.token) in schema.read_text(), "a dry run"
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back", [b.reason for b in result.blockers]
    assert ("schema_block", "removed") in [(s.op, s.outcome) for s in result.steps]
    assert stack_state(fake) == before and schema.read_text() == text


def test_a_block_of_unknown_outcome_in_a_file_that_is_gone_refuses(world, monkeypatch):
    fake, make, state, schema, log = world
    att = make()
    _stopped_after_the_block_was_written(att, fake, state, monkeypatch)
    schema.unlink()
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    assert [b.reason for b in result.blockers] == [
        f"the schema file block for {TABLE} has an unknown outcome and could not be "
        f"settled: {schema.resolve()} does not exist"
    ]
    assert TABLE in fake.named("stp1")["schemas"], "nothing was removed"


def test_a_stopped_block_step_removed_by_hand_lets_remove_finish(world, monkeypatch):
    """#2908: the block taken out by hand before `remove` is read as never made,
    so the refusal that once came back word for word after doing so is gone."""
    fake, make, state, schema, log = world
    before, text = stack_state(fake), schema.read_text()
    att = make()
    _stopped_after_the_block_was_written(att, fake, state, monkeypatch)
    schema.write_text(text)
    assert detach_(att, fake, state, dry_run=True).outcome == "rolled_back"
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back", [b.reason for b in result.blockers]
    assert "schema_block" not in [s.op for s in result.steps if s.outcome == "removed"]
    assert stack_state(fake) == before and schema.read_text() == text
    assert detach_(att, fake, state).outcome == "already"


def test_applying_again_finishes_a_stopped_block_step_and_remove_takes_it(world, monkeypatch):
    """#2908: `attachment add` run again after a stop inside the block step."""
    fake, make, state, schema, log = world
    text = schema.read_text()
    att = make()
    _stopped_after_the_block_was_written(att, fake, state, monkeypatch)
    apply_(att, fake, state)
    m = Manifest.load("pb-uat", state)
    done = m.find("schema_block", "schema-file", TABLE)
    assert m.state == "applied" and done is not None
    assert (done.status, done.ownership) == ("done", "created")
    assert schema.read_text().count(block_text.begin_marker("pb-uat", m.token)) == 1
    assert detach_(att, fake, state).outcome == "rolled_back"
    assert schema.read_text() == text


def test_apply_recognises_its_own_leftover_by_token_after_an_unknown_outcome(world):
    fake, make, state, schema, log = world
    att = make()
    with pytest.raises(applying.ApplyFailed):
        applying.apply(att, FaultIpc(fake, [("stp_register", 0, "after")]), ROOT, state_dir=state)
    res = apply_(att, fake, state)
    first = next(s for s in res.steps if s.op == "stp_register")
    assert first.outcome == "already", "its own table, not another attachment's `shared`"
    assert Manifest.load("pb-uat", state).operations[0].ownership == "created"


def test_apply_refuses_an_attachment_that_is_detaching_and_changes_nothing(world):
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    m = Manifest.load("pb-uat", state)
    m.set_state("detaching", "set by the test")
    before = stack_state(fake)
    with pytest.raises(AttachmentError, match="is detaching.*uqs job attachment remove pb-uat"):
        apply_(att, fake, state)
    assert stack_state(fake) == before
    assert Manifest.load("pb-uat", state).state == "detaching"


# --------------------------------------------- a backfill and a detach in a race (#1526)


def _just_before_the_lock(monkeypatch, interleave):
    """`config.locked`, with `interleave` run first: the other command's whole critical
    section, landing between this one's earlier checks and its taking the lock."""
    real = config.locked

    @contextmanager
    def locked(name, root=None):
        interleave()
        with real(name, root):
            yield

    monkeypatch.setattr(config, "locked", locked)


def test_detach_refuses_a_backfill_that_claimed_the_attachment_before_it_took_the_lock(
    world, monkeypatch
):
    """#1526: a launch that claims the attachment after detach's ownership check and
    before its lock - detach must still see the run, and change nothing."""
    fake, make, state, schema, log = world
    att = make()
    apply_(att, fake, state)
    before, was = stack_state(fake), Manifest.load("pb-uat", state).state
    _just_before_the_lock(
        monkeypatch,
        lambda: backfill_owner.claim("pb-uat", "some_worker", os.getpid(), "abc", state),
    )
    log.clear()
    with pytest.raises(AttachmentError, match="has an attached backfill running: some_worker"):
        detach_(att, fake, state, Publisher(log, running=True))
    assert log == [], "not even the publisher was stopped"
    assert stack_state(fake) == before
    assert Manifest.load("pb-uat", state).state == was


def test_a_backfill_launch_refuses_an_attachment_detached_after_its_admission(world, monkeypatch):
    """#1526: admission saw `verified`; a detach finished before launch took the lock.
    The launch must read the manifest again under the lock and start nothing."""
    fake, make, state, schema, log = world
    att = make()
    m = Manifest.new(att, Manifest.path_for("pb-uat", state))
    m.set_state("verified", "set by the test")
    monkeypatch.setattr(backfill_run, "q_interpreter", lambda env: Path("/bin/false"))
    where = {"hdb_dir": str(state / "hdb"), "state_dir": str(state / "run")}
    admitted = {
        "destination": {"attachment": "pb-uat", "base_port": 6000, "host": "localhost",
                        "dataset": TABLE, "identity": "abc"} | where,
        "reload_targets": [],
    }  # fmt: skip
    monkeypatch.setattr(backfill_run, "plan_attached", lambda *a, **kw: admitted)
    monkeypatch.setattr(backfill_run, "runner_flags", lambda *a, **kw: [])
    started: list = []

    def popen(*a, **kw):
        started.append(a)
        return type("P", (), {"pid": os.getpid()})()

    monkeypatch.setattr(backfill_run.subprocess, "Popen", popen)

    def detached() -> None:
        found = Manifest.load("pb-uat", state)
        found.set_state("detached", "a detach that finished in between")

    _just_before_the_lock(monkeypatch, detached)
    with pytest.raises(UqsError, match="is detached, not verified"):
        backfill_run.launch(
            default_paths(), "pb-uat", "some_worker", "v1",
            datetime(2015, 1, 7, tzinfo=UTC), datetime(2015, 1, 8, tzinfo=UTC),
            ipc=fake, mode="run", attach_root=state,
        )  # fmt: skip
    assert started == [], "no runner was started"
    assert backfill_owner.active("pb-uat", state) == []
