"""scripts/dev/python_tests_hook.py: which Python tests a commit runs (#1879).

Never the whole suite by default: testmon for staged Python, the reach record
for everything else, the union of the two - and the whole suite only when
that cannot be decided, or full mode (UQF_HOOKS_FULL=1) asks for it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
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
        ([("M", "scripts/dev/python_test_reach.py")], "recorder"),
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
        assert cmd[cmd.index("-m") + 1] == "not q_backend and not judges_reach_record"


def test_the_record_s_judges_run_alone_after_the_tests_with_the_same_selection(tmp_path):
    """#2273: a judge of the reach record never runs beside the tests that
    rewrite it; the second pass runs only judges, over the same selection,
    recording into the record itself."""
    ids, record = tmp_path / "ids.txt", tmp_path / ".testreach.json"
    picked = hook.Decision(
        "x", testmon=True, tests={"scripts/dev/tests/test_python_tests_hook.py::t": "w"}
    )
    for decision in (hook.Decision("x", full=True), picked):
        first = hook.commands(decision, ids, tmp_path / "r.json")
        judges = hook.commands(decision, ids, record, judges=True)
        assert len(judges) == len(first)
        for a, b in zip(first, judges, strict=True):
            assert b[b.index("-m") + 1] == "judges_reach_record and not q_backend"
            assert b[b.index("--record-reach") + 1] == str(record)
            assert "-n" not in b and "-n" in a
            assert _after_testmon(a) == _after_testmon(b)
    assert hook.commands(hook.Decision("x"), ids, record, judges=True) == []


def _after_testmon(cmd: list[str]) -> list[str]:
    """What a run selects with: its words from --testmon on, but the record."""
    record = cmd[cmd.index("--record-reach") + 1]
    return [w for w in cmd[cmd.index("--testmon") :] if w != record]


_CONFTEST = """
import importlib.util, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("python_test_reach", {python_test_reach!r})
reach = importlib.util.module_from_spec(spec)
sys.modules["python_test_reach"] = reach
spec.loader.exec_module(reach)
reach.REPO = Path(__file__).resolve().parent


def pytest_addoption(parser):
    for flag in ("--testmon", "--testmon-forceselect"):
        parser.addoption(flag, action="store_true")
    parser.addoption("--record-reach")
    parser.addoption("--select-tests")


def pytest_configure(config):
    config.addinivalue_line("markers", "judges_reach_record: judges the record")
    config.addinivalue_line("markers", "q_backend: a q script")
    path = config.getoption("--record-reach")
    if path:
        recorder = reach.Recorder(Path(path).resolve())
        reach.install(recorder)
        config.pluginmanager.register(recorder, "test-reach")


def pytest_collection_modifyitems(config, items):
    path = config.getoption("--select-tests")
    if path:
        wanted = set(Path(path).read_text().split())
        items[:] = [i for i in items if i.nodeid in wanted]
"""

#: A scan reads every web/*.ts; the judge - CI's rule in miniature - fails
#: when a test reads some web/*.ts files but not all, as a scan judged by a
#: record from before a file was added does.
_TESTS = """
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


def test_scan():
    for path in sorted((ROOT / "web").glob("*.ts")):
        path.read_text()


@pytest.mark.judges_reach_record
def test_judge():
    if not (ROOT / ".testreach.json").is_file():
        pytest.skip("no record yet, as the real judge skips")
    record = json.loads((ROOT / ".testreach.json").read_text())
    web = {f"web/{p.name}" for p in (ROOT / "web").glob("*.ts")}
    for test, keys in record["tests"].items():
        reads = set()
        for key in keys:
            reads.update(record["owners"].get(key, {}).get("reads", []))
        assert reads & web in (set(), web), f"{test} reads {sorted(reads & web)} of {sorted(web)}"
