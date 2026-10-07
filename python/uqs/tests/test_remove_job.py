"""`uqs job remove`: undoing a scaffold, worked out from the tree.

The property that matters is the round trip: scaffold into a copy of the real
tree, remove, and every file is BYTE-identical to before. A removal that left
a blank line in plant_tables.q or a stray backtick in nsList would pass a test
that only checked the job file was gone.

The other half is what it must not take: a table another job still reads, a
source another worker still uses, and a job that has been written.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from uqs.paths import CATALOG_FILE, RUN_TESTS_FILE, STACK_TABLES_TEST, TABLES_FILE, UqsError
from uqs.scaffold import jobs, write
from uqs.scaffold import worker as backfill
from uqs.scaffold.profile import PROFILES_FILE
from uqs.scaffold.reaction import reaction
from uqs.scaffold.remove import plan_removal

UQF_ROOT = Path(__file__).resolve().parents[3]
#: Every file a scaffold appends to or a removal edits.
_TRACKED = (TABLES_FILE, CATALOG_FILE, STACK_TABLES_TEST, RUN_TESTS_FILE, PROFILES_FILE)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of what scaffolding and removal touch: src/, the plant's q files,
    the two test lists and profiles.py."""
    shutil.copytree(UQF_ROOT / "src", tmp_path / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", tmp_path / "scripts" / "processes")
    for rel in (RUN_TESTS_FILE, STACK_TABLES_TEST, PROFILES_FILE):
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
    """Its file, its test and the nsList entry - and nothing else is touched,
    since a reaction has no table, profile or port."""
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
    assert set(removal.rewrites) == {RUN_TESTS_FILE}
    removal.apply(tree)
    assert _snapshot(tree) == before


def test_a_table_something_else_reads_is_kept(tree):
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    write.apply_plan(
        jobs.streaming_job("pulsestat", ["pulse"], "pulse_stats", "n:long", known_tables={"pulse"}),
        tree,
    )
    removal = plan_removal(tree, "pulse")
    assert TABLES_FILE not in removal.rewrites, "pulsestat still subscribes to pulse"
    assert any("kept table pulse" in n for n in removal.notes)


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
    """markout carries no SCAFFOLDED marker: it is real work, not a scaffold."""
    with pytest.raises(UqsError, match="no SCAFFOLDED marker left"):
        plan_removal(tree, "demo_markout")
    assert (
        Path("src/etl/streaming/demo_markout.q")
        in plan_removal(tree, "demo_markout", force=True).deletes
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
    which --force would otherwise have left stale."""
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
    """A table another job reads is kept, so its other mentions are why it
    stays - not something left behind."""
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    write.apply_plan(
        jobs.streaming_job(
            "pulse_reader", ["pulse"], "pulse_stats", "n:long", known_tables={"pulse"}
        ),
        tree,
    )
    removal = plan_removal(tree, "pulse")
    assert "src/etl/streaming/pulse_reader.q" not in _referenced(removal)


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
    from uqs.cli import remove as remove_cli

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
