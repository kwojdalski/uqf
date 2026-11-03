"""The generated-file merge driver (#1965): a two-branch conflict on
load_plan.q merges without stopping once the driver is registered, and stops
as before without it."""

from __future__ import annotations

import fnmatch
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "dev" / "merge_generated.py"
PLAN = "src/etl/generated/load_plan.q"

_spec = importlib.util.spec_from_file_location("merge_generated", SCRIPT)
assert _spec and _spec.loader
mg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mg)


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


@pytest.fixture
def conflict(tmp_path: Path) -> Path:
    """A repository where branches `a` and `b` change the same line of
    load_plan.q, `a` checked out."""
    repo = tmp_path / "clone"
    (repo / "scripts" / "dev").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts" / "dev" / SCRIPT.name)
    shutil.copy2(REPO / ".gitattributes", repo / ".gitattributes")
    (repo / PLAN).parent.mkdir(parents=True)
    (repo / PLAN).write_text("/ generated\n.plan:`base\n")
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    for side in ("a", "b"):
        git(repo, "checkout", "-q", "-b", side, "master")
        (repo / PLAN).write_text(f"/ generated\n.plan:`{side}\n")
        git(repo, "commit", "-q", "-am", side)
    git(repo, "checkout", "-q", "a")
    return repo


def test_without_the_driver_the_merge_stops_as_before(conflict: Path) -> None:
    r = git(conflict, "merge", "-q", "--no-edit", "b", check=False)
    assert r.returncode != 0, "control: an unregistered driver is git's text merge"
    assert "<<<<<<<" in (conflict / PLAN).read_text()


def test_with_the_driver_the_merge_completes_and_records_the_path(conflict: Path) -> None:
    assert mg.install(cwd=conflict) == 0
    r = git(conflict, "merge", "-q", "--no-edit", "b", check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (conflict / PLAN).read_text() == "/ generated\n.plan:`a\n", "this side is kept"
    assert git(conflict, "status", "--porcelain").stdout == "", "the merge is committed"
    assert mg.record_file(conflict).read_text() == f"{PLAN}\n"


def test_a_rebase_does_not_stop_either(conflict: Path) -> None:
    mg.install(cwd=conflict)
    r = git(conflict, "rebase", "-q", "b", check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert PLAN in mg.record_file(conflict).read_text()


def test_regenerate_names_the_target_and_clears_the_record(conflict: Path, capsys) -> None:
    mg.install(cwd=conflict)
    git(conflict, "merge", "-q", "--no-edit", "b")
    assert mg.regenerate(dry_run=True, cwd=conflict) == 0
    assert "derive.py operational-docs" in capsys.readouterr().out
    assert mg.record_file(conflict).exists(), "a dry run keeps the record"


def test_with_nothing_recorded_regenerate_does_nothing(conflict: Path, capsys) -> None:
    assert mg.regenerate(cwd=conflict) == 0
    assert capsys.readouterr().out == ""


def test_every_attributed_path_has_a_target_and_exists() -> None:
    """.gitattributes names only files derive.py writes, each matching a
    tracked file - a pattern that matches nothing is a typo."""
    tracked = git(REPO, "ls-files").stdout.splitlines()
    for line in (REPO / ".gitattributes").read_text().splitlines():
        if not line.strip() or line.startswith("#") or f"merge={mg.NAME}" not in line:
            continue
        pattern = line.split()[0]
        matched = [p for p in tracked if fnmatch.fnmatch(p, pattern)]
        assert matched, f"{pattern} matches no tracked file"
        for path in matched:
            assert mg.target_of(path), f"{path} has no derive.py target in TARGETS"
