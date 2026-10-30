"""The `uqs` commands q's messages tell an operator to run are today's (#1784).

A q error or log line that says "run `uqs remove checkpoint W`" is guidance
someone types, and nothing held it to the CLI: commands renamed in #1733 and
#1737 keep their old paths for one release as hidden aliases, so the old
spelling still ran - and still taught the name about to go.

This reads every q string literal in the tree's own q (src/, scripts/,
sidecars/, the q uqs ships) for `uqs ...` and parses each with the CLI's own
parser, target_confirm.parse - Click's, run without any callback - requiring
that it names a command or group that is not hidden, by its own path rather
than an alias's, with no option it accepts only as an old spelling.

What counts as a reference: `uqs` followed by a word the CLI knows (a command
or group, hidden or not) or an option, up to the backtick, quote, bracket or
punctuation that ends it. "the token uqs writes" is prose, not a command, and a
misspelt command name (`uqs sumary`) is not recognised as one - this guards
renames, not typos. Comments are not scanned: they are prose for the reader
of the file, and tests/ is not either - its strings describe uqs, they do not
instruct anyone. python/uqs/src/uqs/cli/renames.py's own migration notices
are made from the rename tables, not written in q.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uqs.cli import target_confirm

UQF_ROOT = Path(__file__).resolve().parents[3]
ROOTS = ("src", "scripts", "sidecars", "python/uqs/src/uqs")

#: A q string literal, escapes included.
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
#: `uqs`, then the words up to whatever ends the command in prose.
_COMMAND = re.compile(r"\buqs((?: +[^\s`'\"(),;:]+)+)")


def q_files() -> list[Path]:
    return sorted(p for root in ROOTS for p in (UQF_ROOT / root).rglob("*.q"))


def references(text: str) -> list[tuple[int, list[str]]]:
    """(line, the words after `uqs`) for each command a string literal on a
    non-comment line of `text` names."""
    known = set(target_confirm.subcommands(target_confirm.root_command()))
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("/"):
            continue
        for literal in _STRING.findall(line):
            for match in _COMMAND.finditer(literal):
                words = match.group(1).split()
                words[-1] = words[-1].rstrip(".")  # a sentence's full stop
                if words[0] in known or words[0].startswith("-"):
                    found.append((n, words))
    return found


def problem(words: list[str]) -> str | None:
    """Why `uqs <words>` is not a canonical command line, or None."""
    parsed = target_confirm.parse(words)
    if parsed is None:
        return "does not parse: no such command, or an option it does not take"
    _, alias = target_confirm.canonical(parsed.ctx.command)
    if alias is not None:
        return f"is the old spelling of `uqs {' '.join(alias)}`"
    path = parsed.path
    for depth in range(1, len(path) + 1):
        if getattr(target_confirm.find(path[:depth]), "hidden", False):
            return f"`uqs {' '.join(path[:depth])}` is hidden"
    # The command's own options follow its name; one before it is the root's
    # (`uqs --debug status`), which the command's renames do not cover.
    after = words[words.index(path[-1]) + 1 :] if path else words
    renames = getattr(parsed.ctx.command, "renames", None) or {}
    old = [w.partition("=")[0] for w in after if w.partition("=")[0] in renames]
    if old:
        return f"{', '.join(old)} is an old spelling: {renames[old[0]]}"
    return None


def test_every_command_a_q_message_names_is_canonical():
    bad = [
        f"{path.relative_to(UQF_ROOT)}:{n}: `uqs {' '.join(words)}` {why}"
        for path in q_files()
        for n, words in references(path.read_text(errors="replace"))
        if (why := problem(words)) is not None
    ]
    assert bad == [], "\n".join(bad)


def test_the_scan_finds_the_messages_it_exists_for():
    """Not vacuous: the tree's messages name commands this test reads."""
    named = {" ".join(w) for p in q_files() for _, w in references(p.read_text(errors="replace"))}
    assert {"job checkpoint remove", "job runs upgrade", "status"} <= named


@pytest.mark.parametrize(
    ("words", "why"),
    [
        (["remove", "checkpoint", "w"], "old spelling of `uqs job checkpoint remove`"),
        (["summary"], "old spelling of `uqs status`"),
        (["data", "hdb-check"], "old spelling of `uqs data check`"),
        (["job", "runs", "status"], "old spelling of `uqs job runs list`"),
        (["start", "--port", "6000"], "--port is an old spelling"),
        (["query", "--proc", "rdb1", "x"], "--proc is an old spelling"),
        (["status", "--no-such-flag"], "does not parse"),
    ],
)
def test_an_old_or_unknown_spelling_is_refused(words, why):
    assert why in (problem(words) or "")


@pytest.mark.parametrize(
    "words",
    [
        ["job", "checkpoint", "remove", "demo_deals_backfill"],
        ["status"],
        ["data", "check", "--attach", "NAME", "--apply"],
        ["start", "--profile", "crypto"],
        ["connector", "start", "kafka"],
        ["--debug", "status"],
    ],
)
def test_a_canonical_spelling_passes(words):
    assert problem(words) is None


def test_a_reference_is_cut_where_the_prose_ends():
    text = 'x:"run `uqs job runs upgrade` once (uqs job backfill --fixture), as uqs writes"'
    assert references(text) == [
        (1, ["job", "runs", "upgrade"]),
        (1, ["job", "backfill", "--fixture"]),
    ]
    assert references('/ a comment: "uqs summary"') == []
