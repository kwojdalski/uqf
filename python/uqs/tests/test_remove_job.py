"""`uqs job remove`: undoing a scaffold, worked out from the tree.

The property that matters is the round trip: scaffold into a copy of the real
tree, remove, and every file is BYTE-identical to before. A removal that left
a blank line in plant_tables.q or a stray backtick in a list would pass a test
that only checked the job file was gone.

The other half is what it must not take: a table another job still reads, a
source another worker still uses, and a job that has been written.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from uqs.paths import CATALOG_FILE, STACK_TABLES_TEST, TABLES_FILE, UqsError
from uqs.scaffold import jobs, write
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import definition_columns, parse_columns, table_definition
from uqs.scaffold.docs import SHOWCASE_PAGE, STACK_PAGE
from uqs.scaffold.horizon import horizon_job
from uqs.scaffold.normalizer import normalizer
from uqs.scaffold.profile import PROFILES_FILE, UNPROFILED_FILE
from uqs.scaffold.reaction import reaction
from uqs.scaffold.remove import plan_removal

UQF_ROOT = Path(__file__).resolve().parents[3]
#: Every file a scaffold appends to or a removal edits.
_TRACKED = (
    TABLES_FILE,
    CATALOG_FILE,
    STACK_TABLES_TEST,
    PROFILES_FILE,
    UNPROFILED_FILE,
    STACK_PAGE,
    SHOWCASE_PAGE,
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of what scaffolding and removal touch: src/, the plant's q files,
    the test list, profiles.py, unprofiled.py and the two doc pages a process is named on."""
    shutil.copytree(UQF_ROOT / "src", tmp_path / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", tmp_path / "scripts" / "processes")
    for rel in (STACK_TABLES_TEST, PROFILES_FILE, UNPROFILED_FILE, STACK_PAGE, SHOWCASE_PAGE):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, tmp_path / rel)
    return tmp_path


def _snapshot(root: Path) -> dict[Path, str]:
    files = {rel: (root / rel).read_text() for rel in _TRACKED}
    for d in (
        "src/etl/streaming",
        "src/etl/workers",
        "src/etl/sources",
        "src/etl/reactions",
        "tests/q",
        "scripts/examples",
    ):
        files.update({p.relative_to(root): p.read_text() for p in (root / d).glob("*.q")})
    return files


def _remove(root: Path, name: str, **kwargs) -> None:
    plan_removal(root, name, **kwargs).apply(root)


def test_a_feed_and_its_consumer_round_trip_exactly(tree):
    before = _snapshot(tree)
    write.apply_plan(
        jobs.streaming_job(
            "pulse",
            [],
            "pulse",
            "sym:symbol, px:float",
            period="0D00:00:00.500",
            profile="crypto",
            known_profiles=("crypto",),
        ),
        tree,
    )
    write.apply_plan(
        jobs.streaming_job(
            "pulsestat",
            ["pulse"],
            "pulse_stats",
            "sym:symbol, n:long",
            known_tables={"pulse"},
            unprofiled="a demo aggregate, started by hand",
            known_profiles=("crypto",),
        ),
        tree,
    )
    assert _snapshot(tree) != before
    _remove(tree, "pulsestat")
    _remove(tree, "pulse")
    after = _snapshot(tree)
    changed = sorted(str(p) for p in set(before) | set(after) if before.get(p) != after.get(p))
    assert not changed, f"not restored: {changed}"


def test_a_backfill_round_trips_with_its_source(tree):
    before = _snapshot(tree)
    write.apply_plan(
        backfill.bounded_worker(
            "ledger", "ledger", "sym:symbol, amt:float", transport="odbc", check=True
        ),
        tree,
    )
    _remove(tree, "ledger")
    assert _snapshot(tree) == before


def test_a_reaction_round_trips_exactly(tree):
    """Its file and its test - and nothing else is touched, since a reaction
    has no table, profile or port, and no list names its suite."""
    before = _snapshot(tree)
    plan = reaction(
        "rebuild_exposure",
        "demo_deals",
        ["positions"],
        producers={"demo_deals": ["deals_backfill1"]},
        taken=set(),
    )
    write.apply_plan(plan, tree)
    assert _snapshot(tree) != before
    removal = plan_removal(tree, "rebuild_exposure")
    assert not removal.rewrites
    removal.apply(tree)
    assert _snapshot(tree) == before


def test_a_feed_whose_file_is_gone_is_still_removed_by_name(tree):
    """#2945: with the job file deleted by hand, its registrations stayed, each
    refused `uqs job new`'s rerun in turn, and remove found no job. They are
    found by name now, and the round trip is exact."""
    before = _snapshot(tree)
    write.apply_plan(
        jobs.streaming_job("half", [], "half_out", "sym:symbol, px:float", unprofiled="on demand"),
        tree,
    )
    (tree / "src/etl/streaming/half.q").unlink()
    removal = plan_removal(tree, "half")
    assert "no job file for half" in removal.notes[0]
    removal.apply(tree)
    assert _snapshot(tree) == before


