"""A table's declared TorQ sort rule through attach, verify and detach (#1517).

The stack here sorts by default on `securityId`, a column the attached table
does not have - the case that made SORT_CONFIG a dead end. The table declares
its own rule (`sorting[...]` beside it in tables.q); these tests hold the
lifecycle to the issue: planned and shown without a change, written only as
declared rows into a file left otherwise byte for byte as it was, live on every
process that sorts before the publisher may start, and taken out again only
while exactly as written and needed by nobody else.

The stack is scripted (attach_support.FakeStack); the sort.csv is a real file.
test_attach_sorting_q.py runs the same rows through TorQ's own sort code.
"""

from __future__ import annotations

import dataclasses
import stat
from pathlib import Path

import pytest
from attach_support import LEDGER_Q, FakeStack, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, facts, plan, service, sort_file, sorting, verify, workflow
from uqs.attach import detach as detaching
from uqs.attach.config import AttachmentError, Storage
from uqs.attach.manifest import Manifest, Op
from uqs.attach.preflight import Host
from uqs.attach.schema import SchemaError, TableSpec, check_sorting, parse_sorting
from uqs.paths import repo_root
from uqs.stack import bundles

ROOT = repo_root()
TABLE = "piggybank_general_ledger"
DECLARED = f"sorting[`{TABLE};`sym`time!`p`]"
ORIGINAL = "tabname,att,column,sort\ndefault,p,securityId,1\ndefault,,time,1\nquote,g,sym,0\n"
LOCAL = Host(lambda h, p: False, ("localhost",))


class Publisher:
    def __init__(self) -> None:
        self.on, self.started = False, 0

    def running(self, att) -> bool:
        return self.on

    def start(self, att) -> None:
        self.on, self.started = True, self.started + 1

    def stop(self, att) -> None:
        self.on = False


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)


@pytest.fixture
def world(tmp_path: Path):
    csv = tmp_path / "appconfig" / "sort.csv"
    csv.parent.mkdir()
    csv.write_text(ORIGINAL)
    csv.chmod(0o640)
    fake = FakeStack(6000)
    fake.use_sort_file(csv, sort_process=True)
    state = tmp_path / "attachments"
    for name in ("tplogs", "wdb", "hdb"):  # empty: the evidence that nothing was published
        (tmp_path / name).mkdir()
    storage = Storage(str(tmp_path / "tplogs"), "", str(tmp_path / "wdb"), str(tmp_path / "hdb"))
    fake.named("stp1")["tplogdir"] = str(tmp_path / "tplogs")
    fake.named("wdb1")["savedir"] = f":{tmp_path / 'wdb'}"
    fake.named("wdb1")["hdbdir"] = f":{tmp_path / 'hdb'}"

    def make(name: str = "pb-uat", tables_q: str = LEDGER_Q + "\n" + DECLARED, folder="b"):
        bundle = make_bundle(tmp_path / folder, tables_q)
        att = service.create(
            name, "pb_general_ledger", bundle, 6000, ROOT, runtime_only=True, storage=storage
        )
        config.save(att, state)
        return att

    return fake, make, state, csv


def apply_(att, fake, state, **kw):
    return applying.apply(att, fake, ROOT, state_dir=state, **kw)


def detach_(att, fake, state, **kw):
    return detaching.detach(att, fake, Publisher(), host=LOCAL, state_dir=state, **kw)


def live(fake: FakeStack, proc: str, table: str = TABLE) -> list[tuple]:
    return [
        (r["att"], r["col"], r["sort"])
        for r in fake.named(proc)["sortrows"]
        if r["tabname"] == table
    ]


def codes(p: plan.Plan, level: str = "blocker") -> set[str]:
    return {f.code for f in p.findings if f.level == level}


# ----------------------------------------------------------------- the declaration


def test_a_sorting_line_reads_as_columns_in_order_with_their_attributes():
    assert parse_sorting(DECLARED) == (TABLE, (("sym", "p"), ("time", "")))
    assert parse_sorting(".qetl.plant.sorting[`t;(enlist `time)!enlist `];") == (
        "t",
        (("time", ""),),
    )
    assert parse_sorting("t:([] time:`timestamp$())") is None
    with pytest.raises(SchemaError, match="one attribute per column"):
        parse_sorting("sorting[`t;`sym`time!`p]")


