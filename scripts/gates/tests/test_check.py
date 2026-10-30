"""Tests for `scripts/gates/check.py`, the one command for the policy checks (#1806).

Each rule has its own tests beside this file (test_check_<rule>.py), which
hold what it finds. These hold the command: that it runs only the check it is
given, refuses one it does not know, reports a check that crashes as a
failure rather than as a clean tree, and changes nothing it reads.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CHECK = REPO / "scripts" / "gates" / "check.py"

_SPEC = importlib.util.spec_from_file_location("gates_check", CHECK)
assert _SPEC and _SPEC.loader
check = importlib.util.module_from_spec(_SPEC)
sys.modules["gates_check"] = check
_SPEC.loader.exec_module(check)

NAMES = sorted(check.CHECKS)


def _main(capsys, *argv: str) -> tuple[object, str, str]:
    try:
        rc = check.main(list(argv))
    except SystemExit as exc:
        rc = exc.code
    out, err = capsys.readouterr()
    return rc, out, err


# ------------------------------------------------------------- registration


def test_every_check_names_a_rule_module_that_exists():
    for c in check.CHECKS.values():
        assert (check.RULES / f"{c.rule}.py").is_file(), c


def test_the_checks_are_the_ones_registered_by_hand():
    """No discovery: a rule module nobody registered is not a check. Every
    module under policy/ is some check's rule, so none is left orphaned."""
    rules = {c.rule for c in check.CHECKS.values()}
    on_disk = {p.stem for p in check.RULES.glob("*.py") if p.stem != "__init__"}
    assert on_disk == rules


@pytest.mark.parametrize("name", NAMES)
def test_this_checkout_passes_each_check(capsys, name):
    rc, out, err = _main(capsys, name)
    assert rc == 0, err
    assert out.startswith(f"{name}: "), out


# -------------------------------------------------------------------- usage


def test_an_unknown_check_is_a_usage_error_naming_the_known_ones(capsys):
    rc, out, err = _main(capsys, "no-such-check")
    assert rc == 2
    assert "invalid choice: 'no-such-check'" in err
    assert all(name in err for name in NAMES)
    assert out == ""


def test_no_check_at_all_is_a_usage_error(capsys):
    rc, _, err = _main(capsys)
    assert rc == 2
    assert "CHECK" in err


@pytest.mark.parametrize("name", NAMES)
def test_a_root_that_is_not_a_checkout_is_refused_not_read_as_empty(capsys, tmp_path, name):
    """A root with no src/ would read as a tree with nothing in it to break a
    rule - nested-contexts, for one, would pass it."""
    rc, out, err = _main(capsys, name, "--root", str(tmp_path))
    assert rc == 2
    assert "not a checkout of this repository" in err
    assert out == ""


# ------------------------------------------------------- one check, alone


@pytest.mark.parametrize("name", NAMES)
def test_running_one_check_imports_no_other_check_s_rule(name):
    """Run in a fresh interpreter, so no other test's imports count."""
    probe = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('gates_check', {str(CHECK)!r})\n"
        "check = importlib.util.module_from_spec(spec); sys.modules['gates_check'] = check\n"
        "spec.loader.exec_module(check)\n"
        "before = sorted(c.rule for c in check.CHECKS.values() if c.rule in sys.modules)\n"
        f"rc = check.main([{name!r}])\n"
        "after = sorted(c.rule for c in check.CHECKS.values() if c.rule in sys.modules)\n"
        "print(json.dumps([rc, before, after]))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    rc, before, after = json.loads(out.splitlines()[-1])
    assert rc == 0
    assert before == [], "loading the command imported a rule"
    assert after == [check.CHECKS[name].rule]


# -------------------------------------------------------------- failures


def _crashing(exc: BaseException):
    def run(_rule, _root):
        raise exc

    return run


@pytest.mark.parametrize(
    ("exc", "said"),
    [
        (FileNotFoundError("docs/x.md"), "FileNotFoundError: docs/x.md"),
        (SystemExit("required_keys not found"), "required_keys not found"),
    ],
)
def test_a_check_that_crashes_fails_and_says_so(capsys, monkeypatch, exc, said):
    """An error is a failure with a message - never zero findings read as a
    clean tree."""
    name = NAMES[0]
    broken = check.Check(name, "", check.CHECKS[name].rule, _crashing(exc))
    monkeypatch.setitem(check.CHECKS, name, broken)
    rc, out, err = _main(capsys, name)
    assert rc == 1
    assert f"error: {name} could not run: " in err and said in err
    assert out == ""


def test_a_check_that_returns_no_status_fails(capsys, monkeypatch):
    name = NAMES[0]
    broken = check.Check(name, "", check.CHECKS[name].rule, lambda _rule, _root: None)
    monkeypatch.setitem(check.CHECKS, name, broken)
    rc, _, err = _main(capsys, name)
    assert rc == 1
    assert "not an exit status" in err


def test_a_file_the_rule_cannot_read_fails_the_run(capsys, tmp_path):
    """A real error, not a stub: q-module-size meets a `.q` it cannot read."""
    (tmp_path / "src" / "broken.q").mkdir(parents=True)
    rc, out, err = _main(capsys, "q-module-size", "--root", str(tmp_path))
    assert rc == 1
    assert "error: q-module-size could not run: IsADirectoryError" in err
    assert out == ""


def test_findings_fail_with_each_one_on_stderr(capsys, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "big.q").write_text("x:1\n" * 801)
    rc, out, err = _main(capsys, "q-module-size", "--root", str(tmp_path))
    assert rc == 1
    assert "src/big.q: 801 lines is over 800" in err
    assert out == ""


# ----------------------------------------------------------- read-only


#: Enough of this checkout for every check to run to the end over a copy.
_FIXTURE = (
    "src",
    "docs/reference/environment.md",
    "docs/reference/pipeline-declarations.md",
    ".env.example",
    "python/uqs/src/uqs/stack/env.py",
)


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        p.relative_to(root).as_posix(): (
            p.stat().st_mode,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "",
        )
        for p in sorted(root.rglob("*"))
    }


@pytest.mark.parametrize("name", NAMES)
def test_a_check_changes_nothing_in_the_tree_it_reads(capsys, tmp_path, name):
    """Over a copy made read-only: a write would fail the run with an error,
    and the snapshot proves none happened by any other route either."""
    root = tmp_path / "tree"
    for rel in _FIXTURE:
        src, dst = REPO / rel, root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.is_file():
            shutil.copy2(src, dst)
    writable = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    paths = [root, *root.rglob("*")]
    for p in paths:
        os.chmod(p, p.stat().st_mode & ~writable)
    try:
        before = _snapshot(root)
        rc, _, err = _main(capsys, name, "--root", str(root))
        after = _snapshot(root)
    finally:
        for p in paths:
            os.chmod(p, p.stat().st_mode | stat.S_IWUSR)
    assert "could not run" not in err, err
    assert rc in (0, 1)
    assert after == before
