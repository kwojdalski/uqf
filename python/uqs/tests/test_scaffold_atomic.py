"""apply_plan writes a whole plan or none of it (#1568)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uqs.paths import STACK_TABLES_LIST, STACK_TABLES_TEST, UqsError
from uqs.scaffold import write
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode


def _tree(tmp_path: Path, expected_line: bool) -> Path:
    test_file = tmp_path / STACK_TABLES_TEST
    test_file.parent.mkdir(parents=True)
    test_file.write_text(f"{STACK_TABLES_LIST}:`a`b\n" if expected_line else "/ no list here\n")
    return tmp_path


def _plan() -> ScaffoldPlan:
    return ScaffoldPlan(
        "audit",
        [
            FileAction(Path("docs/audit/notes.md"), "notes\n"),
            FileAction(STACK_TABLES_TEST, "`audit", WriteMode.APPEND),
        ],
        [],
    )


def test_an_append_that_refuses_leaves_no_file_written(tmp_path):
    tree = _tree(tmp_path, expected_line=False)
    before = (tree / STACK_TABLES_TEST).read_text()
    with pytest.raises(UqsError, match="expected"):
        write.apply_plan(_plan(), tree)
    assert not (tree / "docs").exists()
    assert (tree / STACK_TABLES_TEST).read_text() == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_a_write_that_fails_rolls_back_what_was_written(tmp_path):
    tree = _tree(tmp_path, expected_line=True)
    target = tree / STACK_TABLES_TEST
    before = target.read_text()
    target.chmod(0o444)
    try:
        with pytest.raises(OSError):
            write.apply_plan(_plan(), tree)
    finally:
        target.chmod(0o644)
    assert not (tree / "docs").exists()
    assert target.read_text() == before


def test_a_plan_that_applies_writes_every_file(tmp_path):
    tree = _tree(tmp_path, expected_line=True)
    assert write.apply_plan(_plan(), tree) == [Path("docs/audit/notes.md"), STACK_TABLES_TEST]
    assert (tree / "docs/audit/notes.md").read_text() == "notes\n"
    assert (tree / STACK_TABLES_TEST).read_text() == f"{STACK_TABLES_LIST}:`a`b`audit\n"


def test_two_appends_to_one_file_both_land(tmp_path):
    tree = _tree(tmp_path, expected_line=True)
    plan = ScaffoldPlan(
        "audit",
        [
            FileAction(STACK_TABLES_TEST, "`x", WriteMode.APPEND),
            FileAction(STACK_TABLES_TEST, "`y", WriteMode.APPEND),
        ],
        [],
    )
    write.apply_plan(plan, tree)
    assert (tree / STACK_TABLES_TEST).read_text() == f"{STACK_TABLES_LIST}:`a`b`x`y\n"