def test_a_backfill_whose_worker_is_gone_takes_its_source_and_example(tree):
    before = _snapshot(tree)
    write.apply_plan(backfill.bounded_worker("ledger", "ledger", "sym:symbol, amt:float"), tree)
    (tree / "src/etl/workers/ledger_backfill.q").unlink()
    _remove(tree, "ledger")
    assert _snapshot(tree) == before


def test_a_name_with_nothing_left_is_still_refused_as_no_job(tree):
    with pytest.raises(UqsError, match="0 job files match 'nosuchjob'"):
        plan_removal(tree, "nosuchjob")


def test_a_written_test_left_behind_is_refused_without_the_override(tree):
    """The leftovers go on the rule the job file's would: a file no longer
    as the scaffold wrote it is written work."""
    write.apply_plan(
        jobs.streaming_job("half", [], "half_out", "sym:symbol, px:float", unprofiled="x"), tree
    )
    (tree / "src/etl/streaming/half.q").unlink()
    test = tree / "tests/q/test_half.q"
    test.write_text(test.read_text().replace("SCAFFOLDED", "written"))
    with pytest.raises(UqsError, match="tests/q/test_half.q no longer read as the scaffold"):
        plan_removal(tree, "half")
    assert plan_removal(tree, "half", force=True).deletes == [Path("tests/q/test_half.q")]


_FEED = "sym:symbol, px:float"
_FEED_TABLE = table_definition("rt_ticks", parse_columns(_FEED))
_REF_TABLE = table_definition("rt_ref", parse_columns(_FEED))
_DEFS = {"rt_ticks": definition_columns(_FEED_TABLE), "rt_ref": definition_columns(_REF_TABLE)}
_KNOWN = {"rt_ticks", "rt_ref"}

#: One plan per job kind the scaffold supports, built when the test runs - each
#: reads the two feeds the tree holds by then.
_KINDS = {
    "poll feed": lambda: jobs.streaming_job("rtpoll", [], "rt_poll", _FEED, poll=True),
    "etl handler": lambda: jobs.streaming_job(
        "rtetl", ["rt_ticks"], "rt_stats", "sym:symbol, n:long", known_tables=_KNOWN
    ),
    "passthrough": lambda: jobs.streaming_job(
        "rtpass",
        ["rt_ticks"],
        "rt_echo",
        _FEED,
        known_tables=_KNOWN,
        transform="passthrough",
        definitions={"rt_ticks": _FEED_TABLE},
    ),
    "derive": lambda: jobs.streaming_job(
        "rtderive",
        ["rt_ticks"],
        "rt_derived",
        "sym:symbol, n:long",
        known_tables=_KNOWN,
        transform="derive",
        definitions={"rt_ticks": _FEED_TABLE},
    ),
    "normalizer": lambda: normalizer(
        "rtnorm",
        ["rt_ticks"],
        parse_columns("sym:symbol, mid:float"),
        {"rt_ticks": _DEFS["rt_ticks"]},
        known_tables=_KNOWN,
    ),
    "horizon": lambda: horizon_job(
        "rthz",
        ["rt_ticks", "rt_ref"],
        parse_columns("sym:symbol, px:float"),
        _DEFS,
        "0D00:00:10",
        known_tables=_KNOWN,
    ),
    "backfill (ipc)": lambda: backfill.bounded_worker("rtbf", "rt_hist", _FEED),
    "backfill (derive)": lambda: backfill.bounded_worker(
        "rtbfd", "rt_bfd", _FEED, transform="derive"
    ),
    "backfill (local)": lambda: backfill.bounded_worker(
        "rtloc", "rt_loc", _FEED, transport="local"
    ),
}


@pytest.mark.parametrize("label", list(_KINDS))
def test_every_job_kind_scaffolds_and_removes_exactly(tree, label):
    """Scaffold then remove, for each supported kind: the tree is byte-identical
    to before, and the scaffold named the suite after the job it tests without
    asking any list to know it (#1412)."""
    write.apply_plan(jobs.streaming_job("rt_feed", [], "rt_ticks", _FEED), tree)
    write.apply_plan(jobs.streaming_job("rt_reffeed", [], "rt_ref", _FEED), tree)
    before = _snapshot(tree)
    plan = _KINDS[label]()
    write.apply_plan(plan, tree)
    assert _snapshot(tree) != before
    tests = [
        a for a in plan.actions if a.path.parent == Path("tests/q") and a.mode.value == "create"
    ]
    assert tests, "a kind with no test would be a job nothing checks"
    suite = jobs.test_namespace(plan.name)
    assert suite == f"qt.qpipe.job.{plan.name}"
    assert all(f".{suite}." in a.body for a in tests), (label, suite)
    _remove(tree, plan.name)
    assert _snapshot(tree) == before, label


