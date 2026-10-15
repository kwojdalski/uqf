"""Applying a scaffold plan: writing its files, and placing each appended line.

Split from scaffold/jobs.py when it crossed the 400-line threshold this package
holds its modules to - the seam `scaffold/plan.py` already names: jobs.py BUILDS
a plan, this APPLIES one. The two change for different reasons - a new kind of
job there, a new file with its own idea of where an appended line goes here.
"""

from __future__ import annotations

from pathlib import Path

from uqs.paths import STACK_TABLES_LIST, STACK_TABLES_TEST, UqsError
from uqs.scaffold import external, profile
from uqs.scaffold.docs import SHOWCASE_PAGE, with_card
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode


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
            raise UqsError(f"{action.path} already exists - pick another name, or remove it first")
        if action.mode is WriteMode.APPEND and not target.is_file():
            raise UqsError(f"{action.path} does not exist, so there is nothing to append to")

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
                raise UqsError(f"unknown write mode {action.mode!r} for {action.path}")
        written.append(action.path)
    return written


def _appended(existing: str, action: FileAction) -> str:
    """`existing` with the action's body added where that file wants it.

    Some files take their content somewhere other than the end, and refuse
    rather than guess when they do not look the way this expects - appending to
    the end would be syntactically valid and register nothing.

    test_stack_tables.q: the table belongs at the end of the `expected` list.

    The Showcase page: a card belongs at the end of its section - see
    scaffold/docs.py's with_card.
    """
    if action.path == STACK_TABLES_TEST:
        return _with_expected_table(existing, action.body)
    if action.path == profile.PROFILES_FILE:
        return profile.apply(existing, action)
    if action.path == SHOWCASE_PAGE:
        return with_card(existing, action.body, action.anchor)
    if action.path == external.DEPENDENCIES_FILE:
        return external.with_producer(existing, action.body)
    return existing.rstrip("\n") + "\n" + action.body


def _with_expected_table(existing: str, entry: str) -> str:
    """`test_stack_tables.q` with `entry` added to its `expected:` list.

    One line of backtick symbols with no terminator, so the entry goes at its
    end. Refusals rather than guesses: exactly one `expected:` line, and
    the table not already on it - a name the q file already defines means the
    scaffold would be redefining someone else's table.
    """
    lines = existing.splitlines(keepends=True)
    found = [i for i, line in enumerate(lines) if line.startswith(f"{STACK_TABLES_LIST}:")]
    if len(found) != 1:
        raise UqsError(
            f"{STACK_TABLES_TEST} has {len(found)} lines starting "
            f"`{STACK_TABLES_LIST}:`, expected 1 - "
            "this scaffold cannot tell where a table goes, so add it by hand"
        )
    index = found[0]
    line = lines[index].rstrip("\n").rstrip()
    listed = line.removeprefix(f"{STACK_TABLES_LIST}:").split("`")
    if entry.lstrip("`") in listed:
        raise UqsError(
            f"{entry} is already in {STACK_TABLES_TEST}'s expected list - "
            "that table exists, so pick another name"
        )
    lines[index] = line + entry + "\n"
    return "".join(lines)
