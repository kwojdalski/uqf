"""scripts/portable/full_suite.py (#863): the PeachQ lane's list of known gaps,
and the comparison that holds the suite to it both ways.

The lane itself - flattening, then the whole suite on PeachQ - takes a PeachQ
binary and about a minute and a half; CI runs it. These pin the parts a wrong
edit could quietly loosen: the file's format, and what counts as a regression.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "full_suite", REPO / "scripts" / "portable" / "full_suite.py"
)
assert _SPEC and _SPEC.loader
fs = importlib.util.module_from_spec(_SPEC)
sys.modules["full_suite"] = fs
_SPEC.loader.exec_module(fs)


def test_the_committed_list_reads_and_every_entry_has_a_reason():
    gaps = fs.read_gaps()
    assert gaps, "the lane is held to a list; an empty one is a claim to check, not a default"
    assert all(reason for reason in gaps.values())


def test_every_listed_test_exists_in_the_suite():
    """A test renamed or deleted would otherwise linger as an entry that can
    never pass - the lane reports it as NOW PASSES, but it is cheaper here.

    A suite defines `.qt.<owner>.<suite>.test_x:` at the start of a line."""
    defined = set()
    for path in (REPO / "tests" / "q").glob("test_*.q"):
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith(".qt.") and ":" in line:
                name = line.split(":", 1)[0]
                if name.rpartition(".")[2].startswith("test_"):
                    defined.add(name)
    missing = sorted(set(fs.read_gaps()) - defined)
    assert not missing, f"listed in {fs.GAPS.name} but not in tests/q: {missing}"


def test_the_test_tree_needs_no_context_conversion(tmp_path):
    """#1412: a suite defines everything from the root, so the converter that
    rewrites nested contexts for kdb+ 4.0 has nothing to do under tests/. This
    is the executable half of "the migrated tests run on q 4.0 without context
    conversion" that needs no 4.0 binary: it fails the day a test file enters a
    nested namespace (`\\d .qt.a.b`), which 4.0 rejects."""
    r = subprocess.run(
        [sys.executable, str(fs.FLATTENER), "tests", "--root", str(REPO), "--out",
         str(tmp_path / "flat"), "--dry-run", "--quiet"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    report = json.loads(r.stdout[r.stdout.index("{") :])
    assert report["status"] == "ok", report["refusals"]
    changed = [f["path"] for f in report["files"] if f["action"] != "unchanged"]
    assert not changed, f"these need context conversion: {changed}"


def test_an_entry_without_a_reason_is_refused(tmp_path):
    p = tmp_path / "gaps.txt"
    p.write_text(".qt.qetl.io.test_x\n")
    with pytest.raises(SystemExit, match="expected `.suite.test_name  # reason`"):
        fs.read_gaps(p)


def test_an_entry_listed_twice_is_refused(tmp_path):
    p = tmp_path / "gaps.txt"
    p.write_text(".a.test_x  # peachq-lacks: one\n.a.test_x  # peachq-lacks: two\n")
    with pytest.raises(SystemExit, match="listed twice"):
        fs.read_gaps(p)


def test_comments_and_blank_lines_are_not_entries(tmp_path):
    p = tmp_path / "gaps.txt"
    p.write_text("# header\n\nplatform: Linux-x86_64\n.a.test_x  # peachq-lacks: on disk\n")
    assert fs.read_gaps(p) == {".a.test_x": "peachq-lacks: on disk"}


def test_the_failures_file_keeps_status_and_why(tmp_path):
    p = tmp_path / "failures.txt"
    p.write_text(".a.test_x\terror\tnyi\n.a.test_y\tfail\tvalues differ\n\n")
    assert fs.read_failures(p) == {".a.test_x": "error: nyi", ".a.test_y": "fail: values differ"}


def test_a_new_failure_and_a_listed_test_that_passes_both_count():
    failed = {".a.test_known": "error: nyi", ".a.test_new": "fail: x"}
    gaps = {".a.test_known": "'nyi", ".a.test_fixed": "'type"}
    assert fs.compare(failed, gaps) == ([".a.test_new"], [".a.test_fixed"])


def test_nothing_to_report_when_the_failures_are_exactly_the_list():
    gaps = {".a.test_known": "'nyi"}
    assert fs.compare({".a.test_known": "error: nyi"}, gaps) == ([], [])


# --- a reason is a reason (#986) ---------------------------------------------


@pytest.mark.parametrize("reason", [
    "PeachQ result differs: the partition is written",  # the boilerplate #986 found
    "'nyi: on disk",
    "tree-bug: 985",  # an issue is named by its number
])  # fmt: skip
def test_a_reason_without_its_kind_is_refused(tmp_path, reason):
    p = tmp_path / "gaps.txt"
    p.write_text(f".a.test_x  # {reason}\n")
    with pytest.raises(SystemExit, match="reason must start with"):
        fs.read_gaps(p)


def test_each_kind_is_accepted(tmp_path):
    p = tmp_path / "gaps.txt"
    p.write_text(".a.test_x  # peachq-lacks: on-disk attributes\n.a.test_y  # tree-bug: #985\n"
                 ".a.test_z  # flattening: reads the source as text\n")  # fmt: skip
    assert len(fs.read_gaps(p)) == 3


def _suite(tmp_path):
    (tmp_path / "test_x.q").write_text(
        ".qt.x.test_one:{[t]\n"
        '    .qunit.assertEquals[1;1;"positions past the file\'s end are refused"]};\n\n'
        ".qt.x.test_two:{[t]\n"
        '    / a comment line in the body\n'
        '    .qunit.assertTrue[1b;"short"]};\n'
    )  # fmt: skip
    return tmp_path


def test_a_tests_messages_are_read_from_its_own_body(tmp_path):
    tests = _suite(tmp_path)
    assert fs.messages_of(".qt.x.test_one", tests) == ["positions past the file's end are refused"]
    assert fs.messages_of(".qt.x.test_two", tests) == ["short"]
    assert fs.messages_of(".qt.y.test_one", tests) == []


def test_a_reason_that_repeats_the_assertion_message_is_caught(tmp_path):
    tests = _suite(tmp_path)
    gaps = {
        ".qt.x.test_one": "peachq-lacks: positions past the file's end are refused",
        ".qt.x.test_two": "peachq-lacks: short",  # a word, not a message
    }
    assert fs.restated(gaps, tests) == [".qt.x.test_one"]


def test_no_committed_reason_repeats_its_tests_message():
    assert fs.restated(fs.read_gaps()) == []


# --- the gaps are CI's platform's (#968) --------------------------------------


def test_the_committed_list_records_cis_platform():
    """CI's PeachQ lane runs on ubuntu, x86_64 - the platform these were seen on."""
    assert fs.read_platform() == "Linux-x86_64"