#: A hand-written q script that reads the pulse table without subscribing to it.
_PULSE_REPORT = Path("scripts/pulse_report.q")


def _pulse_and_a_reader(tree: Path, extra: str = "") -> Path:
    """The pulse feed, and a q script that reads its table: a use of the
    table, but no subscription, so removing pulse is not refused (#2925)."""
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    (tree / _PULSE_REPORT).write_text("/ a report\nn:count select from `pulse\n" + extra)
    return tree / _PULSE_REPORT


def test_a_table_something_else_reads_is_kept(tree):
    _pulse_and_a_reader(tree)
    removal = plan_removal(tree, "pulse")
    assert TABLES_FILE not in removal.rewrites, "pulse_report.q still reads pulse"
    assert any(f"kept table pulse: {_PULSE_REPORT} uses it" in n for n in removal.notes)


def test_a_producer_another_job_subscribes_to_is_refused_naming_the_subscriber(tree):
    """#2925: the table was kept, the removal exited 0, and every profile
    holding the subscriber then started it with nothing feeding it."""
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    write.apply_plan(
        jobs.streaming_job("pulsestat", ["pulse"], "pulse_stats", "n:long", known_tables={"pulse"}),
        tree,
    )
    refusal = (
        r"pulse cannot be removed while another job depends on it: pulsestat "
        r"\(src/etl/streaming/pulsestat.q\) subscribes to pulse, which only pulse publishes "
        r"- remove it first"
    )
    for force in (False, True):
        with pytest.raises(UqsError, match=refusal):
            plan_removal(tree, "pulse", force=force)


def test_a_producer_whose_table_something_else_publishes_is_removable(tree):
    """A second publisher still feeds the subscriber, so nothing is refused."""
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    write.apply_plan(
        jobs.streaming_job("pulse_too", [], "pulse", None, known_tables={"pulse"}), tree
    )
    write.apply_plan(
        jobs.streaming_job("pulsestat", ["pulse"], "pulse_stats", "n:long", known_tables={"pulse"}),
        tree,
    )
    removal = plan_removal(tree, "pulse")
    assert any("kept table pulse" in n for n in removal.notes), removal.notes


def test_a_source_another_worker_reads_is_kept(tree):
    write.apply_plan(backfill.bounded_worker("ledger", "ledger", "amt:float"), tree)
    write.apply_plan(
        backfill.bounded_worker(
            "ledger_eur",
            "ledger",
            None,
            source="ledger",
            reuse_source=True,
            define_table=False,
            partition="EURUSD",
        ),
        tree,
    )
    removal = plan_removal(tree, "ledger")
    assert Path("src/etl/sources/ledger.q") not in removal.deletes
    assert any("kept table ledger" in n for n in removal.notes)


def test_a_written_job_is_refused_without_force(tree):
    """alert_sink carries no SCAFFOLDED marker: it is real work, not a scaffold."""
    with pytest.raises(UqsError, match="no SCAFFOLDED marker left"):
        plan_removal(tree, "alert_sink")
    assert (
        Path("src/etl/streaming/alert_sink.q")
        in plan_removal(tree, "alert_sink", force=True).deletes
    )


def test_a_name_that_matches_no_job_is_refused(tree):
    with pytest.raises(UqsError, match="0 job files match 'nope'"):
        plan_removal(tree, "nope")


def test_nothing_is_touched_until_apply(tree):
    before = _snapshot(tree)
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    scaffolded = _snapshot(tree)
    plan_removal(tree, "pulse")
    assert _snapshot(tree) == scaffolded != before


# ------------------------------------------------ what still names it (#717)


def _referenced(removal) -> set[str]:
    return {str(r.path) for r in removal.references}


def test_a_fresh_scaffold_leaves_nothing_naming_it(tree):
    """Every file a scaffold writes is one the removal edits or deletes, so a
    job nobody has built on is reported clean - the rewritten files are
    searched in their NEW text, not as they are on disk."""
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    assert plan_removal(tree, "pulse").references == []


def test_an_example_and_a_docs_page_naming_it_are_listed(tree):
    """The acceptance case: hdb_transfer has an example script and a docs page,
    which --even-if-edited would otherwise have left stale."""
    for rel in ("scripts/examples/hdb_transfer_example.q", "docs/scaffolding/hdb-transfer.md"):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, tree / rel)
    removal = plan_removal(tree, "hdb_transfer", force=True)
    assert {
        "scripts/examples/hdb_transfer_example.q",
        "docs/scaffolding/hdb-transfer.md",
    } <= _referenced(removal)
    assert all(r.line > 0 for r in removal.references), "each with its line number"
    assert "still named in" in removal.render()


def test_what_the_removal_keeps_is_not_reported(tree):
    """A table something else reads is kept, so its other mentions are why it
    stays - not something left behind."""
    _pulse_and_a_reader(tree)
    removal = plan_removal(tree, "pulse")
    assert str(_PULSE_REPORT) not in _referenced(removal)


