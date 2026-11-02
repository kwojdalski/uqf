"""scripts/dev/test_reach.py: what each test reads, lists and starts (#1879).

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

if "test_reach" in sys.modules:  # loaded by the root conftest already: the same module
    reach = sys.modules["test_reach"]
else:
    _SPEC = importlib.util.spec_from_file_location(
        "test_reach", Path(__file__).resolve().parents[1] / "test_reach.py"
    )
    assert _SPEC and _SPEC.loader
    reach = importlib.util.module_from_spec(_SPEC)
    sys.modules["test_reach"] = reach
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
        (["docker", "run", "x"], "other"),
        ([], "other"),
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
    test = "scripts/dev/tests/test_test_reach.py::test_a_glob_lists_from_its_fixed_prefix"
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
