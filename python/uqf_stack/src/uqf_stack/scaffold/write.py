"""Applying a scaffold plan: writing its files, and placing each appended line.

Split from scaffold/jobs.py when it crossed the 400-line threshold this package
holds its modules to - the seam `scaffold/plan.py` already names: jobs.py BUILDS
a plan, this APPLIES one. The two change for different reasons - a new kind of
job there, a new file with its own idea of where an appended line goes here.
"""

from __future__ import annotations

from pathlib import Path

from uqf_stack.paths import REGISTRY_FILE, RUN_TESTS_FILE, STACK_TABLES_TEST, UqfStackError
from uqf_stack.scaffold.plan import FileAction, ScaffoldPlan, WriteMode


def apply_plan(plan: ScaffoldPlan, repo_root: Path) -> list[Path]:
    """Write a plan, or refuse the whole thing.

    Every target is checked BEFORE anything is written: a scaffold that
    created three files and then refused the fourth would leave a tree that
    neither loads nor reverts cleanly, and the half of it that did land
    registers itself on load.
    """
    for action in plan.actions:
        target = repo_root / action.path
        if action.mode is WriteMode.CREATE and target.exists():
            raise UqfStackError(
                f"{action.path} already exists - pick another name, or remove it first"
            )
        if action.mode is WriteMode.APPEND and not target.is_file():
            raise UqfStackError(f"{action.path} does not exist, so there is nothing to append to")

    written: list[Path] = []
    for action in plan.actions:
        target = repo_root / action.path
        match action.mode:
            case WriteMode.CREATE:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(action.body)
            case WriteMode.APPEND:
                target.write_text(_appended(target.read_text(), action))
            case _:  # pragma: no cover - unreachable while WriteMode has two members
                # Named rather than folded into the append branch, which is
                # what the old `else` did: a third mode added later would have
                # been silently appended instead of refused.
                raise UqfStackError(f"unknown write mode {action.mode!r} for {action.path}")
        written.append(action.path)
    return written


def _appended(existing: str, action: FileAction) -> str:
    """`existing` with the action's body added where that file wants it.

    Two files take their content somewhere other than the end, and both refuse
    rather than guess when they do not look the way this expects - appending to
    the end of either would be syntactically valid and register nothing.

    model/registry.py: the entry belongs INSIDE the PIPELINES tuple, so it goes before
    the closing paren.

    run_tests.q: the namespace belongs inside the `nsList:` symbol list, before
    its terminating semicolon.

    test_stack_tables.q: the table belongs at the end of the `expected:` list.
    """
    if action.path == RUN_TESTS_FILE:
        return _with_nslist_entry(existing, action.body)
    if action.path == STACK_TABLES_TEST:
        return _with_expected_table(existing, action.body)
    if action.path != REGISTRY_FILE:
        return existing.rstrip("\n") + "\n" + action.body
    marker = "\n)\n"
    if not existing.endswith(marker):
        raise UqfStackError(
            "model/registry.py does not end with the PIPELINES tuple's closing paren, so "
            "this scaffold cannot tell where an entry goes - add it by hand"
        )
    return existing[: -len(marker)] + "\n" + action.body + ")\n"


def _with_nslist_entry(existing: str, entry: str) -> str:
    """`run_tests.q` with `entry` added to its nsList.

    The list is one long line of backtick symbols ending in `;`, so the entry
    goes immediately before that semicolon. Refusals rather than guesses:
    exactly one `nsList:` line must exist and it must end in a semicolon, since
    appending anywhere else produces a file that loads, runs the same suites as
    before, and reports nothing missing.
    """
    lines = existing.splitlines(keepends=True)
    found = [i for i, line in enumerate(lines) if line.startswith("nsList:")]
    if len(found) != 1:
        raise UqfStackError(
            f"{RUN_TESTS_FILE} has {len(found)} lines starting `nsList:`, expected 1 - "
            "this scaffold cannot tell where a namespace goes, so add it by hand"
        )
    index = found[0]
    line = lines[index].rstrip("\n")
    if not line.endswith(";"):
        raise UqfStackError(
            f"{RUN_TESTS_FILE}'s nsList line does not end in `;` - add the namespace by hand"
        )
    if entry in line:
        raise UqfStackError(
            f"{entry} is already in {RUN_TESTS_FILE}'s nsList - pick another job name"
        )
    lines[index] = line[:-1] + entry + ";\n"
    return "".join(lines)


def _with_expected_table(existing: str, entry: str) -> str:
    """`test_stack_tables.q` with `entry` added to its `expected:` list.

    One line of backtick symbols with no terminator, so the entry goes at its
    end. The same refusals as the nsList: exactly one `expected:` line, and
    the table not already on it - a name the q file already defines means the
    scaffold would be redefining someone else's table.
    """
    lines = existing.splitlines(keepends=True)
    found = [i for i, line in enumerate(lines) if line.startswith("expected:")]
    if len(found) != 1:
        raise UqfStackError(
            f"{STACK_TABLES_TEST} has {len(found)} lines starting `expected:`, expected 1 - "
            "this scaffold cannot tell where a table goes, so add it by hand"
        )
    index = found[0]
    line = lines[index].rstrip("\n").rstrip()
    listed = line.removeprefix("expected:").split("`")
    if entry.lstrip("`") in listed:
        raise UqfStackError(
            f"{entry} is already in {STACK_TABLES_TEST}'s expected list - "
            "that table exists, so pick another name"
        )
    lines[index] = line + entry + "\n"
    return "".join(lines)