def test_a_kept_table_does_not_hide_a_call_into_the_removed_job(tree):
    """#1589: the job and its table share a name. A script reads the table,
    so the table stays - but a call into the removed job's namespace is still a
    reference, not a use of the table."""
    _pulse_and_a_reader(tree, "poke:{[] .qpipe.job.pulse.on_timer[]}\n")
    removal = plan_removal(tree, "pulse")
    assert any("kept table pulse" in n for n in removal.notes)
    refs = [r for r in removal.references if r.path == _PULSE_REPORT]
    assert [r.text for r in refs] == ["poke:{[] .qpipe.job.pulse.on_timer[]}"]


def test_a_name_inside_a_longer_one_is_not_a_reference(tree):
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    (tree / "docs").mkdir(exist_ok=True)
    (tree / "docs" / "note.md").write_text("pulsed and pulse_extra are other things\n")
    assert plan_removal(tree, "pulse").references == []
    (tree / "docs" / "note.md").write_text("see pulse1\n")
    assert _referenced(plan_removal(tree, "pulse")) == {"docs/note.md"}, "its process is named"


def test_strict_refuses_and_dry_run_only_says_it_would(tree, monkeypatch):
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli.job import remove as remove_cli

    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    (tree / "docs").mkdir(exist_ok=True)
    (tree / "docs" / "note.md").write_text("the pulse job\n")
    monkeypatch.setattr(remove_cli, "_paths", lambda: type("P", (), {"repo_root": tree})())
    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(remove_cli, "_die", record)
    runner = CliRunner()
    shown = runner.invoke(cli.app, ["job", "remove", "pulse", "--dry-run", "--strict"])
    assert shown.exit_code == 0 and "--strict would refuse" in shown.output
    result = runner.invoke(cli.app, ["job", "remove", "pulse", "--strict", "--yes"])
    assert result.exit_code == 1 and refused and "1 line(s)" in refused[0]
    assert (tree / "src/etl/streaming/pulse.q").is_file(), "and nothing was removed"


def test_a_guest_writer_does_not_take_an_authored_table(tree):
    """#1585: a job that only publishes onto a table it did not define leaves
    that table - its definition, comment, catalog entry and expected entry."""
    write.apply_plan(jobs.streaming_job("audit_existing", [], "audit_existing", "px:float"), tree)
    tables = tree / TABLES_FILE
    # Make the table authored: the scaffold's comment rewritten by hand.
    text = tables.read_text().replace(
        "/ audit_existing1's output. SCAFFOLDED: <one line: what a row means>",
        "/ Hand-written: an audit print, one row per check.",
    )
    tables.write_text(text)
    # Nothing else writes or reads it: only its authored definition remains.
    (tree / "src/etl/streaming/audit_existing.q").unlink()
    (tree / "tests/q/test_audit_existing.q").unlink()
    before = _snapshot(tree)
    write.apply_plan(
        jobs.streaming_job(
            "audit_guest", [], "audit_existing", None, known_tables={"audit_existing"}
        ),
        tree,
    )
    removal = plan_removal(tree, "audit_guest")
    assert any("kept table audit_existing" in n for n in removal.notes)
    removal.apply(tree)
    after = _snapshot(tree)
    changed = sorted(str(p) for p in set(before) | set(after) if before.get(p) != after.get(p))
    assert not changed, f"not restored: {changed}"


def test_a_feed_with_a_list_column_round_trips_with_its_nested_declaration(tree):
    # #1567: the table's definition went but its `.qetl.plant.nested` line
    # stayed, and plant_tables.q then failed to load: no table to resolve.
    before = _snapshot(tree)
    write.apply_plan(
        jobs.streaming_job(
            "listbook",
            [],
            "listbook",
            "sym:symbol, levels:list",
            unprofiled="a demo feed, started by hand",
            known_profiles=("crypto",),
        ),
        tree,
    )
    assert ".qetl.plant.nested[`listbook;" in (tree / TABLES_FILE).read_text()
    _remove(tree, "listbook")
    assert ".qetl.plant.nested[`listbook;" not in (tree / TABLES_FILE).read_text()
    after = _snapshot(tree)
    changed = sorted(str(p) for p in set(before) | set(after) if before.get(p) != after.get(p))
    assert not changed, f"not restored: {changed}"


def _literal(text: str, name: str):
    """The dict literal `name` is assigned in a profiles module's source."""
    import ast

    for node in ast.parse(text).body:
        if (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", None) == name
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"no {name} in that source")


@pytest.mark.parametrize("proc", ["default", "crypto"])
def test_removing_a_process_named_like_a_profile_keeps_that_profile(proc):
    # #1566: a process named `default` was removed by dropping every entry
    # keyed `"default": (`, which is PROFILES["default"] itself.
    from uqs.scaffold import profile
    from uqs.scaffold.remove import _drop_from_profiles

    before = PROFILES_FILE.read_text()
    joined = profile._with_member(before, "arbitrage", proc)
    after = _drop_from_profiles(joined, proc)
    assert _literal(after, "PROFILES") == _literal(before, "PROFILES")
    exempt = UNPROFILED_FILE.read_text()
    assert _drop_from_profiles(exempt, proc) == exempt


