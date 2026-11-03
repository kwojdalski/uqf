"""scripts/dev/python_tests_hook.py: which Python tests a commit runs (#1879).

Never the whole suite by default: testmon for staged Python, the reach record
for everything else, the union of the two - and the whole suite only when
that cannot be decided, or full mode (UQF_HOOKS_FULL=1) asks for it.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "python_tests_hook", Path(__file__).resolve().parents[1] / "python_tests_hook.py"
)
assert _SPEC and _SPEC.loader
hook = importlib.util.module_from_spec(_SPEC)
sys.modules["python_tests_hook"] = hook
_SPEC.loader.exec_module(hook)

KNOWN = {"python/uqs/src/uqs/paths.py", "python/uqs/tests/test_paths.py"}

#: What three tests did: one read a document, one started q, one ran a gate.
RECORD = {
    "version": 1,
    "owners": {
        "t.py::reads": {"reads": ["docs/guides/x.md"]},
        "fixture:t.py::q": {"starts": ["q"]},
        "t.py::gate": {"starts": ["python"], "scripts": ["scripts/gates/check_x.py"]},
        "t.py::lists": {"lists": ["src/etl"]},
    },
    "tests": {
        "t.py::reads": ["t.py::reads", "module:t.py"],
        "t.py::starts_q": ["t.py::starts_q", "module:t.py", "fixture:t.py::q"],
        "t.py::gate": ["t.py::gate", "module:t.py"],
        "t.py::lists": ["t.py::lists", "module:t.py"],
        "t.py::pure": ["t.py::pure", "module:t.py"],
    },
}


def _no_q_readers(paths):
    return {p: [] for p in paths}


def decide(changes, *, full_mode=False, known=KNOWN, record=RECORD, q_readers=_no_q_readers):
    return hook.decide(
        changes, full_mode=full_mode, known=known, record=record, q_readers=q_readers
    )


def test_python_testmon_has_seen_runs_testmon_alone():
    d = decide([("M", "python/uqs/src/uqs/paths.py")])
    assert (d.mode, d.full, d.testmon, d.tests) == ("picked", False, True, {})


def test_a_new_test_module_is_left_to_testmon():
    """testmon runs a test it has no record of, so a new one needs no full run."""
    d = decide([("A", "python/uqs/tests/test_brand_new.py")])
    assert d.testmon and not d.full and not d.tests


def test_a_document_runs_only_the_tests_that_read_it_or_start_a_python_child():
    d = decide([("M", "docs/guides/x.md")])
    assert not d.full and not d.testmon
    assert set(d.tests) == {"t.py::reads", "t.py::gate"}
    assert "reads docs/guides/x.md" in d.tests["t.py::reads"]


def test_a_q_file_runs_the_tests_starting_a_q_through_a_fixture():
    d = decide([("M", "src/etl/core/status.q")])
    assert set(d.tests) == {"t.py::starts_q", "t.py::gate"}
    assert "starts a q" in d.tests["t.py::starts_q"]


def test_a_data_file_q_names_reaches_the_q_starters_and_one_it_does_not_does_not():
    named = decide(
        [("M", "scripts/torqconfig/x.csv")],
        q_readers=lambda ps: {p: ["src/x.q"] for p in ps},
    )
    assert "t.py::starts_q" in named.tests
    assert "t.py::starts_q" not in decide([("M", "scripts/torqconfig/x.csv")]).tests


def test_a_file_added_under_a_listed_directory_reaches_the_lister_and_a_change_does_not():
    assert "t.py::lists" in decide([("A", "src/etl/new.txt")]).tests
    assert "t.py::lists" not in decide([("M", "src/etl/old.txt")]).tests


def test_a_python_file_testmon_has_not_seen_runs_the_python_child_starters():
    d = decide([("M", "scripts/gates/check_x.py")])
    assert d.testmon
    assert set(d.tests) == {"t.py::gate"}


def test_python_and_other_files_run_the_union():
    d = decide([("M", "python/uqs/src/uqs/paths.py"), ("M", "docs/guides/x.md")])
    assert d.testmon and set(d.tests) == {"t.py::reads", "t.py::gate"}


def test_a_change_no_test_reaches_runs_nothing():
    record = {**RECORD, "tests": {"t.py::reads": ["t.py::reads"]}}
    d = decide([("M", "docs/guides/other.md")], record=record)
    assert d.mode == "none" and not d.full


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ([("M", "python/conftest.py")], "conftest"),
        ([("M", "pyproject.toml")], "pyproject.toml"),
        ([("M", "uv.lock")], "uv.lock"),
        ([("M", "scripts/dev/test_reach.py")], "recorder"),
        ([], "nothing staged"),
    ],
)
def test_what_shapes_every_test_runs_the_whole_suite(changes, why):
    d = decide(changes)
    assert d.full and d.mode == "full"
    assert why in d.why


def test_without_records_the_whole_suite_runs_and_says_which():
    assert "testmon record" in decide([("M", "python/uqs/src/uqs/paths.py")], known=None).why
    assert decide([("M", "docs/x.md")], record=None).full
    assert ".testreach.json" in decide([("M", "docs/x.md")], record=None).why


def test_full_mode_runs_the_whole_suite_whatever_is_staged():
    """UQF_HOOKS_FULL=1: the switch back to the old behaviour."""
    d = decide([("M", "python/uqs/src/uqs/paths.py")], full_mode=True)
    assert d.full and "UQF_HOOKS_FULL=1" in d.why


def test_full_mode_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(hook.hooks_full.ENV, "1")
    assert hook.hooks_full.full()
    monkeypatch.setenv(hook.hooks_full.ENV, "0")
    assert not hook.hooks_full.full()


def test_the_whole_suite_records_into_both_records_and_a_pick_runs_only_its_tests(tmp_path):
    ids = tmp_path / "ids.txt"
    full = hook.commands(hook.Decision("x", full=True), ids, tmp_path / "r.json")
    assert len(full) == 1 and "--testmon" in full[0] and "--record-reach" in full[0]
    assert "--testmon-forceselect" not in full[0] and "--select-tests" not in full[0]
    picked = hook.Decision(
        "x", testmon=True, tests={"scripts/dev/tests/test_python_tests_hook.py::t": "w"}
    )
    testmon, chosen = hook.commands(picked, ids, tmp_path / "r.json")
    assert "--testmon-forceselect" in testmon
    assert chosen[chosen.index("--select-tests") + 1] == str(ids)
    assert chosen[-1] == "scripts/dev/tests/test_python_tests_hook.py"
    for cmd in (*full, testmon, chosen):
        assert cmd[cmd.index("-m") + 1] == "not q_backend"


def test_the_records_seed_a_new_worktree_and_are_written_back(tmp_path):
    shared, local = tmp_path / "shared", tmp_path / "wt"
    shared.mkdir()
    local.mkdir()
    (shared / ".testmondata").write_text("seed")
    records = (local / ".testmondata", local / ".testreach.json")
    assert hook.seed(records, shared) == [".testmondata"]
    assert (local / ".testmondata").read_text() == "seed"
    (local / ".testreach.json").write_text("{}")
    hook.publish(records, shared)
    assert (shared / ".testreach.json").read_text() == "{}"
    assert hook.seed(records, shared) == [], "a worktree's own record is never overwritten"


def test_staged_changes_are_read_with_their_status(tmp_path, monkeypatch):
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "old.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("mv", "old.md", "new.md")
    (tmp_path / "a.q").write_text("a:1\n")
    git("add", "-A")
    monkeypatch.setattr(hook, "REPO", tmp_path)
    assert sorted(hook.staged_changes()) == [("A", "a.q"), ("A", "new.md"), ("D", "old.md")]


def test_the_record_is_read_from_testmons_own_table(tmp_path):
    data = tmp_path / ".testmondata"
    with sqlite3.connect(data) as db:
        db.execute("CREATE TABLE file_fp (id INTEGER PRIMARY KEY, filename TEXT)")
        db.executemany("INSERT INTO file_fp (filename) VALUES (?)", [(f,) for f in KNOWN])
    assert hook.testmon_files(data) == KNOWN
    assert hook.testmon_files(tmp_path / "absent") is None
