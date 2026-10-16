"""Apply, verify and the start gate, against a scripted stack that keeps state (#1424).

`FakeStack` here is stateful: `stp_register` and `consumer_attach` change what
`probe` answers, so idempotence, resumption after a lost answer and verification
are tested for real - in CI, without a q. The same behaviours are put to a real
TorQ 5.2.12 stack in test_attach_apply.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attach_support import LEDGER_COLUMNS, FakeStack, FaultIpc, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, facts, persist, plan, service, verify
from uqs.attach.config import AttachmentError
from uqs.attach.manifest import Manifest
from uqs.paths import UqsError, default_paths, repo_root
from uqs.stack import attach_state

ROOT = repo_root()
TABLE = "piggybank_general_ledger"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "attachments"


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)


def attachment(bundle: Path, name: str = "pb-uat", **kw) -> config.Attachment:
    kw.setdefault("runtime_only", True)
    return service.create(name, "pb_general_ledger", bundle, 6000, ROOT, **kw)


def found(entry):
    assert entry is not None
    return entry


def go(att, ipc, state: Path) -> applying.ApplyResult:
    config.save(att, state)
    return applying.apply(att, ipc, ROOT, state_dir=state)


def mutating(fake: FakeStack) -> list[str]:
    return [op for _, op, _ in fake.calls if op != "probe"]


# ---------------------------------------------------------------- the happy path


def test_apply_registers_the_table_subscribes_every_consumer_and_journals_each_step(bundle, state):
    fake = FakeStack(6000)
    result = go(attachment(bundle), fake, state)
    assert result.state == "applied"
    assert [(s.op, s.process, s.outcome) for s in result.steps] == [
        ("stp_register", "stp1", "created"),
        ("consumer_attach", "rdb1", "created"),
        ("consumer_attach", "wdb1", "created"),
    ]
    assert fake.named("stp1")["subscribers"][TABLE] == 2
    m = Manifest.load("pb-uat", state)
    assert m.state == "applied"
    assert [(o.op, o.process, o.status, o.ownership) for o in m.operations] == [
        ("stp_register", "stp1", "done", "created"),
        ("consumer_attach", "rdb1", "done", "created"),
        ("consumer_attach", "wdb1", "done", "created"),
    ]
    assert [o.depends_on for o in m.operations] == [[], [1], [1]]
    assert m.tables[TABLE]["columns"][0] == ["time", "p"]
    assert fake.named("stp1")["owned"][TABLE]["token"] == m.token, "ownership is in the process too"


def test_every_step_is_journalled_pending_before_its_call_is_made(bundle, state):
    """Write-ahead: at the moment stp_register runs, the manifest on disk already
    says so - a crash there leaves `pending`, never a change nobody wrote down."""
    seen: list[str] = []

    class Peek(FakeStack):
        def call(self, endpoint, op, request):
            if op in ("stp_register", "consumer_attach"):
                disk = Manifest.load("pb-uat", state)
                seen.append(f"{op}:{found(disk.find(op, endpoint.procname, TABLE)).status}")
            return super().call(endpoint, op, request)

    go(attachment(bundle), Peek(6000), state)
    assert seen == [
        "stp_register:pending",
        "consumer_attach:pending",
        "consumer_attach:pending",
    ]


def test_applying_twice_changes_nothing_the_second_time(bundle, state):
    fake = FakeStack(6000)
    att = attachment(bundle)
    go(att, fake, state)
    first = Manifest.load("pb-uat", state)
    fake.calls.clear()
    again = go(att, fake, state)
    assert {s.outcome for s in again.steps} == {"already"}
    assert mutating(fake) == [], "a converged attachment makes no call that changes anything"
    second = Manifest.load("pb-uat", state)
    assert [o.id for o in second.operations] == [o.id for o in first.operations]
    assert second.token == first.token
    assert fake.named("stp1")["subscribers"][TABLE] == 2, "no second subscription"
    assert fake.named("stp1")["tables"].count(TABLE) == 1


def test_a_blocked_stack_is_refused_before_anything_is_written(bundle, state):
    fake = FakeStack(6000)
    fake.named("stp1")["multilog"] = "singular"
    with pytest.raises(applying.ApplyRefused, match="LOGGING_MODE"):
        go(attachment(bundle), fake, state)
    assert mutating(fake) == []
    assert not Manifest.exists("pb-uat", state)


def test_a_publisher_that_is_already_running_blocks_apply(bundle, state, monkeypatch):
    att = attachment(bundle)
    port = plan.publisher_port(att, ROOT).port
    monkeypatch.setattr(facts, "_tcp_open", lambda host, p: p == port)
    with pytest.raises(applying.ApplyRefused, match="PORT_CONFLICT|PUBLISHER_RUNNING"):
        go(att, FakeStack(6000), state)


# ------------------------------------------------------- failure at every stage


STAGES = [
    ("stp_register", 0, "before"),
    ("stp_register", 0, "after"),
    ("consumer_attach", 0, "before"),
    ("consumer_attach", 0, "after"),
    ("consumer_attach", 1, "before"),
    ("consumer_attach", 1, "after"),
]


HOLDER = {"stp_register": ["stp1"], "consumer_attach": ["rdb1", "wdb1"]}


@pytest.mark.parametrize(("op", "nth", "when"), STAGES)
def test_a_lost_connection_at_each_stage_is_reported_exactly_and_a_retry_converges(
    bundle, state, op, nth, when
):
    fake = FakeStack(6000)
    att = attachment(bundle)
    config.save(att, state)
    with pytest.raises(applying.ApplyFailed) as caught:
        applying.apply(att, FaultIpc(fake, [(op, nth, when)]), ROOT, state_dir=state)
    assert "no cross-process atomicity is claimed" in str(caught.value)
    m = Manifest.load("pb-uat", state)
    assert m.state == "partial"
    unknown = m.find(op, HOLDER[op][nth], TABLE)
    assert unknown is not None and unknown.status == "pending"
    assert "outcome unknown" in unknown.note
    # what the process really holds depends on whether the call got through
    if op == "stp_register":
        assert bool(fake.named("stp1")["schemas"].get(TABLE)) == (when == "after")
    # a retry resolves the unknown step by asking the process - it does not duplicate
    result = applying.apply(att, fake, ROOT, state_dir=state)
    assert result.state == "applied"
    final = Manifest.load("pb-uat", state)
    assert [o.status for o in final.operations] == ["done"] * 3
    assert [o.ownership for o in final.operations] == ["created"] * 3
    assert fake.named("stp1")["subscribers"][TABLE] == 2
    assert fake.named("stp1")["tables"].count(TABLE) == 1


def test_a_step_the_process_says_failed_is_recorded_failed_with_its_reason(
    bundle, state, monkeypatch
):
    fake = FakeStack(6000)
    real = fake._attach

    def refuse(p, req):
        if p["procname"] == "wdb1":
            return {"status": "failed", "detail": "subscription did not take"}
        return real(p, req)

    monkeypatch.setattr(fake, "_attach", refuse)
    with pytest.raises(applying.ApplyFailed, match="subscription did not take"):
        go(attachment(bundle), fake, state)
    m = Manifest.load("pb-uat", state)
    assert m.state == "partial"
    assert [(o.process, o.status) for o in m.operations] == [
        ("stp1", "done"),
        ("rdb1", "done"),
        ("wdb1", "failed"),
    ]
    assert found(m.find("consumer_attach", "wdb1", TABLE)).note == "subscription did not take"


def test_an_unfinished_manifest_does_not_open_the_publisher(bundle, state):
    att = attachment(bundle)
    config.save(att, state)
    cut = FaultIpc(FakeStack(6000), [("consumer_attach", 1, "before")])
    with pytest.raises(applying.ApplyFailed):
        applying.apply(att, cut, ROOT, state_dir=state)
    published = {p.procname: p.state for p in attach_state.publishers(state)}
    assert published == {"pb_ledger1": "partial"}


def test_applying_again_after_the_tickerplant_forgot_the_table_restores_it_as_ours(bundle, state):
    """A tickerplant restarted without a persisted schema loses the table and its
    ownership record; the consumers keep the table but lose the subscription. The
    manifest's token is what lets apply tell the consumers' tables - ours - from
    pre-existing ones, so they stay `created` and removable."""
    fake = FakeStack(6000)
    att = attachment(bundle)
    go(att, fake, state)
    stp = fake.named("stp1")
    for key in ("schemas", "owned", "subscribers", "logfiles", "replay", "logged", "updtab"):
        stp[key].pop(TABLE, None)
    stp["tables"].remove(TABLE)
    stp["published"].remove(TABLE)
    for name in ("rdb1", "wdb1"):
        fake.named(name)["subscribed"].remove(TABLE)
    result = go(att, fake, state)
    assert [s.outcome for s in result.steps] == ["created", "already", "already"]
    m = Manifest.load("pb-uat", state)
    assert [o.ownership for o in m.operations] == ["created"] * 3
    assert stp["owned"][TABLE]["token"] == m.token
    assert verify.verify(att, fake, state).ok


# ------------------------------------------------------- existing and shared state


def test_a_table_that_was_already_there_is_adopted_and_never_recorded_as_created(bundle, state):
    fake = FakeStack(6000)
    fake.named("stp1")["schemas"] = {TABLE: LEDGER_COLUMNS}
    fake.named("stp1")["published"] = [TABLE]
    result = go(attachment(bundle), fake, state)
    assert result.steps[0].outcome == "adopted"
    m = Manifest.load("pb-uat", state)
    assert found(m.find("stp_register", "stp1", TABLE)).ownership == "existing"
    assert m.created()[0].process in ("rdb1", "wdb1"), "only what it made is removable"
    assert TABLE not in fake.named("stp1")["owned"], "an adopted table gets no ownership record"


def test_a_second_attachment_to_the_same_table_depends_on_the_first_and_never_owns_it(
    bundle, state
):
    fake = FakeStack(6000)
    go(attachment(bundle, "pb-a"), fake, state)
    result = go(attachment(bundle, "pb-b"), fake, state)
    assert [s.outcome for s in result.steps] == ["shared", "shared", "shared"]
    b = Manifest.load("pb-b", state)
    assert {o.ownership for o in b.operations} == {"shared"}
    assert b.created() == []
    ledger = fake.named("stp1")["owned"][TABLE]
    assert ledger["attachment"] == "pb-a" and ledger["users"] == ["pb-b"]
    assert fake.named("stp1")["subscribers"][TABLE] == 2, "no second subscription for B"


# ------------------------------------------------------------------- persistence


def test_apply_adds_one_marked_block_to_the_schema_file_and_nothing_else(bundle, state, tmp_path):
    schema = tmp_path / "database.q"
    before = "trade:([] time:`timestamp$())"  # no final newline
    schema.write_text(before)
    fake = FakeStack(6000, schema_file=str(schema))
    result = go(attachment(bundle, runtime_only=False), fake, state)
    assert [s.op for s in result.steps][-1] == "schema_block"
    text = schema.read_text()
    m = Manifest.load("pb-uat", state)
    assert text.startswith(before + "\n/ BEGIN uqs-attach pb-uat " + m.token)
    assert f"{TABLE}:([] time:`timestamp$();" in text and text.rstrip().endswith(
        "/ END uqs-attach pb-uat"
    )
    assert persist.outside(text, "pb-uat") == before + "\n"
    block = m.find("schema_block", "schema-file", TABLE)
    assert found(block).status == "done" and found(block).post["path"] == str(schema)
    fake.calls.clear()
    go(attachment(bundle, runtime_only=False), fake, state)
    assert schema.read_text() == text, "a second apply does not add a second block"


def test_a_failure_writing_the_schema_file_leaves_the_runtime_registration_and_says_so(
    bundle, state, tmp_path, monkeypatch
):
    schema = tmp_path / "database.q"
    schema.write_text("x\n")

    def refuse(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(persist, "write", refuse)
    fake = FakeStack(6000, schema_file=str(schema))
    with pytest.raises(applying.ApplyFailed, match="disk full"):
        go(attachment(bundle, runtime_only=False), fake, state)
    m = Manifest.load("pb-uat", state)
    assert (
        m.state == "partial"
        and found(m.find("schema_block", "schema-file", TABLE)).status == "failed"
    )
    assert fake.named("stp1")["schemas"][TABLE], (
        "the runtime registration stays; a restart drops it"
    )
    assert schema.read_text() == "x\n"


# ----------------------------------------------------------------------- verify


def test_verify_before_apply_is_refused(bundle, state):
    att = attachment(bundle)
    config.save(att, state)
    with pytest.raises(AttachmentError, match="has not been applied"):
        verify.verify(att, FakeStack(6000), state)


def test_verify_checks_without_changing_anything_and_opens_the_publisher_gate(bundle, state):
    fake = FakeStack(6000)
    att = attachment(bundle)
    go(att, fake, state)
    published = {p.procname: p.state for p in attach_state.publishers(state)}
    assert published == {"pb_ledger1": "applied"}
    fake.calls.clear()
    result = verify.verify(att, fake, state)
    assert result.ok, [c for c in result.checks if not c.ok]
    assert result.state == "verified"
    assert mutating(fake) == [], "verification only reads"
    assert {p.procname: p.state for p in attach_state.publishers(state)} == {
        "pb_ledger1": "verified"
    }
    assert fake.named("stp1")["rowcount"] == {} and not fake.named("stp1")["msgcount"], (
        "no canary row"
    )


def test_verify_fails_naming_what_is_missing_and_closes_a_gate_that_was_open(bundle, state):
    fake = FakeStack(6000)
    att = attachment(bundle)
    go(att, fake, state)
    assert verify.verify(att, fake, state).state == "verified"
    fake.named("wdb1")["subscribed"].remove(TABLE)
    fake.named("stp1")["subscribers"][TABLE] = 1
    result = verify.verify(att, fake, state)
    assert not result.ok and result.state == "applied"
    failed = {c.name for c in result.checks if not c.ok}
    assert f"wdb1: subscribed to {TABLE}" in failed
    assert any("hold a subscription" in n for n in failed)


def test_verify_notices_a_schema_file_block_that_was_edited(bundle, state, tmp_path):
    schema = tmp_path / "database.q"
    schema.write_text("trade:([] time:`timestamp$())\n")
    fake = FakeStack(6000, schema_file=str(schema))
    att = attachment(bundle, runtime_only=False)
    go(att, fake, state)
    assert verify.verify(att, fake, state).ok
    schema.write_text(schema.read_text().replace("persisted", "edited by hand"))
    result = verify.verify(att, fake, state)
    assert not result.ok
    assert any("schema file" in c.name and "was edited" in c.detail for c in result.checks)


def test_a_verification_that_cannot_reach_the_stack_fails_and_leaves_the_state_alone(bundle, state):
    fake = FakeStack(6000)
    att = attachment(bundle)
    go(att, fake, state)
    cut = FaultIpc(fake, [("probe", 0, "before")])
    result = verify.verify(att, cut, state)
    assert not result.ok and result.state == "applied"
    assert Manifest.load("pb-uat", state).state == "applied"


# --------------------------------------------------------------------- the gate


def test_the_publisher_is_refused_until_verified_and_only_under_its_stack(
    bundle, state, monkeypatch
):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    fake = FakeStack(6000)
    att = attachment(bundle)
    go(att, fake, state)
    paths = default_paths()
    with pytest.raises(UqsError, match="which is applied: it may start once"):
        attach_state.refuse_unverified_start(paths, "pb_ledger1", 6000)
    verify.verify(att, fake, state)
    attach_state.refuse_unverified_start(paths, "pb_ledger1", 6000)
    with pytest.raises(UqsError, match="discovery at 6051, not 6001"):
        attach_state.refuse_unverified_start(paths, "pb_ledger1", None)
    with pytest.raises(UqsError, match="--port 6000"):
        attach_state.refuse_unverified_start(paths, "pb_ledger1", 6100)
    attach_state.refuse_unverified_start(paths, "rdb1 wdb1", None)
    attach_state.refuse_unverified_start(paths, "all", None)