def _fx_one_per_line(text: str, extra: str) -> str:
    """profiles.py with the fx profile written a member per line, plus `extra`
    - the layout "add it by hand" leaves a profile past the line limit."""
    one_line = '    "fx": ("posbook1", "fxpositions1", "last_value1"),\n'
    assert one_line in text, "the fx profile as profiles.py writes it"
    members = ["posbook1", "fxpositions1", extra, "last_value1"]
    return text.replace(
        one_line, '    "fx": (\n' + "".join(f'        "{m}",\n' for m in members) + "    ),\n"
    )


def test_removing_a_process_drops_it_from_a_profile_written_a_member_per_line():
    """#2926: only a one-line tuple was edited, so the process stayed in a
    multi-line profile, the removal reported success and the profile could
    no longer start."""
    from uqs.scaffold.remove import _drop_from_profiles

    before = PROFILES_FILE.read_text()
    listed = _fx_one_per_line(before, "zq_ml1")
    after = _drop_from_profiles(listed, "zq_ml1")
    assert _literal(after, "PROFILES") == _literal(before, "PROFILES")
    assert after == _fx_one_per_line(before, "zq_ml1").replace('        "zq_ml1",\n', "")


def test_removing_from_a_profile_job_new_wrapped_keeps_the_shape_ruff_format_keeps():
    """#2917 wraps a profile past 100 characters a member per line; #2926's
    removal reads that shape back. Dropping the member that brings it under
    the limit leaves it wrapped (the trailing comma keeps ruff from joining
    it, and job new keeps a wrapped profile wrapped), and dropping the last
    leaves `()`, not `(\n    ),`, which ruff format would rewrite."""
    from uqs.scaffold.profile import _with_member
    from uqs.scaffold.remove import _drop_from_profiles

    base = 'PROFILES: dict[str, tuple[str, ...]] = {\n    "p": ("job_a",),\n}\n'
    long_b, long_c = "job_b_with_a_rather_long_plausible_name1", "job_c_with_a_longer_name2"
    wrapped = _with_member(_with_member(_with_member(base, "p", long_b), "p", long_c), "p", "job_d")

    def shape(*members: str) -> str:
        body = "".join(f'        "{m}",\n' for m in members)
        return f'PROFILES: dict[str, tuple[str, ...]] = {{\n    "p": (\n{body}    ),\n}}\n'

    assert wrapped == shape("job_a", long_b, long_c, "job_d"), "the shape job new writes"
    under = _drop_from_profiles(wrapped, long_c)
    assert under == shape("job_a", long_b, "job_d")
    assert _with_member(under, "p", "job_e") == shape("job_a", long_b, "job_d", "job_e")
    for proc in (long_b, "job_d", "job_a"):
        under = _drop_from_profiles(under, proc)
    assert under == 'PROFILES: dict[str, tuple[str, ...]] = {\n    "p": (),\n}\n'


def test_a_profile_layout_the_removal_cannot_edit_refuses_the_removal():
    """Rather than leave the process in a profile and report success (#2926)."""
    from uqs.scaffold.remove import _drop_from_profiles

    text = PROFILES_FILE.read_text().replace(
        '"fx": ("posbook1", "fxpositions1", "last_value1"),',
        '"fx": ("posbook1", "fxpositions1",\n           "zq_ml1", "last_value1"),',
    )
    with pytest.raises(UqsError, match=r"still lists zq_ml1 in a profile this cannot edit"):
        _drop_from_profiles(text, "zq_ml1")


def test_removing_an_exempt_process_named_like_a_profile_drops_only_its_exemption():
    from uqs.scaffold import profile
    from uqs.scaffold.remove import _drop_from_profiles

    before = UNPROFILED_FILE.read_text()
    exempt = profile._with_unprofiled(
        before, profile._unprofiled_entry("default", "a demo, started by hand")
    )
    assert "default" in _literal(exempt, "UNPROFILED")
    after = _drop_from_profiles(exempt, "default")
    assert after == before


def test_a_written_job_is_removed_only_through_the_named_override(tree, monkeypatch):
    """#1852: the bypass is `--override even-if-edited --reason`, as every other is."""
    argv = ["--override", "even-if-edited", "--reason", "rewriting it from scratch"]
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli.job import remove as remove_cli

    runner = CliRunner()
    monkeypatch.setattr(remove_cli, "_paths", lambda: type("P", (), {"repo_root": tree})())
    errors: list[str] = []

    def record(exc: Exception) -> None:
        errors.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(remove_cli, "_die", record)
    refused = runner.invoke(cli.app, ["job", "remove", "alert_sink", "--dry-run"])
    assert refused.exit_code == 1 and any("no SCAFFOLDED marker left" in e for e in errors)
    errors.clear()
    shown = runner.invoke(cli.app, ["job", "remove", "alert_sink", "--dry-run", *argv])
    assert shown.exit_code == 0 and errors == [], errors
    assert "alert_sink.q" in shown.output


