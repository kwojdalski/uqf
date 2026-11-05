"""The vendored rule, `scripts/gates/policy/vendored.py`, and the one way to
change a vendored tree, `scripts/dev/revendor.py` (#2157)."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vendored = _load("vendored", "scripts/gates/policy/vendored.py")
revendor = _load("revendor", "scripts/dev/revendor.py")
check = _load("gates_check", "scripts/gates/check.py")


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init(path: Path) -> None:
    git("init", "-q", cwd=path)
    git("config", "user.email", "t@example.com", cwd=path)
    git("config", "user.name", "t", cwd=path)
    git("config", "commit.gpgsign", "false", cwd=path)


# --------------------------------------------------------------- real tree


def test_the_real_tree_matches_its_record():
    assert vendored.problems(REPO) == []


def test_every_directory_under_lib_is_recorded_with_a_licence():
    trees = vendored.load_record(REPO)
    assert {t.name for t in trees} == {
        p.name for p in (REPO / "lib").iterdir() if p.is_dir() and p.name != ".vendored"
    }
    for t in trees:
        assert len(t.commit) == 40, t.name
        assert t.upstream.startswith("https://"), t.name
        assert t.license and (REPO / t.path / t.license_file).is_file(), t.name


def _copy_tree(tmp_path: Path, name: str):
    """lib/<name>, the record and its listings, copied into a fresh repository."""
    root = tmp_path / "copy"
    (root / "lib").mkdir(parents=True)
    shutil.copytree(REPO / "lib" / name, root / "lib" / name)
    shutil.copytree(REPO / vendored.LISTINGS, root / vendored.LISTINGS)
    shutil.copy(REPO / vendored.RECORD, root / vendored.RECORD)
    _init(root)
    (tree,) = [t for t in vendored.load_record(root) if t.name == name]
    return root, tree


def test_an_edit_to_a_real_vendored_file_is_named(tmp_path):
    root, tree = _copy_tree(tmp_path, "kx-dbmaint")
    assert vendored.tree_problems(root, tree) == []
    with (root / tree.path / "dbmaint.q").open("a") as f:
        f.write("/ a local fix\n")
    (found,) = vendored.tree_problems(root, tree)
    assert found.startswith(
        "lib/kx-dbmaint/dbmaint.q: differs from upstream (expected blob d00d97cf"
    )


def test_recorded_drift_is_frozen_not_allowed_to_grow(tmp_path):
    root, tree = _copy_tree(tmp_path, "torq-finance-starter-pack")
    assert vendored.tree_problems(root, tree) == []
    drifted = root / tree.path / "mkdocs.yml"
    drifted.write_bytes(drifted.read_bytes() + b"# more\n")
    (found,) = vendored.tree_problems(root, tree)
    assert found.startswith(f"{tree.path}/mkdocs.yml: differs from its recorded drift")


# ----------------------------------------------------- a fixture upstream


@pytest.fixture
def upstream(tmp_path):
    up = tmp_path / "upstream"
    (up / "code").mkdir(parents=True)
    _init(up)
    (up / "LICENSE").write_text("MIT\n")
    (up / "code" / "a.q").write_text("a:1  \r\n")  # bytes this repository's hooks would rewrite
    (up / "code" / "run.sh").write_text("#!/bin/sh\n")
    (up / "code" / "run.sh").chmod(0o755)
    (up / "big.bin").write_bytes(bytes(range(256)))
    git("add", "-A", cwd=up)
    git("commit", "-q", "-m", "one", cwd=up)
    first = git("rev-parse", "HEAD", cwd=up)
    (up / "code" / "b.q").write_text("b:2\n")
    git("add", "-A", cwd=up)
    git("commit", "-q", "-m", "two", cwd=up)
    return up, first, git("rev-parse", "HEAD", cwd=up)


RECORD = """\
# test record
[tree.thing]
upstream = "{url}"
commit = "{commit}"
license = "MIT"
license_file = "LICENSE-thing"
renames = {{ "LICENSE" = "LICENSE-thing" }}
omit = ["big.bin"]
local = ["NOTICE.md"]
upstream_tree = ""
files = 0
digest = ""
"""


@pytest.fixture
def project(tmp_path, upstream):
    up, first, _ = upstream
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "lib" / "thing").mkdir(parents=True)
    (root / "lib" / "thing" / "NOTICE.md").write_text("ours\n")
    (root / "lib" / "VENDORED.toml").write_text(RECORD.format(url=up.as_uri(), commit=first))
    _init(root)
    assert revendor.main(["thing", "--root", str(root)]) == 0
    return root


def _thing(root: Path):
    (tree,) = vendored.load_record(root)
    return tree


def test_revendor_writes_upstream_bytes_and_a_record_the_gate_accepts(project, upstream):
    up, first, _ = upstream
    tree = _thing(project)
    assert tree.commit == first and tree.files == 3
    assert tree.upstream_tree == git("rev-parse", f"{first}^{{tree}}", cwd=up)
    lib = project / "lib" / "thing"
    assert (lib / "code" / "a.q").read_bytes() == b"a:1  \r\n"
    assert (lib / "LICENSE-thing").read_text() == "MIT\n"
    assert not (lib / "big.bin").exists()
    assert (lib / "NOTICE.md").read_text() == "ours\n"
    assert vendored.problems(project) == []
    assert check.main(["vendored", "--root", str(project)]) == 0


def test_the_gate_names_each_kind_of_difference(project):
    lib = project / "lib" / "thing"
    (lib / "code" / "a.q").write_bytes(b"a:1\n")  # what a whitespace hook would do
    (lib / "code" / "run.sh").chmod(0o644)
    (lib / "LICENSE-thing").unlink()
    (lib / "extra.q").write_text("x\n")
    (project / "lib" / "other").mkdir()
    (project / "lib" / "other" / "f.q").write_text("f\n")
    found = vendored.problems(project)
    assert (
        "lib/other: not recorded in lib/VENDORED.toml - vendor it with scripts/dev/revendor.py"
        in found
    )
    assert any(f.startswith("lib/thing/code/a.q: differs from upstream") for f in found)
    assert "lib/thing/code/run.sh: mode 100644, upstream 100755" in found
    assert any(f.startswith("lib/thing/LICENSE-thing: missing") for f in found)
    assert any(f.startswith("lib/thing/extra.q: not in upstream") for f in found)
    assert not any(f.startswith("lib/thing: licence file") for f in found)
    assert check.main(["vendored", "--root", str(project)]) == 1


def test_a_missing_local_file_and_an_ignored_file(project):
    lib = project / "lib" / "thing"
    (project / ".gitignore").write_text("/lib/thing/operator.csv\n")
    (lib / "operator.csv").write_text("an operator's own config\n")
    assert vendored.problems(project) == []
    (lib / "NOTICE.md").unlink()
    assert vendored.problems(project) == [
        "lib/thing/NOTICE.md: listed as `local` in lib/VENDORED.toml but missing"
    ]


def test_a_listing_edited_without_its_record_is_refused(project):
    listing = project / _thing(project).listing_file
    listing.write_text(listing.read_text().replace("100755", "100644"))
    (found,) = vendored.problems(project)
    assert "does not match lib/VENDORED.toml's sha256:" in found


def test_revendor_round_trips_an_edited_tree_back_to_upstream(project):
    lib = project / "lib" / "thing"
    before = {p: p.read_bytes() for p in lib.rglob("*") if p.is_file()}
    record_before = (project / vendored.RECORD).read_text()
    (lib / "code" / "a.q").write_bytes(b"a:1\n")
    (lib / "stray.q").write_text("s\n")
    assert vendored.problems(project)
    assert revendor.main(["thing", "--root", str(project)]) == 0
    assert vendored.problems(project) == []
    assert {p: p.read_bytes() for p in lib.rglob("*") if p.is_file()} == before
    assert (project / vendored.RECORD).read_text() == record_before


def test_revendor_deletes_recorded_drift(project):
    tree = _thing(project)
    lib = project / "lib" / "thing"
    (lib / "code" / "a.q").write_bytes(b"a:1\n")
    up_blob = vendored.parse_listing((project / tree.listing_file).read_text())["code/a.q"][1]
    (project / tree.drift_file).write_text(
        f"# frozen\ncode/a.q\t{up_blob}\t{vendored.blob_id(b'a:1' + bytes([10]))}\twhitespace\n"
    )
    assert vendored.problems(project) == []
    assert vendored.summary(project).endswith("(1 files of recorded drift)")
    assert revendor.main(["thing", "--root", str(project)]) == 0
    assert not (project / tree.drift_file).exists()
    assert (lib / "code" / "a.q").read_bytes() == b"a:1  \r\n"


def test_revendor_moves_to_another_commit_and_verifies(project, upstream, capsys):
    up, _, second = upstream
    assert revendor.main(["thing", "--root", str(project), "--commit", second[:10]]) == 0
    tree = _thing(project)
    assert tree.commit == second and tree.files == 4
    assert (project / "lib" / "thing" / "code" / "b.q").read_text() == "b:2\n"
    assert vendored.problems(project) == []
    assert revendor.main(["thing", "--root", str(project), "--verify"]) == 0
    listing = project / tree.listing_file
    listing.write_text(listing.read_text().replace("100755", "100644"))
    assert revendor.main(["thing", "--root", str(project), "--verify"]) == 1
    assert "DOES NOT match" in capsys.readouterr().out
