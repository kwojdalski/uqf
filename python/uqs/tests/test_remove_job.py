"""`uqs job remove`: undoing a scaffold, worked out from the tree.

The property that matters is the round trip: scaffold into a copy of the real
tree, remove, and every file is BYTE-identical to before. A removal that left
a blank line in uqs_tables.q or a stray backtick in nsList would pass a test
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
        "rebuild_positions",
        "demo_deals",
        ["positions"],
        producers={"demo_deals": ["deals_backfill1"]},
        taken=set(),
    )
    write.apply_plan(plan, tree)
    assert _snapshot(tree) != before
    removal = plan_removal(tree, "rebuild_positions")
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
        plan_removal(tree, "markout")
    assert Path("src/etl/streaming/markout.q") in plan_removal(tree, "markout", force=True).deletes


def test_a_name_that_matches_no_job_is_refused(tree):
    with pytest.raises(UqsError, match="0 job files match 'nope'"):
        plan_removal(tree, "nope")


def test_nothing_is_touched_until_apply(tree):
    before = _snapshot(tree)
    write.apply_plan(jobs.streaming_job("pulse", [], "pulse", "px:float"), tree)
    scaffolded = _snapshot(tree)
    plan_removal(tree, "pulse")
    assert _snapshot(tree) == scaffolded != before