def test_a_removal_not_confirmed_exits_1_and_removes_nothing(tree, monkeypatch):
    """A no to `job remove` exited 0 while every other command's no exited 1,
    and with no terminal it prompted into the void (#1885)."""
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli.common import safety
    from uqs.cli.job import remove as remove_cli

    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    monkeypatch.setattr(remove_cli, "_paths", lambda: type("P", (), {"repo_root": tree})())
    runner = CliRunner()
    unasked = runner.invoke(cli.app, ["job", "remove", "pulse"])
    assert unasked.exit_code == 1 and "Aborted" not in unasked.output
    monkeypatch.setattr(safety, "interactive", lambda: True)
    declined = runner.invoke(cli.app, ["job", "remove", "pulse"], input="n\n")
    assert declined.exit_code == safety.EXIT_DECLINED == 1
    assert (tree / "src/etl/streaming/pulse.q").is_file(), "and nothing was removed"


# --------------------------- only what the scaffold provably wrote goes (#2831-#2843)


def _with_python(tree: Path) -> Path:
    """The tree, with the directories a connector scaffold writes into."""
    from uqs.scaffold.external import EXTERNAL_DIR, PY_TEST_DIR

    for rel in (EXTERNAL_DIR, PY_TEST_DIR):
        (tree / rel).mkdir(parents=True, exist_ok=True)
    return tree


def test_a_plain_job_named_like_a_shipped_connector_leaves_its_modules(tree):
    """#2831: a subscriber called `kafka` is not the Kafka connector: the
    hand-written kafka_streamer.py and kafka_feed.py are not its to delete."""
    from uqs.scaffold.external import EXTERNAL_DIR

    _with_python(tree)
    for module in ("kafka_streamer.py", "kafka_feed.py"):
        shutil.copy2(UQF_ROOT / EXTERNAL_DIR / module, tree / EXTERNAL_DIR / module)
    write.apply_plan(jobs.streaming_job("kafka", [], "zz_k", "sym:symbol, px:float"), tree)
    removal = plan_removal(tree, "kafka")
    assert not [p for p in removal.deletes if p.suffix == ".py"], removal.deletes
    assert any("kafka_feed.py" in n for n in removal.notes), removal.notes
    removal.apply(tree)
    assert (tree / EXTERNAL_DIR / "kafka_streamer.py").is_file()
    assert (tree / EXTERNAL_DIR / "kafka_feed.py").is_file()


def _connector(tree: Path) -> None:
    from uqs.scaffold.external import external_feed

    write.apply_plan(
        external_feed(
            "zq_conn",
            "zq_conn_raw",
            "zq_conn_out",
            "sym:symbol, px:float",
            known_tables=set(),
            unprofiled="a test feed",
        ),
        _with_python(tree),
    )


@pytest.mark.parametrize(
    "written",
    [
        "python/uqs/src/uqs/external/zq_conn_streamer.py",
        "python/uqs/tests/test_zq_conn_streamer.py",
        "tests/q/test_zq_conn.q",
        "python/uqs/src/uqs/external/zq_conn_feed.py",
    ],
)
def test_a_written_file_of_a_scaffolded_job_needs_the_override(tree, written):
    """#2832: only the job file's marker was checked, so a written streamer,
    feed module or test went with the scaffold - silently."""
    _connector(tree)
    path = tree / written
    path.write_text(path.read_text().replace("SCAFFOLDED", "written") + "\n# real work\n")
    with pytest.raises(UqsError, match=re.escape(written)):
        plan_removal(tree, "zq_conn")
    assert Path(written) in plan_removal(tree, "zq_conn", force=True).deletes


def test_an_unedited_connector_still_goes_whole(tree):
    from uqs.scaffold.external import external_files

    _connector(tree)
    removal = plan_removal(tree, "zq_conn")
    assert {Path(f) for f in external_files("zq_conn")} <= set(removal.deletes)


def test_a_source_a_streaming_job_reads_is_kept(tree):
    """#2833: only other bounded workers were asked; a streaming feed reading
    the source through `.qetl.source.for_source` lost it, override or not."""
    write.apply_plan(backfill.bounded_worker("zq_deals", "zq_deals_hist", "amt:float"), tree)
    write.apply_plan(jobs.streaming_job("zq_deals_feed", [], "zq_deals_live", "amt:float"), tree)
    feed = tree / "src/etl/streaming/zq_deals_feed.q"
    feed.write_text(feed.read_text() + "src:.qetl.source.for_source `zq_deals\n")
    for force in (False, True):
        removal = plan_removal(tree, "zq_deals", force=force)
        assert Path("src/etl/sources/zq_deals.q") not in removal.deletes
        assert any("zq_deals_feed.q" in n for n in removal.notes), removal.notes


