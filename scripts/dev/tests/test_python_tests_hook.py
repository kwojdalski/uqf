"""scripts/dev/python_tests_hook.py: which Python tests a commit runs.

The one property that matters is the safe direction: testmon is chosen only
when every staged file is Python it can see, and everything else - a .q, a
CSV, a conftest, a file no test runs in-process, or no record at all - gets
the whole suite.
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


def test_python_testmon_has_seen_runs_only_the_affected_tests():
    mode, _ = hook.choose(["python/uqs/src/uqs/paths.py"], KNOWN)
    assert mode == "testmon"


def test_a_new_test_module_is_left_to_testmon():
    """testmon runs a test it has no record of, so a new one needs no full run."""
    assert hook.choose(["python/uqs/tests/test_brand_new.py"], KNOWN)[0] == "testmon"


@pytest.mark.parametrize(
    ("staged", "why"),
    [
        (["src/etl/core/status.q"], "non-Python"),
        (["python/uqs/src/uqs/paths.py", "scripts/torqconfig/sources.csv"], "non-Python"),
        (["uv.lock"], "non-Python"),
        (["python/conftest.py"], "conftest"),
        (["scripts/gates/check_x.py"], "in-process"),
        ([], "nothing staged"),
    ],
)
def test_anything_testmon_cannot_see_runs_the_whole_suite(staged, why):
    mode, reason = hook.choose(staged, KNOWN)
    assert mode == "full"
    assert why in reason


def test_without_a_record_the_whole_suite_runs():
    mode, reason = hook.choose(["python/uqs/src/uqs/paths.py"], None)
    assert (mode, "no testmon record") == ("full", reason[: len("no testmon record")])


def test_the_record_is_read_from_testmons_own_table(tmp_path):
    data = tmp_path / ".testmondata"
    with sqlite3.connect(data) as db:
        db.execute("CREATE TABLE file_fp (id INTEGER PRIMARY KEY, filename TEXT)")
        db.executemany("INSERT INTO file_fp (filename) VALUES (?)", [(f,) for f in KNOWN])
    assert hook.testmon_files(data) == KNOWN
    assert hook.testmon_files(tmp_path / "absent") is None