@pytest.mark.parametrize(
    ("rule", "why"),
    [
        ((("securityId", "p"),), "is not one of the table's columns"),
        ((("ladder", ""),), "holds a list per row"),
        ((("sym", "x"),), "is not one TorQ's sort.csv takes"),
        ((("time", ""), ("sym", "p")), "only holds on the first sort column"),
        ((("sym", "p"), ("sym", "")), "is named twice"),
    ],
)
def test_an_invalid_declaration_is_refused(rule, why):
    spec = TableSpec(TABLE, (("time", "p"), ("sym", "s"), ("ladder", "F")), rule)
    with pytest.raises(SchemaError, match=why):
        check_sorting(spec)


def test_an_invalid_declaration_is_refused_before_the_stack_is_asked_anything(world):
    fake, make, state, csv = world
    with pytest.raises(AttachmentError, match="sort column securityId is not one of"):
        make(tables_q=LEDGER_Q + f"\nsorting[`{TABLE};(enlist `securityId)!enlist `p]")
    assert fake.calls == [] and csv.read_text() == ORIGINAL


def test_a_bundle_takes_a_sorting_line_only_for_a_table_it_defines(tmp_path):
    good = bundles.read_bundle(make_bundle(tmp_path / "a", LEDGER_Q + "\n" + DECLARED))
    lines, tables = bundles.table_lines(good, set())
    assert tables == [TABLE] and lines[-1] == ".qetl.plant." + DECLARED
    bad = bundles.read_bundle(make_bundle(tmp_path / "b", LEDGER_Q + "\nsorting[`other;`x!`]"))
    with pytest.raises(bundles.BundleError, match="sorting\\[\\] for other"):
        bundles.table_lines(bad, set())


def test_the_declaration_is_saved_with_the_attachment(world):
    fake, make, state, csv = world
    att = make()
    assert att.table(TABLE).sort == (("sym", "p"), ("time", ""))
    assert config.load("pb-uat", state).table(TABLE).sort == att.table(TABLE).sort


# ------------------------------------------------------------------------ plan


def test_without_a_declaration_the_incompatible_default_is_still_a_blocker(world):
    fake, make, state, csv = world
    fake.named("wdb1")["sortrows"] = [
        {"tabname": "default", "att": "p", "col": "securityId", "sort": True}
    ]
    p = plan.build(make(tables_q=LEDGER_Q), fake, ROOT)
    assert "SORT_CONFIG" in codes(p) and not p.ok and p.sort_changes == []


def test_a_missing_rule_is_planned_as_an_addition_read_by_every_process_that_sorts(world):
    fake, make, state, csv = world
    p = plan.build(make(), fake, ROOT)
    assert p.ok, [f for f in p.findings if f.level == "blocker"]
    assert "SORT_CONFIG" not in codes(p) and "SORT_RULES_ADD" in codes(p, "info")
    (change,) = p.as_dict()["sorting"]
    assert change == {
        "table": TABLE,
        "file": str(csv),
        "status": "add",
        "declared": [["p", "sym", True], ["", "time", True]],
        "existing": [],
        "processes": ["wdb1", "sort1"],
        "reload": ["wdb1", "sort1"],
        "sha": sort_file.sha(ORIGINAL),
    }
    assert any(c["process"] == "sort.csv" and "add 2" in c["action"] for c in p.changes())


def test_planning_changes_nothing(world):
    """`--dry-run` is the plan: no write to the file, no manifest, no reload."""
    fake, make, state, csv = world
    before = (csv.read_bytes(), csv.stat().st_mtime_ns)
    plan.build(make(), fake, ROOT)
    assert (csv.read_bytes(), csv.stat().st_mtime_ns) == before
    assert fake.ops() == {"probe"} and not Manifest.exists("pb-uat", state)
    assert live(fake, "wdb1") == [] and live(fake, "sort1") == []


def test_an_existing_compatible_rule_is_reused_and_nothing_is_written(world):
    fake, make, state, csv = world
    csv.write_text(ORIGINAL + f"{TABLE},p,sym,1\n{TABLE},,time,1\n")
    fake.use_sort_file(csv)
    fake._load_sort(fake.named("sort1"))
    att = make()
    p = plan.build(att, fake, ROOT)
    assert p.ok and p.sort_changes[0].status == "present" and p.sort_changes[0].reload == []
    text = csv.read_bytes()
    apply_(att, fake, state)
    assert csv.read_bytes() == text and "sort_reload" not in fake.ops()
    op = Manifest.load("pb-uat", state).find("sort_rows", str(csv), TABLE)
    assert op is not None and op.ownership == "existing"