"""


def test_a_push_adding_a_file_a_scan_reads_passes_the_first_time(tmp_path, monkeypatch):
    """#2273, end to end: a record from before a web/ file was added, and one
    run of what the hook selects for the push adding it, passes - where the
    judge run beside the scan, as it was, failed against the stale record."""
    project = tmp_path / "project"
    (project / "web").mkdir(parents=True)
    (project / "conftest.py").write_text(
        _CONFTEST.replace("{python_test_reach!r}", repr(str(hook.HERE / "python_test_reach.py")))
    )
    (project / "test_web.py").write_text(_TESTS)
    for name in ("a.ts", "b.ts"):
        (project / "web" / name).write_text("export {};\n")
    record = project / ".testreach.json"
    for key in [k for k in os.environ if k.startswith("PYTEST_")]:
        monkeypatch.delenv(key)
    monkeypatch.setattr(hook, "REPO", project)
    monkeypatch.setattr(hook, "REACH_DATA", record)
    monkeypatch.setattr(hook, "SPREAD", [])
    monkeypatch.setattr(
        hook, "PYTEST",
        [sys.executable, "-m", "pytest", "-q", f"--rootdir={project}", "-p", "no:cacheprovider",
         "-p", "no:pytest-testmon", "-p", "no:xdist"],
    )  # fmt: skip

    assert hook.run(hook.Decision("first record", full=True)) == 0
    (project / "web" / "c.ts").write_text("export {};\n")
    stale = record.read_text()
    assert "web/c.ts" not in stale

    beside = subprocess.run(
        [*hook.PYTEST, "--record-reach", str(record), "test_web.py"],
        cwd=project, capture_output=True, text=True,
    )  # fmt: skip
    assert beside.returncode == 1 and "test_judge" in beside.stdout, beside.stdout
    record.write_text(stale)

    push = hook.decide(
        [("A", "web/c.ts")],
        full_mode=False,
        known=set(),
        record=json.loads(stale),
        q_readers=_no_q_readers,
    )
    assert set(push.tests) == {"test_web.py::test_scan", "test_web.py::test_judge"}
    assert hook.run(push) == 0
    assert "web/c.ts" in record.read_text()


def test_a_scan_the_push_does_not_reach_is_rerun_before_the_judge(tmp_path, monkeypatch):
    """#2276: web/c.ts came from master, not from this push, so nothing the
    push reaches rereads web/ - the scan's record still predates c.ts. The
    hook reruns that scan before it runs the judge, which passes."""
    project = tmp_path / "project"
    (project / "web").mkdir(parents=True)
    (project / "conftest.py").write_text(
        _CONFTEST.replace("{python_test_reach!r}", repr(str(hook.HERE / "python_test_reach.py")))
    )
    (project / "test_web.py").write_text(_TESTS)
    for name in ("a.ts", "b.ts"):
        (project / "web" / name).write_text("export {};\n")
    record = project / ".testreach.json"
    for key in [k for k in os.environ if k.startswith("PYTEST_")]:
        monkeypatch.delenv(key)
    monkeypatch.setattr(hook, "REPO", project)
    monkeypatch.setattr(hook, "REACH_DATA", record)
    monkeypatch.setattr(hook, "SPREAD", [])
    monkeypatch.setattr(
        hook, "PYTEST",
        [sys.executable, "-m", "pytest", "-q", f"--rootdir={project}", "-p", "no:cacheprovider",
         "-p", "no:pytest-testmon", "-p", "no:xdist"],
    )  # fmt: skip

    assert hook.run(hook.Decision("first record", full=True)) == 0
    (project / "web" / "c.ts").write_text("export {};\n")
    assert hook._stale_scans() == {"test_web.py::test_scan"}
    assert hook.run(hook.Decision("only the judge", tests={"test_web.py::test_judge": "x"})) == 0
    assert "web/c.ts" in record.read_text()
    assert hook._stale_scans() == set()


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
    # Run by the pre-push hook itself, the suite inherits a pushed range.
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    assert sorted(hook.staged_changes()) == [("A", "a.q"), ("A", "new.md"), ("D", "old.md")]


def test_a_push_reads_the_pushed_range_not_the_index(tmp_path, monkeypatch):
    """At `git push` (#1965) pre-commit names the range being pushed; what is
    staged then is unrelated to what is pushed."""
    import subprocess

    def git(*a):
        return subprocess.run(
            ["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "old.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    start = git("rev-parse", "HEAD")
    (tmp_path / "pushed.q").write_text("a:1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "pushed")
    end = git("rev-parse", "HEAD")
    (tmp_path / "staged.md").write_text("y\n")
    git("add", "-A")
    monkeypatch.setattr(hook, "REPO", tmp_path)
    env = {"PRE_COMMIT_FROM_REF": start, "PRE_COMMIT_TO_REF": end}
    assert hook.staged_changes(env) == [("A", "pushed.q")]


def test_the_record_is_read_from_testmons_own_table(tmp_path):
    data = tmp_path / ".testmondata"
    with sqlite3.connect(data) as db:
        db.execute("CREATE TABLE file_fp (id INTEGER PRIMARY KEY, filename TEXT)")
        db.executemany("INSERT INTO file_fp (filename) VALUES (?)", [(f,) for f in KNOWN])
    assert hook.testmon_files(data) == KNOWN
    assert hook.testmon_files(tmp_path / "absent") is None
