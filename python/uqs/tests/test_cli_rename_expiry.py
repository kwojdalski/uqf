"""Every old spelling a rename keeps has a recorded window, and none outlives it
(#1796, #1738 rule 6).

`uqs.cli.rename_expiry.RENAMED_AFTER` holds, per old spelling, the stable tag
that was the newest when its rename landed. The next stable tag shipped the
rename with the old spelling kept, logged; the one after that must not ship the
old spelling - "kept for one release". The stable tags are CHANGELOG.md's
`## stable/...` headings, which the snapshot that cuts each tag writes, so the
test needs no git history (CI checks out shallow, without tags).
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import typer

from uqs import cli
from uqs.cli import lists, seed
from uqs.cli.rename_expiry import RENAMED_AFTER

ROOT = Path(__file__).resolve().parents[3]

#: How many stable tags an old spelling may outlive the one that shipped it.
#: renames.py promises "one release": the tag that ships a rename carries the
#: old spelling, and the next must not, so a newer tag is already one too many.
RELEASES_KEPT = 0

_STABLE = re.compile(r"^## (stable/\d{4}-\d{2}-\d{2}(?:\.\d+)?)\s*$", re.MULTILINE)


def stable_tags() -> list[str]:
    """The stable tags, oldest first, from CHANGELOG.md (newest first there)."""
    return _STABLE.findall((ROOT / "CHANGELOG.md").read_text())[::-1]


def _commands(command: Any, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield path, command
    for name, sub in getattr(command, "commands", {}).items():
        yield from _commands(sub, (*path, name))


def kept_in_the_cli() -> set[str]:
    """Every old spelling the command tree carries: an option `renamed()`
    rewrites, a hidden option, and a hidden command - an alias, or the
    hand-written `deploy build-runtime`. An alias's own options come with it,
    and a hidden group is only the alias commands under it."""
    found: set[str] = set()
    for path, command in _commands(typer.main.get_command(cli.app), ("uqs",)):
        words = " ".join(path)
        if getattr(command, "hidden", False) and not hasattr(command, "commands"):
            found.add(words)
            continue
        found |= {f"{words} {old}" for old in getattr(command, "renames", {})}
        for param in command.params:
            if getattr(param, "hidden", False):
                found |= {f"{words} {opt}" for opt in param.opts}
    # `uqs --yes`: the global flag `uqs --target` reads, hidden, never renamed
    return found - {"uqs --yes"}


#: Old spellings no command tree shows, each with the check that it is still
#: there - so a row outliving its spelling is caught like any other.
HAND_KEPT: dict[str, Callable[[], bool]] = {
    **{f"uqs list {kind}": (lambda k=kind: k in lists.MOVED) for kind in lists.MOVED},
    "uqs data seed --from RUNTIME": lambda: "range_from in RUNTIMES" in inspect.getsource(seed),
    "mcp uqs_summary": lambda: hasattr(__import__("uqs.mcp").mcp, "uqs_summary"),
}


def recorded() -> dict[str, str]:
    """Old spelling -> the stable tag that was newest when its rename landed."""
    rows: dict[str, str] = {}
    for after, spellings in RENAMED_AFTER.items():
        for spelling in spellings:
            assert spelling not in rows, f"`{spelling}` is recorded twice"
            rows[spelling] = after
    return rows


def test_every_old_spelling_records_when_it_was_renamed():
    missing = (kept_in_the_cli() | set(HAND_KEPT)) - set(recorded())
    assert not missing, (
        f"old spellings with no window: {sorted(missing)} - add each to "
        "uqs/cli/rename_expiry.py RENAMED_AFTER, under the newest `## stable/` "
        "heading in CHANGELOG.md"
    )


def test_every_recorded_spelling_is_still_kept():
    gone = set(recorded()) - kept_in_the_cli() - {k for k, kept in HAND_KEPT.items() if kept()}
    assert not gone, f"rows for old spellings that are gone - delete them: {sorted(gone)}"


def test_each_window_starts_at_a_stable_tag():
    tags = stable_tags()
    assert tags, "CHANGELOG.md has no `## stable/...` heading"
    unknown = set(RENAMED_AFTER) - set(tags)
    assert not unknown, f"RENAMED_AFTER keys that are no stable tag in CHANGELOG.md: {unknown}"


def overdue(rows: dict[str, str], tags: list[str], kept: int = RELEASES_KEPT) -> dict[str, str]:
    """Old spelling -> the tag that shipped it, for each that has outlived
    `kept` releases after that one."""
    late: dict[str, str] = {}
    for spelling, after in rows.items():
        newer = tags[tags.index(after) + 1 :]  # newer[0] shipped the rename
        if len(newer) > 1 + kept:
            late[spelling] = newer[0]
    return late


def test_no_old_spelling_outlives_its_one_release():
    late = overdue(recorded(), stable_tags())
    assert not late, (
        "old spellings kept past their one release - remove each, its alias or "
        "renamed() row, and its row in uqs/cli/rename_expiry.py: "
        + ", ".join(f"`{s}` (shipped in {t})" for s, t in sorted(late.items()))
    )


@pytest.mark.parametrize(
    ("tags", "late"),
    [
        (["stable/a"], {}),  # renamed after the newest tag: not shipped yet
        (["stable/a", "stable/b"], {}),  # shipped in b, its one release
        (["stable/a", "stable/b", "stable/c"], {"uqs up": "stable/b"}),  # c shipped it again
    ],
)
def test_overdue_counts_the_releases_after_the_one_that_shipped_it(tags, late):
    assert overdue({"uqs up": "stable/a"}, tags) == late