def test_a_conflicting_rule_is_a_blocker_and_nothing_is_touched(world):
    fake, make, state, csv = world
    csv.write_text(ORIGINAL + f"{TABLE},p,account,1\n")
    fake.use_sort_file(csv)
    att = make()
    p = plan.build(att, fake, ROOT)
    assert "SORT_CONFLICT" in codes(p)
    with pytest.raises(applying.ApplyRefused, match="SORT_CONFLICT"):
        apply_(att, fake, state)
    assert csv.read_text() == ORIGINAL + f"{TABLE},p,account,1\n"
    assert "sort_reload" not in fake.ops()


def test_a_conflicting_live_rule_set_by_hand_is_a_blocker(world):
    fake, make, state, csv = world
    fake.named("sort1")["sortrows"].append(
        {"tabname": TABLE, "att": "", "col": "account", "sort": True}
    )
    assert "SORT_CONFLICT" in codes(plan.build(make(), fake, ROOT))


def test_live_rules_that_differ_from_the_file_for_other_tables_block_the_reload(world):
    """A reload reads the whole file: it would also apply someone else's edit."""
    fake, make, state, csv = world
    csv.write_text(ORIGINAL + "trade,g,sym,0\n")
    p = plan.build(make(), fake, ROOT)
    assert "SORT_DRIFT" in codes(p)
    assert {f.component for f in p.findings if f.code == "SORT_DRIFT"} == {"wdb1", "sort1"}


def test_a_running_process_that_cannot_be_reloaded_is_a_blocker_naming_the_manual_step(
    world, monkeypatch
):
    fake, make, state, csv = world
    fake.down.add(6006)
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: port == 6006)  # up, not answering
    p = plan.build(make(), fake, ROOT)
    found = [f for f in p.findings if f.code == "SORT_RELOAD_UNAVAILABLE"]
    assert found and found[0].component == "sort1" and "by hand" in found[0].action


def test_a_sort_process_that_is_not_running_reads_the_file_when_it_starts(world):
    fake, make, state, csv = world
    fake.down.add(6006)
    att = make()
    p = plan.build(att, fake, ROOT)
    assert p.ok and "SORT_READER_STOPPED" in codes(p, "warning")
    assert [r.procname for r in p.sort_changes[0].reload] == ["wdb1"]
    apply_(att, fake, state)
    assert verify.verify(att, fake, state, ROOT).ok


def test_a_file_on_another_machine_cannot_be_written(world):
    fake, make, state, csv = world
    att = make()
    away = dataclasses.replace(att, stack=dataclasses.replace(att.stack, host="far.invalid"))
    p = plan.build(away, fake, ROOT)
    assert "SORT_FILE" in codes(p)
    assert any("can only be edited from that machine" in f.message for f in p.findings)


def test_a_parted_writedown_needs_a_p_column(world):
    fake, make, state, csv = world
    fake.named("wdb1")["writedownmode"] = "partbyattr"
    p = plan.build(make(tables_q=LEDGER_Q + f"\nsorting[`{TABLE};`sym`time!`g`]"), fake, ROOT)
    assert "SORT_DECLARATION" in codes(p)


# ----------------------------------------------------------------------- apply


def test_apply_adds_only_the_declared_rows_keeps_the_file_and_makes_them_live(world):
    fake, make, state, csv = world
    att = make()
    result = apply_(att, fake, state)
    assert result.state == "applied"
    assert csv.read_text() == ORIGINAL + f"{TABLE},p,sym,1\n{TABLE},,time,1\n"
    assert stat.S_IMODE(csv.stat().st_mode) == 0o640, "the file's permissions are kept"
    want = [("p", "sym", True), ("", "time", True)]
    assert live(fake, "wdb1") == want and live(fake, "sort1") == want
    op = Manifest.load("pb-uat", state).find("sort_rows", str(csv), TABLE)
    assert op is not None and op.ownership == "created" and op.status == "done"
    assert Path(op.post["backup"]).read_text() == ORIGINAL, "a copy was kept before the write"
    assert op.post["run"] == f"{TABLE},p,sym,1\n{TABLE},,time,1\n"
    assert verify.verify(att, fake, state, ROOT).ok