def test_a_list_without_its_platform_is_refused(tmp_path):
    p = tmp_path / "gaps.txt"
    p.write_text(".a.test_x  # peachq-lacks: on disk\n")
    with pytest.raises(SystemExit, match="platform"):
        fs.read_platform(p)


def test_a_difference_on_cis_platform_fails_the_lane(capsys):
    gaps = {".a.test_known": "peachq-lacks: x"}
    assert fs.report({".a.test_new": "fail: x"}, gaps, "Linux-x86_64", "Linux-x86_64") == 1
    assert "NEW FAILURE" in capsys.readouterr().err


def test_elsewhere_a_difference_is_not_comparable_rather_than_a_verdict(capsys):
    gaps = {".a.test_known": "peachq-lacks: x"}
    assert fs.report({".a.test_new": "fail: x"}, gaps, "Linux-x86_64", "Darwin-arm64") == 0
    out = capsys.readouterr()
    assert "NOT COMPARABLE on Darwin-arm64" in out.out
    assert "NEW FAILURE" not in out.err and "NOW PASSES" not in out.err


# --- --shards (#1694) -------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 4, 9])
def test_every_suite_file_is_in_exactly_one_shard(count):
    files = [f"test_{i}.q" for i in range(30)]
    weights = {f: float(i % 7 + 1) for i, f in enumerate(files)}
    split = fs.assign(files, weights, count)
    flat = [f for shard in split for f in shard]
    assert len(split) == count and sorted(flat) == sorted(files)


def test_the_shards_are_balanced_and_the_split_is_stable():
    weights = {"a.q": 40.0, "b.q": 30.0, "c.q": 20.0, "d.q": 10.0, "e.q": 10.0}
    split = fs.assign(list(weights), weights, 2)
    assert sorted(sum(weights[f] for f in s) for s in split) == [50.0, 60.0]
    assert fs.assign(list(reversed(list(weights))), weights, 2) == split


def test_shards_merge_their_failures_and_a_shard_that_never_loaded_fails_the_lane(tmp_path):
    """Two fake 'q's: each writes its own failures file, or none at all."""

    def fake_q(writes: str) -> str:
        script = tmp_path / f"q-{len(list(tmp_path.glob('q-*')))}"
        script.write_text(f"#!/bin/sh\n{writes}\n")
        script.chmod(0o755)
        return str(script)

    trees = []
    for name in ("shard-1", "shard-2"):
        (tmp_path / name).mkdir()
        trees.append((tmp_path / name, [f"{name}.q"]))
    ok = fake_q('printf ".qt.a.test_x\\tfail\\twhy\\n" > "$UQF_TEST_FAILURES"')
    merged = fs.run_shards(ok, trees, 30, {})
    assert merged == {".qt.a.test_x": "fail: why"}
    silent = fake_q("exit 1")
    assert fs.run_shards(silent, trees, 30, {}) is None
