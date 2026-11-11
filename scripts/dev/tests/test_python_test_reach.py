"""scripts/dev/python_test_reach.py: what each test reads, lists and starts (#1879).

The record decides which tests a commit of anything but Python runs, so what
matters is that it charges each sighting to the right owner - the test, the
fixture that was being set up, the module being imported - and that a test
inherits its fixtures' and its module's.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

if "python_test_reach" in sys.modules:  # loaded by the root conftest already: the same module
    reach = sys.modules["python_test_reach"]
else:
    _SPEC = importlib.util.spec_from_file_location(
        "python_test_reach", Path(__file__).resolve().parents[1] / "python_test_reach.py"
    )
    assert _SPEC and _SPEC.loader
    reach = importlib.util.module_from_spec(_SPEC)
    sys.modules["python_test_reach"] = reach
    _SPEC.loader.exec_module(reach)


@pytest.mark.parametrize(
    ("argv", "kind"),
    [
        (["git", "ls-files"], "git"),
        (["/usr/bin/python3", "scripts/gates/check.py"], "python"),
        ([sys.executable, "-m", "uqs"], "python"),
        (["uv", "run", "uqs", "stack", "up"], "python"),
        (["/opt/kx/bin/q", "tests/run_tests.q"], "q"),
        (["bash", "torq.sh", "start", "all"], "q"),
        (["/tmp/x/torq.sh"], "q"),
        (["timeout", "30", "q", "x.q"], "q"),
        (["env", "A=1", "git", "status"], "git"),
        (["ps", "-A"], "inert"),
        (["mount"], "inert"),
        (["qlinter", "src/"], "q"),
        ([str(Path(reach.tempfile.gettempdir()) / "x" / "q_fake"), "-q"], "q"),
        (["docker", "run", "x"], "other"),
        ([], "other"),
        # #2660: what reads only the files its arguments name ...
        (["d2", "a.d2", "a.svg"], "args"),
        (["/usr/bin/grep", "-r", "x", "src"], "args"),
        # ... a program asked only its version or usage, which reads none ...
        (["rustc", "--version"], "inert"),
        (["d2", "--version"], "inert"),
        (["git", "--version"], "git"),
        (["node", "-e", "x"], "other"),
        # ... and sandbox-exec, known by what it runs, past its profile
        (["sandbox-exec", "-p", "(version 1)(allow default)", "true"], "inert"),
        (["sandbox-exec", "-p", "(version 1)", "bash", "-c", "x"], "q"),
        (["sandbox-exec", "-f", "p.sb", "docker"], "other"),
    ],
)
def test_a_started_process_is_known_by_the_program_it_runs(argv, kind):
    assert reach.kind_of(argv) == kind


def test_q_is_known_by_qcmd_whatever_the_binary_is_called():
    assert reach.kind_of(["/opt/kdbx/bin/kdbx"], qcmd="/opt/kdbx/bin/kdbx") == "q"


def test_a_path_is_recorded_relative_to_the_repository_and_tooling_is_not():
    assert reach._rel(REPO / "docs" / "README.md") == "docs/README.md"
    assert reach._rel(str(REPO)) == "."
    assert reach._rel("/etc/hosts") is None
    assert reach._rel(REPO / ".venv" / "x") is None
    assert reach._rel(REPO / "src" / "__pycache__" / "x.pyc") is None
    assert reach._rel(3) is None


def test_a_glob_lists_from_its_fixed_prefix():
    assert reach._glob_prefix("src/etl/**/*.q") == "src/etl"
    assert reach._glob_prefix("*.md") == "."


_INNER = """
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path({repo!r})
(REPO / "LICENSE").read_text()  # at import: the module's


@pytest.fixture(scope="session")
def shared():
    return (REPO / "README.md").read_text()


def test_uses_the_fixture(shared):
    pass


def test_also_uses_it(shared):
    pass


def test_reads_and_lists():
    (REPO / "pyproject.toml").read_text()
    os.listdir(REPO / "docs")


def test_starts_a_python_child_that_reads():
    code = "open(" + repr(str(REPO / "CHANGELOG.md")) + ").read()"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_starts_git():
    subprocess.run(["git", "--version"], cwd=REPO, capture_output=True, check=True)


def test_writes_nothing_it_reads(tmp_path):
    (tmp_path / "x").write_text("x")
