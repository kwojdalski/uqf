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

The same parse holds two more places guidance is written (#1787): the help
`uqs ... --help` prints, read from the CLI tree itself, and the string literals
of the tree's Python - an error, a log line, a next step - other than
docstrings, which are prose for the reader of the code as a q comment is. A
Python string is prose more often than a q message is, and cuts a command off
mid-line (`uqs job backfill --attach` with its NAME in the next sentence), so
there the part that parses is what is judged: it must still name no alias, no
hidden command and no old option. The few places that name an old spelling on
purpose are KEPT, each with its reason, and each must still be needed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from uqs.cli import target_confirm

UQF_ROOT = Path(__file__).resolve().parents[3]
ROOTS = ("src", "scripts", "sidecars", "python/uqs/src/uqs")
#: The tree's own Python, whose string literals are read too.
PY_ROOTS = (
    "python/uqs/src",
    "python/uqf_frontend/src",
    "python/uqf_airflow_provider/src",
    "scripts",
    "sidecars",
)

#: (where, the old spelling as written) -> why it is named there on purpose.
#: `where` is a file under the tree, or `uqs <path> --help` for help text.
KEPT = {
    ("python/uqs/src/uqs/attach/persist.py", "job attach"): (
        "the marker comments written into an attached stack's own files: an "
        "existing attachment is re-applied by finding its block by this text"
    ),
    ("python/uqs/src/uqs/cli/attach_legacy.py", "job attach"): (
        "the refusal of a retired stage command, naming the command it refuses"
    ),
    ("python/uqs/src/uqs/cli/deploy_component.py", "deploy build-runtime"): (
        "the hidden command's own notice that it is renamed"
    ),
    ("python/uqs/src/uqs/deploy/selection.py", "deploy verify"): (
        "the fallback for a release from before `deploy check`, which has only "
        "`deploy verify`: a rollback's target"
    ),
    ("uqs job runs list --help", "job runs status"): "the migration notice in its help",
}

#: A q string literal, escapes included.
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
#: `uqs`, then the words up to whatever ends the command in prose.
_COMMAND = re.compile(r"\buqs((?: +[^\s`'\"(),;:]+)+)")


def q_files() -> list[Path]:
    return sorted(p for root in ROOTS for p in (UQF_ROOT / root).rglob("*.q"))


def commands_in(literal: str) -> list[list[str]]:
    """The words after `uqs` of each command `literal` names."""
    known = set(target_confirm.subcommands(target_confirm.root_command()))
    found = []
    for match in _COMMAND.finditer(literal):
        words = match.group(1).split()
        words[-1] = words[-1].rstrip(".")  # a sentence's full stop
        if words[0] in known or words[0].startswith("-"):
            found.append(words)
    return found


def references(text: str) -> list[tuple[int, list[str]]]:
    """(line, the words after `uqs`) for each command a string literal on a
    non-comment line of `text` names."""
    return [
        (n, words)
        for n, line in enumerate(text.splitlines(), 1)
        if not line.lstrip().startswith("/")
        for literal in _STRING.findall(line)
        for words in commands_in(literal)
    ]


def python_strings(text: str) -> list[tuple[int, str]]:
    """(line, text) of each string literal in Python source `text` that is not
    a docstring. An f-string's replacement fields read as NAME."""
    tree = ast.parse(text)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in node.values)
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            skip.add(id(body[0].value))  # a docstring, or a bare expression
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and id(node) not in skip:
            parts = (str(v.value) if isinstance(v, ast.Constant) else "NAME" for v in node.values)
            found.append((node.lineno, "".join(parts)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip:
                found.append((node.lineno, node.value))
    return sorted(found)


def python_references(text: str) -> list[tuple[int, list[str]]]:
    """(line, the words after `uqs`) for each command a non-docstring string
    literal of Python source `text` names."""
    return [(n, words) for n, s in python_strings(text) for words in commands_in(s)]


def help_texts() -> list[tuple[str, str]]:
    """(`uqs <path> --help`, text) for the help of every command, group and
    option `--help` shows: hidden ones are not shown, so not read."""
    found = []

    def walk(cmd, path):
        if getattr(cmd, "hidden", False):
            return
        where = " ".join(("uqs", *path, "--help"))
        texts = [getattr(cmd, a, None) for a in ("help", "short_help", "epilog")]
        texts += [getattr(p, "help", None) for p in cmd.params if not getattr(p, "hidden", False)]
        found.extend((where, t) for t in texts if t)
        for name, sub in target_confirm.subcommands(cmd).items():
            walk(sub, (*path, name))

    walk(target_confirm.root_command(), ())
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
    # An option kept hidden for a release (renames.old_spelling, a retired
    # flag that now refuses) parses too, and is going as surely.
    hidden = {o for p in parsed.ctx.command.params if getattr(p, "hidden", False) for o in p.opts}
    retired = [w.partition("=")[0] for w in after if w.partition("=")[0] in hidden]
    if retired:
        return f"{', '.join(retired)} is a hidden option, kept only for its old users"
    return None


def old_spelling(words: list[str]) -> str | None:
    """Why `uqs <words>` names a spelling that is going, or None: `problem`,
    applied to the longest leading part of `words` that parses, since prose
    cuts a command short. Nothing that parses is not judged here."""
    for k in range(len(words), 0, -1):
        if target_confirm.parse(words[:k]) is not None:
            return problem(words[:k])
    return None


def _kept(where: str, words: list[str]) -> bool:
    written = " ".join(words)
    return any(w == where and (written + " ").startswith(old + " ") for w, old in KEPT)


def python_findings() -> list[tuple[str, int, list[str], str]]:
    """(file, line, words, why) for each old spelling the tree's Python names."""
    return [
        (str(path.relative_to(UQF_ROOT)), n, words, why)
        for root in PY_ROOTS
        for path in sorted((UQF_ROOT / root).rglob("*.py"))
        for n, words in python_references(path.read_text(errors="replace"))
        if (why := old_spelling(words)) is not None
    ]


def help_findings() -> list[tuple[str, list[str], str]]:
    """(where, words, why) for each old spelling `--help` shows."""
    return [
        (where, words, why)
        for where, text in help_texts()
        for words in commands_in(text)
        if (why := old_spelling(words)) is not None
    ]


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


# ------------------------------------------------- Python and --help (#1787)


def test_every_command_the_python_names_is_canonical():
    bad = [
        f"{where}:{n}: `uqs {' '.join(words)}` {why}"
        for where, n, words, why in python_findings()
        if not _kept(where, words)
    ]
    assert bad == [], "\n".join(bad)


def test_every_command_the_help_names_is_canonical():
    bad = [
        f"{where}: `uqs {' '.join(words)}` {why}"
        for where, words, why in help_findings()
        if not _kept(where, words)
    ]
    assert bad == [], "\n".join(bad)


def test_every_kept_old_spelling_is_still_named_where_it_is_kept():
    """A KEPT entry the tree no longer needs is removed, so the list cannot
    excuse a new reference in the same file."""
    named = {(w, " ".join(words)) for w, _, words, _ in python_findings()}
    named |= {(w, " ".join(words)) for w, words, _ in help_findings()}
    unused = [
        key for key in KEPT
        if not any(w == key[0] and (s + " ").startswith(key[1] + " ") for w, s in named)
    ]  # fmt: skip
    assert unused == []


def test_the_python_scan_finds_the_messages_it_exists_for():
    """Not vacuous: the tree's Python and help name commands this test reads."""
    named = {
        " ".join(w)
        for root in PY_ROOTS
        for p in (UQF_ROOT / root).rglob("*.py")
        for _, w in python_references(p.read_text(errors="replace"))
    }
    assert {"--target NAME status", "job checkpoint remove"} <= named
    helped = {" ".join(w) for _, text in help_texts() for w in commands_in(text)}
    assert "job runs status" in helped


def test_a_python_string_is_read_and_a_docstring_is_not():
    text = (
        '"""`uqs summary` was its name."""\n'
        "def f(target):\n"
        '    """`uqs summary` here too."""\n'
        '    raise UqsError(f"failed - `uqs --target {target} summary` shows")\n'
        'HINT = "run `uqs job runs status`"\n'
    )
    assert python_references(text) == [
        (4, ["--target", "NAME", "summary"]),
        (5, ["job", "runs", "status"]),
    ]


@pytest.mark.parametrize(
    ("words", "why"),
    [
        (["--target", "NAME", "summary"], "old spelling of `uqs status`"),
        (["data", "check", "--fix"], "--fix is a hidden option"),
        (["job", "backfill", "--debug", "--attach"], "--debug is an old spelling"),
        (["deploy", "verify", "NAME"], "old spelling of `uqs deploy check`"),
    ],
)
def test_prose_cut_short_is_judged_by_what_parses(words, why):
    assert why in (old_spelling(words) or "")


@pytest.mark.parametrize(
    "words",
    [
        ["job", "backfill", "--attach"],  # its NAME in the next sentence
        ["--target", "NAME", "<command>"],
        ["job", "new", "named", "it"],
        ["status", "--sort"],
    ],
)
def test_prose_that_names_a_canonical_command_passes(words):
    assert old_spelling(words) is None