def _twin(tree: Path) -> None:
    write.apply_plan(jobs.streaming_job("zq_a", [], "zq_a_t", "sym:symbol, px:float"), tree)
    write.apply_plan(
        backfill.bounded_worker(
            "zq_a_hist", "zq_a_t", "sym:symbol, px:float", twin_of="zq_a", define_table=False
        ),
        tree,
    )


def test_a_job_with_a_twin_is_refused_naming_the_twin(tree):
    """#2834: the live job went and its twin's twin_of resolved to nothing."""
    _twin(tree)
    with pytest.raises(UqsError, match=r"zq_a_hist_backfill.*remove it first"):
        plan_removal(tree, "zq_a", force=True)


def test_a_worker_a_reaction_watches_is_refused_naming_the_reaction(tree):
    write.apply_plan(backfill.bounded_worker("zq_bf", "zq_bf", "amt:float"), tree)
    write.apply_plan(
        reaction("zq_re", "zq_bf", ["zq_re"], producers={"zq_bf": ["zq_bf1"]}, taken=set()),
        tree,
    )
    with pytest.raises(UqsError, match=r"zq_re.*remove it first"):
        plan_removal(tree, "zq_bf", force=True)


def test_a_stream_and_its_twin_removed_in_the_order_allowed_restore_the_tree(tree):
    """#2842: both jobs gone left zq_a_t defined with no owner. The live job
    is now refused while its twin stands, and the other order is exact."""
    before = _snapshot(tree)
    _twin(tree)
    _remove(tree, "zq_a_hist")
    _remove(tree, "zq_a")
    after = _snapshot(tree)
    changed = sorted(str(p) for p in set(before) | set(after) if before.get(p) != after.get(p))
    assert not changed, f"not restored: {changed}"


def test_the_last_job_on_a_scaffolded_table_takes_it_when_its_definer_is_gone(tree):
    """#2842: a guest writer removed after the job that defined the table left
    the definition with nobody to remove it."""
    before = _snapshot(tree)
    write.apply_plan(jobs.streaming_job("zq_def", [], "zq_shared", "px:float"), tree)
    write.apply_plan(
        jobs.streaming_job("zq_guest", [], "zq_shared", None, known_tables={"zq_shared"}), tree
    )
    assert any("kept table zq_shared" in n for n in plan_removal(tree, "zq_def").notes)
    _remove(tree, "zq_def")
    _remove(tree, "zq_guest")
    after = _snapshot(tree)
    changed = sorted(str(p) for p in set(before) | set(after) if before.get(p) != after.get(p))
    assert not changed, f"not restored: {changed}"


def test_the_last_reader_of_a_scaffolded_table_takes_it_when_its_definer_is_gone(tree):
    """#2924: #2842's rule asked only about the tables a job writes, so once
    the job that defined a table was gone, removing its last subscriber left
    the definition, its catalog lines and its expected entry behind."""
    files = (TABLES_FILE, CATALOG_FILE, STACK_TABLES_TEST)
    before = {rel: (tree / rel).read_text() for rel in files}
    write.apply_plan(jobs.streaming_job("zq_p", [], "zq_shared", "px:float"), tree)
    write.apply_plan(
        jobs.streaming_job("zq_q", ["zq_shared"], "zq_q_out", "n:long", known_tables={"zq_shared"}),
        tree,
    )
    # The job that defined zq_shared is gone; its definition is still unwritten.
    (tree / "src/etl/streaming/zq_p.q").unlink()
    (tree / "tests/q/test_zq_p.q").unlink()
    removal = plan_removal(tree, "zq_q")
    assert any("removed table zq_shared" in n for n in removal.notes), removal.notes
    assert removal.references == []
    removal.apply(tree)
    assert {rel: (tree / rel).read_text() for rel in files} == before


def test_a_table_the_removed_job_reads_stays_while_its_definer_does(tree):
    write.apply_plan(jobs.streaming_job("zq_p", [], "zq_shared", "px:float"), tree)
    write.apply_plan(
        jobs.streaming_job("zq_q", ["zq_shared"], "zq_q_out", "n:long", known_tables={"zq_shared"}),
        tree,
    )
    removal = plan_removal(tree, "zq_q")
    assert not any("zq_shared" in n for n in removal.notes), removal.notes
    removal.apply(tree)
    assert ".qetl.plant.zq_shared:([]" in (tree / TABLES_FILE).read_text(), "zq_p still writes it"