"""


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    """Run _INNER under a Recorder, in this process, and return the record.
    The audit hook is process-wide, so whatever recorder this session itself
    reports to (the python-tests hook records every run) is restored after."""
    tmp_path = tmp_path_factory.mktemp("inner")
    test_file = tmp_path / "test_reach_inner.py"
    test_file.write_text(_INNER.format(repo=str(REPO)))
    out = tmp_path / "reach.json"
    recorder = reach.Recorder(out)
    before = list(reach._ACTIVE)
    environ = dict(os.environ)
    reach.install(recorder)
    recorder.watch_children()
    # Under xdist a worker can set this fixture up twice, each time in a new
    # directory; the first run's module must not be found in its place
    # ("import file mismatch").
    sys.modules.pop("test_reach_inner", None)
    try:
        code = pytest.main(
            [str(test_file), "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
             "-p", "no:testmon", "--rootdir", str(tmp_path)],
            plugins=[recorder],
        )  # fmt: skip
    finally:
        reach._ACTIVE[:] = before
        os.environ.clear()
        os.environ.update(environ)
    assert code == 0
    data = reach.load(out)
    assert data is not None
    return data, test_file.relative_to(tmp_path).as_posix()


def test_a_session_fixture_s_reads_reach_every_test_using_it(recorded):
    data, mod = recorded
    for name in ("test_uses_the_fixture", "test_also_uses_it"):
        assert "README.md" in reach.reach(data, f"{mod}::{name}")["reads"]
    assert "README.md" not in reach.reach(data, f"{mod}::test_starts_git").get("reads", ())


def test_a_test_s_own_reads_lists_and_starts_are_its(recorded):
    data, mod = recorded
    own = reach.reach(data, f"{mod}::test_reads_and_lists")
    assert "pyproject.toml" in own["reads"] and "docs" in own["lists"]
    git = reach.reach(data, f"{mod}::test_starts_git")
    assert git["starts"] == {"git"} and "." in git["lists"]


def test_a_python_child_s_reads_are_its_test_s_and_it_is_no_unseen_start(recorded):
    data, mod = recorded
    seen = reach.reach(data, f"{mod}::test_starts_a_python_child_that_reads")
    assert "CHANGELOG.md" in seen["reads"]
    assert "python" not in seen.get("starts", ())


def test_what_the_module_reads_at_import_reaches_all_its_tests(recorded):
    data, mod = recorded
    assert all("LICENSE" in reach.reach(data, t)["reads"] for t in data["tests"])


def test_reached_picks_by_the_record(recorded):
    data, mod = recorded
    assert set(reach.reached(data, [("M", "README.md")], {})) == {
        f"{mod}::test_uses_the_fixture",
        f"{mod}::test_also_uses_it",
    }
    # a new file at the root: the test whose git listed the root - and the
    # one whose Python child did, importing from its working directory - not
    # the tests that only read files
    added = set(reach.reached(data, [("A", "NEW.md")], {}))
    assert f"{mod}::test_starts_git" in added
    assert f"{mod}::test_uses_the_fixture" not in added
    assert reach.reached(data, [("M", "docs/README.md")], {}) == {}


def _started(tmp_path, argv, cwd=None):
    """A record of one test that started `argv` and did nothing else."""
    rec = reach.Recorder(tmp_path / "reach.json")
    rec.stack = ["t::x"]
    rec.tests = {"t::x": ["t::x"]}
    rec._start(argv, cwd)
    return rec.data()


def _picks(record, *paths):
    return bool(reach.reached(record, [("M", p) for p in paths], {}))


def test_a_program_reading_only_its_arguments_reaches_only_what_they_name(tmp_path):
    """#2660: d2 rendering a diagram copied out of the tree reaches no file
    of it - neither CHANGELOG.md nor docs/ - where an unknown program would
    reach every one."""
    copy = tmp_path / "sample.d2"
    record = _started(tmp_path, ["d2", str(copy), str(tmp_path / "sample.svg")])
    assert not _picks(record, "CHANGELOG.md", "docs/diagrams/tap.d2", "src/init.q")
    assert _picks(_started(tmp_path, ["docker", "run", "x"]), "CHANGELOG.md")


def test_a_repository_file_it_names_reaches_its_directory_but_no_further(tmp_path):
    """The file itself, and its siblings (a d2 import, an awk include, an svg
    beside its source): under-selecting costs more than a few extra tests."""
    named = REPO / "docs" / "diagrams" / "tap.d2"
    record = _started(tmp_path, ["d2", str(named), str(tmp_path / "tap.svg")])
    assert _picks(record, "docs/diagrams/tap.d2")
    assert _picks(record, "docs/diagrams/kafka.d2")
    assert not _picks(record, "CHANGELOG.md", "docs/README.md")
    # relative to where it runs, which by default is the repository
    assert _picks(_started(tmp_path, ["cat", "docs/diagrams/tap.d2"]), "docs/diagrams/x.d2")


def test_a_repository_directory_it_names_reaches_everything_under_it(tmp_path):
    record = _started(tmp_path, ["grep", "-r", "x", "src"])
    assert _picks(record, "src/etl/new.q")
    assert not _picks(record, "CHANGELOG.md")
    # an option's value is an argument too
    record = _started(tmp_path, ["grep", "-r", "x", f"--include={REPO / 'docs'}"])
    assert _picks(record, "docs/a.md")


def test_a_python_file_it_names_is_charged_as_a_script(tmp_path):
    """A .py file testmon never executed is chosen by who may read it."""
    record = _started(tmp_path, ["cat", "scripts/dev/python_test_reach.py"])
    assert reach.reach(record, "t::x")["scripts"] == {"scripts/dev/python_test_reach.py"}
    assert _picks(_started(tmp_path, ["grep", "-r", "x", "scripts"]), "scripts/gates/x.py")


def test_the_repository_named_inside_an_argument_is_an_unknown_program(tmp_path):
    """A path this cannot take apart - inside a pattern, a script, a list -
    could name any file, so the program is charged as one it cannot see into."""
    for argv in (
        ["grep", f"{REPO}/src|x", str(tmp_path / "f")],
        ["grep", f"-f{REPO}/x", str(tmp_path / "f")],
        ["jq", f"--from-file={REPO}/missing.jq"],
    ):
        record = _started(tmp_path, argv)
        assert reach.reach(record, "t::x")["starts"] == {"other"}, argv
        assert _picks(record, "CHANGELOG.md"), argv


def test_a_rerun_replaces_a_test_s_entry_and_adds_to_a_fixture_s():
    old = {
        "version": 1,
        "owners": {"t::a": {"reads": ["x"]}, "fixture:f": {"reads": ["y"]}},
        "tests": {"t::a": ["t::a", "fixture:f"], "t::b": ["t::b"]},
    }
    run = {
        "owners": {"t::a": {"reads": ["z"]}, "fixture:f": {"reads": ["w"]}},
        "tests": {"t::a": ["t::a", "fixture:f"]},
    }
    merged = reach.merge(old, [run])
    assert merged["owners"]["t::a"]["reads"] == ["z"]
    assert merged["owners"]["fixture:f"]["reads"] == ["w", "y"]
    assert set(merged["tests"]) == {"t::a", "t::b"}


def test_xdist_workers_write_parts_the_controller_merges(tmp_path):
    out = tmp_path / "reach.json"
    for worker, test in (("gw0", "t::a"), ("gw1", "t::b")):
        rec = reach.Recorder(out, worker)
        rec.tests[test] = [test]
        rec.owners[test]["reads"].add(f"{test[-1]}.csv")
        rec.pytest_sessionfinish(None)
    reach.Recorder(out).pytest_sessionfinish(None)
    data = json.loads(out.read_text())
    assert set(data["tests"]) == {"t::a", "t::b"}
    assert not list(tmp_path.glob("*.part"))


def test_a_record_of_another_version_is_no_record(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"version": 0, "owners": {}, "tests": {}}))
    assert reach.load(path) is None
    assert reach.load(tmp_path / "absent.json") is None


def test_the_suite_records_through_the_root_conftest(tmp_path):
    """--record-reach is the root conftest's option, so a run of any testpath records."""
    out = tmp_path / "reach.json"
    test = "scripts/dev/tests/test_python_test_reach.py::test_a_glob_lists_from_its_fixed_prefix"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:xdist", "-p", "no:cacheprovider",
         "--record-reach", str(out), test],
        cwd=REPO, env=env, check=True, capture_output=True,
    )  # fmt: skip
    data = reach.load(out)
    assert data is not None and test in data["tests"]


