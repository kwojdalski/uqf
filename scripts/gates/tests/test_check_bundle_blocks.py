"""An installed bundle's blocks never become tree content (#881).

Both halves, each seen to fire: the commit gate refuses a block in what
would be committed, and the release build refuses a block from outside its
composition. The gate runs in a throwaway repository, with git's hook
variables cleared - a commit hook exports GIT_DIR, which would otherwise
point every `git` here at the real checkout.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from uqs.deploy.artifact import ReleaseError
from uqs.deploy.foreign_blocks import refuse as refuse_foreign_blocks
from uqs.stack.bundle_blocks import block_names, with_block

GATE = Path(__file__).resolve().parents[1] / "check_bundle_blocks.py"
_spec = importlib.util.spec_from_file_location("check_bundle_blocks", GATE)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

TABLES = "src/etl/plant_tables.q"


@pytest.fixture(autouse=True)
def _no_enclosing_repository(monkeypatch):
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)


def _repo(tmp_path: Path, monkeypatch) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "src" / "etl").mkdir(parents=True)
    (tmp_path / TABLES).write_text("trade:([] sym:`symbol$())\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    monkeypatch.setattr(gate, "REPO", tmp_path)
    return tmp_path


def _install(root: Path, name: str) -> None:
    path = root / TABLES
    path.write_text(with_block(path.read_text(), name, ["pbt:([] x:`long$())"], TABLES))


def test_a_clean_index_passes(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    assert gate.main() == 0


def test_a_block_only_in_the_working_tree_passes(tmp_path, monkeypatch):
    """Prepare is allowed to write it there: only committing it is refused."""
    root = _repo(tmp_path, monkeypatch)
    _install(root, "pb")
    assert gate.main() == 0


def test_a_staged_block_is_refused_naming_its_file(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, monkeypatch)
    _install(root, "pb")
    subprocess.run(["git", "-C", str(root), "add", TABLES], check=True)
    assert gate.main() == 1
    out = capsys.readouterr().out
    assert f"{TABLES}:" in out and "/ BEGIN bundle pb" in out


def test_block_names_reads_each_opening_marker():
    text = with_block(with_block("x:1\n", "a", ["y:2"], "t"), "b", ["z:3"], "t")
    assert block_names(text) == ["a", "b"]
    assert block_names("x:1\n") == []


def test_the_build_refuses_a_block_from_outside_its_composition(tmp_path):
    (tmp_path / "src" / "etl").mkdir(parents=True)
    (tmp_path / TABLES).write_text("trade:([] sym:`symbol$())\n")
    _install(tmp_path, "marketwarehouse")
    with pytest.raises(ReleaseError, match=f"{TABLES}: bundle marketwarehouse"):
        refuse_foreign_blocks(tmp_path, [TABLES], "uqf", set())


def test_the_build_allows_its_own_composition_s_block(tmp_path):
    (tmp_path / "src" / "etl").mkdir(parents=True)
    (tmp_path / TABLES).write_text("trade:([] sym:`symbol$())\n")
    _install(tmp_path, "pb")
    refuse_foreign_blocks(tmp_path, [TABLES], "crypto", {"pb"})
