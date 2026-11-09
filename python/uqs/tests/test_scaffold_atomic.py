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


@pytest.mark.parametrize("existing", [False, True])
def test_a_partial_write_rolls_back_the_failing_target(tmp_path, monkeypatch, existing):
    target = tmp_path / "partial" / "target.txt"
    original = "é€ original contents\r\n"
    if existing:
        target.parent.mkdir()
        target.write_text(original)
    mode = WriteMode.APPEND if existing else WriteMode.CREATE
    plan = ScaffoldPlan(
        "audit",
        [
            FileAction(Path("docs/audit/notes.md"), "notes\n"),
            FileAction(Path("partial/target.txt"), "replacement\n", mode),
        ],
    )
    write_text = Path.write_text
    failed = False

    def partial_write(path, text, *args, **kwargs):
        nonlocal failed
        if path == target and not failed:
            failed = True
            path.write_bytes(text.encode()[:3])
            raise OSError("write failed after truncating the target")
        return write_text(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", partial_write)
    with pytest.raises(OSError, match="after truncating"):
        write.apply_plan(plan, tmp_path)
    assert not (tmp_path / "docs").exists()
    if existing:
        assert target.read_bytes() == original.encode()
    else:
        assert not target.parent.exists()


def test_a_filename_the_filesystem_refuses_rolls_back_without_a_second_error(tmp_path, monkeypatch):
    """#2507: the rollback unlinked the over-long path it never created, which
    raised again and skipped removing the folders made for the plan. The plan
    refuses such a name up front now, so that check is bypassed to reach the
    rollback, which must still hold for any path the filesystem refuses."""
    monkeypatch.setattr(write, "_check_filenames", lambda plan: None, raising=False)
    plan = ScaffoldPlan(
        "audit",
        [
            FileAction(Path("docs/audit/notes.md"), "notes\n"),
            FileAction(Path("tests") / ("x" * 300 + ".q"), "body\n"),
        ],
    )
    with pytest.raises(OSError) as raised:
        write.apply_plan(plan, tmp_path)
    assert raised.value.__context__ is None, "the rollback raised a second error"
    assert list(tmp_path.iterdir()) == []


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