def test_a_test_id_drops_the_group_xdist_adds():
    assert reach.test_id("a.py::t@grp", ["xdist_group"]) == "a.py::t"
    assert reach.test_id("a.py::t[x@y]", []) == "a.py::t[x@y]"


def _work_tree(root: Path, linked: bool) -> Path:
    """A work tree at `root`: its own .git directory, or - as a linked
    worktree has - a .git file naming one elsewhere. Its git directory."""
    root.mkdir(parents=True)
    if not linked:
        (root / ".git").mkdir()
        return root / ".git"
    gitdir = root.parent / "common" / "worktrees" / root.name
    gitdir.mkdir(parents=True)
    (root / ".git").write_text(f"gitdir: {gitdir}\n")
    return gitdir


@pytest.mark.parametrize("linked", [False, True])
def test_the_record_is_written_whole_from_the_git_directory(tmp_path, linked):
    """#2273: the copy that replaces the record is written in the git
    directory, so a write cut short leaves no file in the work tree."""
    tree = tmp_path / "wt"
    gitdir = _work_tree(tree, linked)
    record = tree / ".testreach.json"
    assert reach.scratch_dir(record).resolve() == gitdir.resolve()
    reach.write_whole(record, '{"version": 1}')
    assert json.loads(record.read_text()) == {"version": 1}
    assert sorted(p.name for p in tree.iterdir()) == [".git", ".testreach.json"]
    assert list(gitdir.iterdir()) == [], "the copy became the record"