def test_the_rows_follow_the_files_column_order_and_line_endings(world):
    fake, make, state, csv = world
    csv.write_bytes(b"tabname,column,att,sort\r\ndefault,sym,p,1")  # no final newline either
    fake.use_sort_file(csv, sort_process=False)
    fake._load_sort(fake.named("sort1"))
    apply_(make(), fake, state)
    assert csv.read_bytes() == (
        b"tabname,column,att,sort\r\ndefault,sym,p,1\r\n"
        + f"{TABLE},sym,p,1\r\n{TABLE},time,,1\r\n".encode()
    )


def test_applying_again_adds_no_duplicate_rows(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    text = csv.read_text()
    fake.calls.clear()
    again = apply_(att, fake, state)
    assert csv.read_text() == text and again.mutations() == 0
    assert "sort_reload" not in fake.ops()


def test_a_file_changed_since_the_confirmed_plan_is_refused_before_anything_changes(world):
    fake, make, state, csv = world
    att = make()
    shown = plan.build(att, fake, ROOT)
    with csv.open("a") as fh:  # the operator edits it after looking at the plan
        fh.write("trade,g,sym,0\n")
    fake.use_sort_file(csv, sort_process=False)
    fake._load_sort(fake.named("sort1"))
    edited = csv.read_text()
    with pytest.raises(AttachmentError, match="changed since the plan you confirmed"):
        apply_(att, fake, state, expected_sort={c.path: c.sha for c in shown.sort_changes})
    assert csv.read_text() == edited and "stp_register" not in fake.ops()


def test_a_file_changed_between_planning_and_writing_is_refused(world, monkeypatch):
    """Apply's own plan read the file; someone appends to it before the write."""
    fake, make, state, csv = world
    real = sorting.read

    def racing(path: Path) -> sort_file.SortFile:
        found = real(path)
        path.write_text(found.text + "trade,g,sym,0\n")
        return found

    att = make()
    monkeypatch.setattr(sorting, "read", racing)  # what apply_change reads just before writing
    with pytest.raises(applying.ApplyFailed, match="changed since it was planned"):
        apply_(att, fake, state)
    assert TABLE not in csv.read_text(), "nothing of ours was written"
    op = Manifest.load("pb-uat", state).find("sort_rows", str(csv), TABLE)
    assert op is not None and op.status == "failed"


def test_a_failed_reload_leaves_the_publisher_stopped_and_a_rerun_finishes(world):
    fake, make, state, csv = world
    att = make()
    fake.named("sort1")["reload_fails"] = "failed to open sort.csv"
    publisher = Publisher()
    shown = plan.build(att, fake, ROOT)
    resolved = workflow.Resolved(att, "new", config.directory("pb-uat", state))
    out = workflow.execute(resolved, shown, fake, ROOT, publisher, state_dir=state)
    assert not out.ok and [s.name for s in out.stages] == ["save", "apply"]
    assert "a file alone is not configuration" in out.stages[-1].detail
    assert publisher.started == 0 and not publisher.on
    m = Manifest.load("pb-uat", state)
    assert m.state == "partial"
    op = m.find("sort_rows", str(csv), TABLE)
    assert op is not None and op.ownership == "created" and "not live on sort1" in op.note
    assert "stp_register" not in fake.ops(), "nothing after the sort rules was started"

    del fake.named("sort1")["reload_fails"]
    out = workflow.execute(resolved, plan.build(att, fake, ROOT), fake, ROOT, publisher,
                           state_dir=state)  # fmt: skip
    assert out.ok and publisher.started == 1
    assert csv.read_text().count(f"{TABLE},p,sym,1") == 1, "the retry did not write twice"


def test_verify_fails_when_a_process_lost_the_live_rule(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    assert verify.verify(att, fake, state, ROOT).ok
    fake.named("sort1")["sortrows"] = [r for r in fake.named("sort1")["sortrows"]
                                       if r["tabname"] != TABLE]  # fmt: skip  # a restart, say
    result = verify.verify(att, fake, state, ROOT)
    failed = [c.name for c in result.checks if not c.ok]
    assert failed == [f"sort1: {TABLE} is sorted by the declared rule, live"]
    assert result.state == "applied", "the start gate closes again"


# ---------------------------------------------------------------------- detach


def test_detach_removes_exactly_the_rows_it_added_and_reloads(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    with csv.open("a") as fh:  # an unrelated operator edit after attaching is kept
        fh.write("trade,g,sym,0\n")
    for name in ("wdb1", "sort1"):
        fake._load_sort(fake.named(name))
    result = detach_(att, fake, state)
    assert result.outcome == "rolled_back", result.blockers
    assert csv.read_text() == ORIGINAL + "trade,g,sym,0\n"
    assert live(fake, "wdb1") == [] and live(fake, "sort1") == []
    assert stat.S_IMODE(csv.stat().st_mode) == 0o640
    op = Manifest.load("pb-uat", state).find("sort_rows", str(csv), TABLE)
    assert op is not None and op.status == "reverted"


def test_detach_leaves_a_reused_rule_in_place(world):
    fake, make, state, csv = world
    csv.write_text(ORIGINAL + f"{TABLE},p,sym,1\n{TABLE},,time,1\n")
    for name in ("wdb1", "sort1"):
        fake._load_sort(fake.named(name))
    att = make()
    apply_(att, fake, state)
    text = csv.read_text()
    assert detach_(att, fake, state).outcome == "rolled_back"
    assert csv.read_text() == text and live(fake, "wdb1") == [
        ("p", "sym", True),
        ("", "time", True),
    ]


def test_detach_refuses_rows_an_operator_modified(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    csv.write_text(csv.read_text().replace(f"{TABLE},,time,1", f"{TABLE},,time,0"))
    for name in ("wdb1", "sort1"):
        fake._load_sort(fake.named(name))
    edited = csv.read_text()
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    assert any("an operator changed them" in b.reason for b in result.blockers)
    assert csv.read_text() == edited


def test_detach_refuses_when_the_operator_added_rows_for_the_table(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    with csv.open("a") as fh:
        fh.write(f"{TABLE},g,account,0\n")
    for name in ("wdb1", "sort1"):
        fake._load_sort(fake.named(name))
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    assert any("besides the ones this attachment added" in b.reason for b in result.blockers)


def test_detach_keeps_rows_another_attachment_relies_on(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    other = Manifest(
        "other-att", "tok", state="verified", path=Manifest.path_for("other-att", state)
    )
    other.operations.append(Op(1, "sort_rows", str(csv), TABLE, "done", "existing"))
    other.save()
    config.save(make("other-att", folder="b2"), state)
    text = csv.read_text()
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    assert any("relied on by attachment other-att" in b.reason for b in result.blockers)
    assert csv.read_text() == text


def test_a_failed_reload_during_detach_is_partial_and_a_retry_finishes(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    fake.named("sort1")["reload_fails"] = "disk unreadable"
    with pytest.raises(AttachmentError, match="rollback stopped"):
        detach_(att, fake, state)
    m = Manifest.load("pb-uat", state)
    assert m.state == "rollback_partial"
    op = m.find("sort_rows", str(csv), TABLE)
    assert op is not None and op.status == "done" and op.post["removed"]
    assert csv.read_text() == ORIGINAL, "the file is reverted, the live state is not yet"
    del fake.named("sort1")["reload_fails"]
    assert detach_(att, fake, state).outcome == "rolled_back"
    assert csv.read_text() == ORIGINAL and live(fake, "sort1") == []


def test_a_write_whose_outcome_was_lost_is_settled_by_the_files_hash(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    m = Manifest.load("pb-uat", state)
    op = m.find("sort_rows", str(csv), TABLE)
    assert op is not None
    op.status, op.ownership, op.post = "pending", "", {}  # as if the connection dropped mid-write
    m.save()
    assert detach_(att, fake, state).outcome == "rolled_back"
    assert csv.read_text() == ORIGINAL


def test_after_publication_the_rows_are_retained(world):
    fake, make, state, csv = world
    att = make()
    apply_(att, fake, state)
    fake.publish(TABLE)
    text = csv.read_text()
    result = detach_(att, fake, state)
    assert result.outcome == "detached" and csv.read_text() == text
    op = Manifest.load("pb-uat", state).find("sort_rows", str(csv), TABLE)
    assert op is not None and op.status == "retained"
