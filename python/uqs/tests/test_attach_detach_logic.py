"""Detach against a scripted stack that keeps state (#1424).

The contract is the issue's reversibility section, one test per sentence: only
what was created is removed; before publication newly created empty state goes;
after publication nothing is deleted; drift and unknown ownership refuse; a
partial failure is reported as partial and a retry finishes it.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from attach_support import FakeStack, FaultIpc, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, facts, persist, service, verify
from uqs.attach import detach as detaching
from uqs.attach.config import AttachmentError, Storage
from uqs.attach.manifest import Manifest
from uqs.attach.preflight import Host
from uqs.paths import repo_root

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