def test_a_record_outside_a_work_tree_root_is_written_beside_itself(tmp_path):
    assert reach.scratch_dir(tmp_path / "r.json") == tmp_path


def test_an_interrupted_write_leaves_no_copy_behind(tmp_path, monkeypatch):
    """A write that fails before the copy replaces the record removes the
    copy, and the record it would have replaced is untouched."""
    tree = tmp_path / "wt"
    gitdir = _work_tree(tree, linked=True)
    record = tree / ".testreach.json"
    record.write_text("old")

    def interrupted(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(reach.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        reach.write_whole(record, "new")
    assert record.read_text() == "old"
    assert sorted(p.name for p in tree.iterdir()) == [".git", ".testreach.json"]
    assert list(gitdir.iterdir()) == []


def test_a_run_leaves_no_copy_of_the_record_in_the_work_tree(tmp_path):
    """The recorder's own write, at the end of a run, goes through write_whole."""
    tree = tmp_path / "wt"
    _work_tree(tree, linked=True)
    recorder = reach.Recorder(tree / ".testreach.json")
    recorder.pytest_sessionfinish(None)
    assert reach.load(tree / ".testreach.json") is not None
    assert sorted(p.name for p in tree.iterdir()) == [".git", ".testreach.json"]


def test_a_file_s_scan_is_its_kind_or_the_whole_tree_when_it_is_alone():
    files = ["web/a.ts", "web/b.ts", "web/c.mjs"]
    assert reach.scan_of("web/a.ts", files) == ["web/a.ts", "web/b.ts"]
    assert reach.scan_of("web/c.mjs", files) == files


def test_a_scan_recorded_before_a_file_was_added_is_stale_and_a_named_read_is_not():
    """#2276: a test that listed web/ and read every .ts there, before d.ts
    was added, is rerun before the record is judged; one that reads a single
    file by name, though it lists the root too, is the judge's to find."""
    record = {
        "version": 1,
        "owners": {
            "t.py::scan": {"reads": ["web/a.ts", "web/b.ts", "web/c.ts"], "lists": ["web"]},
            "t.py::named": {"reads": ["web/a.ts"], "lists": ["."]},
            "t.py::whole": {"reads": ["web/a.ts", "web/b.ts", "web/c.ts", "web/d.ts"]},
            "t.py::unlisted": {"reads": ["web/a.ts", "web/b.ts", "web/c.ts"]},
        },
        "tests": {t: [t] for t in ("t.py::scan", "t.py::named", "t.py::whole", "t.py::unlisted")},
    }
    files = ["web/a.ts", "web/b.ts", "web/c.ts", "web/d.ts"]
    assert reach.stale_scans(record, files) == {"t.py::scan"}
    assert reach.stale_scans(record, files[:3]) == set()


def test_each_test_is_stamped_with_the_tree_whose_run_wrote_it(tmp_path):
    """#2276: a merge keeps each test's stamp from the run that last ran it,
    so a record seeded from another worktree says which entries are whose."""
    old = {
        "version": 1,
        "owners": {"a": {"reads": ["x"]}, "b": {"reads": ["y"]}},
        "tests": {"a": ["a"], "b": ["b"]},
        "where": {"a": "/elsewhere", "b": "/elsewhere"},
    }
    recorder = reach.Recorder(tmp_path / "r.json")
    recorder.tests = {"b": ["b"]}
    merged = reach.merge(old, [recorder.data()])
    here = str(reach.REPO.resolve())
    assert merged["where"] == {"a": "/elsewhere", "b": here}
    assert reach.recorded_here(merged) == {"b"}
    assert reach.recorded_here({"tests": {"b": []}}) == set()
