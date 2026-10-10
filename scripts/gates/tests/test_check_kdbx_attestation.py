"""Tests for `scripts/gates/check_kdbx_attestation.py` and
`scripts/dev/attest_kdbx.py` (#1014), against a throwaway git repository."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("check_kdbx_attestation", "scripts/gates/check_kdbx_attestation.py")
attest = _load("attest_kdbx", "scripts/dev/attest_kdbx.py")


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(repo: Path, path: str, text: str, msg: str = "change") -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    sh(repo, "add", path)
    sh(repo, "commit", "-q", "-m", msg)
    return sh(repo, "rev-parse", "HEAD")


def attest_here(repo: Path) -> str:
    tree = sh(repo, "rev-parse", "HEAD^{tree}")
    sh(repo, "commit", "-q", "--allow-empty", "-m", "attest", "-m", f"KDB-X-gates: {tree}")
    return sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    sh(tmp_path, "init", "-q", "-b", "master")
    sh(tmp_path, "config", "user.email", "t@example.com")
    sh(tmp_path, "config", "user.name", "t")
    commit(tmp_path, "README.md", "x\n", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_change_the_gates_do_not_judge_needs_nothing(repo):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "python/uqs/x.py", "y = 1\n")
    ok, why = check.report(base, head)
    assert ok and "no attestation needed" in why


def test_a_q_change_without_an_attestation_is_reported(repo):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "src/a.q", "a:1\n")
    ok, why = check.report(base, head)
    assert not ok
    assert "src/a.q" in why and "attest_kdbx.py" in why


def test_an_attestation_of_exactly_this_tree_passes(repo):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    assert check.report(base, head) == (True, "the KDB-X gates passed on exactly this tree")


def test_a_change_after_the_attestation_needs_a_new_one(repo):
    """The case that broke master: what merges is not what was run."""
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    attest_here(repo)
    head = commit(repo, "src/a.q", "a:2\n")
    assert not check.report(base, head)[0]


def test_an_attested_head_behind_its_base_is_reported(repo):
    fork = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "-b", "pr")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    sh(repo, "checkout", "-q", "master")
    base = commit(repo, "src/b.q", "b:1\n")
    ok, why = check.report(base, head)
    assert not ok and "does not contain the base's tip" in why
    assert check.report(fork, head)[0]


def test_attest_refuses_a_dirty_tree(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    (repo / "README.md").write_text("changed\n")
    assert "uncommitted" in attest.refusal()


def test_attest_refuses_an_interpreter_that_is_not_kdbx(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    fake = type("T", (), {"check_interpreter": staticmethod(lambda: "peachq")})
    monkeypatch.setattr(attest, "_test_py", lambda: fake)
    assert "not KDB-X" in attest.refusal()


def test_attest_records_the_tree_only_when_every_hook_passes(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    monkeypatch.setattr(attest, "refusal", lambda: None)
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    monkeypatch.setattr(attest, "run_hook", lambda hook: hook != "q-coverage")
    assert attest.main() == 1
    assert not check.report(base, sh(repo, "rev-parse", "HEAD"))[0]
    monkeypatch.setattr(attest, "run_hook", lambda hook: True)
    assert attest.main() == 0
    assert check.report(base, sh(repo, "rev-parse", "HEAD"))[0]
