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
    p.write_text(".a.test_x  # one\n.a.test_x  # two\n")
    with pytest.raises(SystemExit, match="listed twice"):
        fs.read_gaps(p)


def test_comments_and_blank_lines_are_not_entries(tmp_path):
    p = tmp_path / "gaps.txt"
    p.write_text("# header\n\n.a.test_x  # 'nyi: on disk\n")
    assert fs.read_gaps(p) == {".a.test_x": "'nyi: on disk"}


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
