"""Old spellings a rename keeps for one release (#1738 rule 6, #1729).

Each renamed option still parses under its old name, hidden from --help, and
logs the new spelling when used. The release after removes the row in
`renamed(...)` - and this file's case for it.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import lists, renames
from uqs.stack import multitail as stack_multitail

runner = CliRunner()

#: (command path, old spelling, new spelling): every rename #1729 made.
RENAMED = [
    ("start", "--port", "--base-port"),
    ("start", "--print", "--show-command"),
    ("up", "--port", "--base-port"),
    ("stop", "--port", "--base-port"),
    ("stop", "--force", "--kill"),
    ("restart", "--port", "--base-port"),
    ("restart", "--print", "--show-command"),
    ("exec", "--port", "--base-port"),
    ("raw", "--port", "--base-port"),
    ("summary", "--port", "--base-port"),
    ("graph", "--port", "--base-port"),
    ("list", "--port", "--base-port"),
    ("logs", "-n", "--lines"),
    ("logs", "--print", "--show-command"),
    ("query", "--raw", "--no-gateway"),
    ("query", "--target", "--tunnel"),
    ("config get", "--port", "--base-port"),
    ("config get", "--raw", "--unresolved"),
    ("data replay", "--port", "--base-port"),
    ("connector start", "--port", "--base-port"),
    ("connector restart", "--port", "--base-port"),
    ("deploy verify", "--port", "--base-port"),
    ("job backfill", "--port", "--base-port"),
    ("job backfill", "--force", "--any-writer"),
    ("job receive", "--port", "--listen-port"),
    ("job remove", "--force", "--even-if-edited"),
]


def _command(path: str):
    command: Any = typer.main.get_command(cli.app)
    for word in path.split():
        command = command.commands[word]
    return command


def _names(command) -> set[str]:
    return {opt for p in command.params for opt in (*p.opts, *p.secondary_opts)}


@pytest.mark.parametrize(("path", "old", "new"), RENAMED)
def test_the_old_spelling_is_hidden_and_maps_to_the_new(path, old, new):
    command = _command(path)
    assert new in _names(command)
    assert old not in _names(command), f"`{path} {old}` still has a meaning of its own"
    assert command.renames[old] == new


@pytest.mark.parametrize("path", ["summary", "job backfill"])
def test_a_commands_own_debug_is_the_global_one(path):
    assert "--debug" not in _names(_command(path))
    assert _command(path).renames["--debug"] == "--debug"


def test_old_spellings_are_rewritten_and_each_warned_about_once(monkeypatch):
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    table = {"--port": "--base-port", "-n": "--lines"}
    got = renames._rewrite_renamed(
        ["rdb1", "--port", "7000", "--port=7001", "-n20", "-n", "5", "--", "--port"],
        table,
        "uqs logs",
        None,
    )
    assert got == ["rdb1", "--base-port", "7000", "--base-port=7001",
                   "--lines", "20", "--lines", "5", "--", "--port"]  # fmt: skip
    assert said == [("--port", "--base-port"), ("-n", "--lines")]


def test_a_new_spelling_is_left_alone(monkeypatch):
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    args = ["--base-port", "7000", "--no-gateway"]
    assert renames._rewrite_renamed(args, {"--port": "--base-port"}, "uqs x", None) == args
    assert said == []


def test_the_old_spelling_still_works_end_to_end(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        stack_multitail,
        "multitail_command",
        lambda paths, procs, **kw: seen.update(kw) or ["multitail", "x"],
    )
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    result = runner.invoke(cli.app, ["logs", "--multitail", "--print", "-n", "7"])
    assert result.exit_code == 0, result.output
    assert seen["lines"] == 7
    assert "multitail x" in result.stdout
    assert said == [("--print", "--show-command"), ("-n", "--lines")]


@pytest.mark.parametrize(
    ("old", "new", "target"),
    [
        (["remove", "output", "--dry-run"], "uqs clean", "clean"),
        (["remove", "checkpoint", "w"], "uqs job checkpoint remove", "clear_checkpoint"),
    ],
)
def test_a_moved_command_still_runs_at_its_old_path(monkeypatch, old, new, target):
    """The old `uqs remove ...` group (#1727), hidden, logging the new path."""
    from uqs.stack import backfill as stack_backfill
    from uqs.stack import clean as stack_clean

    module = stack_clean if target == "clean" else stack_backfill
    returns = [] if target == "clean" else None
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return returns

    monkeypatch.setattr(module, target, fake)
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda o, n: said.append(n))
    result = runner.invoke(cli.app, old)
    assert result.exit_code == 0, result.output
    assert calls and said == [new]
    assert _command("remove").hidden


def test_a_kind_that_moved_to_its_noun_still_lists_under_list(monkeypatch):
    """`uqs list runtimes` -> `uqs runtime list` (#1728), for one release."""
    from uqs.stack import listing

    monkeypatch.setitem(listing.LISTABLE_KINDS, "runtimes", lambda *_a: [{"runtime": "uqf"}])
    said = []
    monkeypatch.setattr(lists, "warn_renamed", lambda o, n: said.append((o, n)))
    old = runner.invoke(cli.app, ["list", "runtimes", "--json"])
    new = runner.invoke(cli.app, ["runtime", "list", "--json"])
    assert old.exit_code == new.exit_code == 0, old.output
    assert old.stdout == new.stdout
    assert said == [("uqs list runtimes", "uqs runtime list")]
    kinds = runner.invoke(cli.app, ["list", "--json"]).stdout
    assert "runtimes" not in kinds and "processes" in kinds


@pytest.mark.parametrize(
    ("args", "proc", "rest"),
    [
        (["rdb1"], "rdb1", None),  # names a process: PROC
        (["count quote"], None, "count quote"),  # does not: the expression
        (["rdb1", "count quote"], "rdb1", "count quote"),
    ],
)
def test_a_lone_word_is_proc_only_when_it_names_a_process(monkeypatch, args, proc, rest):
    from uqs.cli import shared

    monkeypatch.setattr(shared, "_known_procs", lambda: {"rdb1", "gateway1"})
    ctx = typer.Context(typer.main.get_command(cli.app))
    first, second = (args + [None])[:2]
    assert shared._proc_first(ctx, first, second, port_given=False) == (proc, rest)


def test_the_old_proc_option_names_the_process_whatever_it_is(monkeypatch):
    """`--proc nope1` was a process name, so it is refused as one rather than
    guessed to be an expression."""
    from uqs.cli import shared

    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda o, n: said.append((o, n)))
    monkeypatch.setattr(shared, "_known_procs", lambda: {"rdb1"})
    ctx = typer.Context(_command("query"))
    args = _command("query").renames
    moved = ctx.meta.setdefault(renames.MOVED_KEY, [])
    got = renames._rewrite_renamed(
        ["count quote", "--proc", "nope1"], args, "uqs query", None, moved
    )
    assert got == ["nope1", "count quote"] and moved == ["nope1"]
    assert shared._proc_first(ctx, "nope1", None, port_given=False) == ("nope1", None)
    assert said == [("--proc", "uqs query PROC")]
