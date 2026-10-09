"""scripts/portable/full_suite.py (#863): the PeachQ lane's list of known gaps,
and the comparison that holds the suite to it both ways.

The lane itself - flattening, then the whole suite on PeachQ - takes a PeachQ
binary and about a minute and a half; CI runs it. These pin the parts a wrong
edit could quietly loosen: the file's format, and what counts as a regression.
"""

from __future__ import annotations

import importlib.util
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
    never pass - the lane reports it as NOW PASSES, but it is cheaper here."""
    defined = set()
    for path in (REPO / "tests" / "q").glob("test_*.q"):
        ns = None
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("\\d ."):
                ns = line[3:].strip()
            elif ns and line.startswith("test_") and ":" in line:
                defined.add(f"{ns}.{line.split(':', 1)[0]}")
    missing = sorted(set(fs.read_gaps()) - defined)
    assert not missing, f"listed in {fs.GAPS.name} but not in tests/q: {missing}"


def test_an_entry_without_a_reason_is_refused(tmp_path):
    p = tmp_path / "gaps.txt"
    p.write_text(".iotest.test_x\n")
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
        "\\d .xtest\n\n"
        "test_one:{[t]\n"
        '    .qunit.assertEquals[1;1;"positions past the file\'s end are refused"]};\n\n'
        "test_two:{[t]\n"
        '    / a comment line in the body\n'
        '    .qunit.assertTrue[1b;"short"]};\n'
    )  # fmt: skip
    return tmp_path


def test_a_tests_messages_are_read_from_its_own_body(tmp_path):
    tests = _suite(tmp_path)
    assert fs.messages_of(".xtest.test_one", tests) == ["positions past the file's end are refused"]
    assert fs.messages_of(".xtest.test_two", tests) == ["short"]
    assert fs.messages_of(".ytest.test_one", tests) == []


def test_a_reason_that_repeats_the_assertion_message_is_caught(tmp_path):
    tests = _suite(tmp_path)
    gaps = {
        ".xtest.test_one": "peachq-lacks: positions past the file's end are refused",
        ".xtest.test_two": "peachq-lacks: short",  # a word, not a message
    }
    assert fs.restated(gaps, tests) == [".xtest.test_one"]


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