def test_a_stream_and_a_backfill_under_one_name_are_each_removable(tree):
    """#2840: `zq_s` matched the stream and zq_s_backfill both, so the stream
    had no name that removed it."""
    write.apply_plan(jobs.streaming_job("zq_s", [], "zq_s_t", "px:float"), tree)
    write.apply_plan(backfill.bounded_worker("zq_s", "zq_s_hist", "amt:float"), tree)
    assert Path("src/etl/streaming/zq_s.q") in plan_removal(tree, "zq_s").deletes
    assert Path("src/etl/workers/zq_s_backfill.q") in plan_removal(tree, "zq_s_backfill").deletes


def _install_bundle(tree: Path, where: Path) -> None:
    """A one-job bundle, installed into `tree` as `uqs job install` would."""
    from uqs.stack import bundles

    where.mkdir()
    (where / "bundle.json").write_text('{"name": "zqbundle", "version": "1.0.0"}')
    (where / "zq_bjob.q").write_text(
        ".qetl.job.stream.define[`zq_bjob;`procname`outputs!(`zq_bjob1;enlist `zq_btab)];\n"
    )
    (where / "tables.q").write_text("/ zqbundle's table\nzq_btab:([]time:`timestamp$();px:())\n")
    (where / "catalog.q").write_text('.qcat.describe[`zq_btab]:\n    "one zqbundle print";\n')
    bundles.install(bundles.plan(bundles.read_bundle(where), tree), tree)


def test_a_job_a_bundle_installed_is_refused_naming_the_bundle(tree, tmp_path):
    """#2923: the refusal offered --override even-if-edited, which deleted the
    file and cut its table and catalog entries out of the bundle's blocks,
    while the ledger still recorded all three."""
    from uqs.paths import BUNDLE_LEDGER

    _install_bundle(tree, tmp_path / "zqbundle")
    job = tree / "src/etl/streaming/zq_bjob.q"
    assert "zq_bjob.q" in (tree / BUNDLE_LEDGER).read_text(), "the install recorded it"
    refusal = (
        r"src/etl/streaming/zq_bjob.q was installed by bundle zqbundle .*take the job out of "
        r"the bundle's folder and install the bundle again \(`uqs job install <folder>`"
    )
    before = _snapshot(tree)
    for force in (False, True):
        with pytest.raises(UqsError, match=refusal):
            plan_removal(tree, "zq_bjob", force=force)
    assert job.is_file() and _snapshot(tree) == before


#: The files the removal regenerates, each as a scaffold leaves it naming the job.
_REGENERATED = {
    "python/uqs/src/uqs/generated/q_facts.py": '    "pulse": ("pulse",),\n',
    "python/uqf_airflow_provider/src/uqf_airflow_provider/q_facts.py": '    "pulse": ("pulse",),\n',
    "scripts/torqconfig/dataaccess/tableproperties.csv": ",pulse,time,sym,sym,,,,\n",
}


def test_strict_does_not_count_what_the_removal_regenerates(tree):
    """#2838: q_facts.py and tableproperties.csv name every scaffolded table,
    so --strict refused every job with one."""
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    for rel, line in _REGENERATED.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(line)
    assert plan_removal(tree, "pulse").references == []


def test_strict_does_not_count_the_generated_profile_table(tree):
    """#2920: the runtimes guide's profile table is a block the removal's own
    operational-docs run rewrites, and it names every profile's processes - so
    --strict refused every job in a profile. The prose around it still counts."""
    guide = Path("docs/guides/runtimes-and-profiles.md")
    write.apply_plan(
        jobs.streaming_job(
            "pulse", [], "pulse", "px:float", profile="depth", known_profiles=("depth",)
        ),
        tree,
    )
    (tree / guide).parent.mkdir(parents=True, exist_ok=True)
    end = "<!-- END GENERATED profile table -->"
    text = (UQF_ROOT / guide).read_text()
    assert text.count(end) == 1, "the block operational_docs.py owns in the real guide"
    # A row as operational-docs writes it once pulse1 is in the depth profile.
    listed = text.replace(
        end, f"  | `depth` | `vectorize1`, `cross1`, `pulse1` | 19 | 9/14 |\n\n{end}"
    )
    (tree / guide).write_text(listed)
    assert plan_removal(tree, "pulse").references == []
    (tree / guide).write_text(listed + "\nStart `pulse1` first.\n")
    refs = plan_removal(tree, "pulse").references
    assert [(r.path, r.text) for r in refs] == [(guide, "Start `pulse1` first.")]


def test_a_removal_that_breaks_regeneration_exits_nonzero(tree, monkeypatch):
    """#2843: `could not regenerate` was printed and the exit code stayed 0."""
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli.job import remove as remove_cli

    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    monkeypatch.setattr(remove_cli, "_paths", lambda: type("P", (), {"repo_root": tree})())
    monkeypatch.setattr(remove_cli, "report_regeneration", lambda root: False)
    result = CliRunner().invoke(cli.app, ["job", "remove", "pulse", "--yes"])
    assert result.exit_code == 1, result.output
    assert "removed pulse" in result.output
    assert not (tree / "src/etl/streaming/pulse.q").exists()
