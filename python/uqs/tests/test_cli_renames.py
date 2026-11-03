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
    ("start", "--print", "--dry-run"),
    ("up", "--port", "--base-port"),
    ("stop", "--port", "--base-port"),
    ("stop", "--force", "--kill"),
    ("restart", "--port", "--base-port"),
    ("restart", "--print", "--dry-run"),
    ("restart", "--show-command", "--dry-run"),
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
    ("job backfill", "--force", renames.bypass_flag("any-writer")),
    ("job receive", "--port", "--listen-port"),
    ("job remove", "--force", renames.bypass_flag("even-if-edited")),
    # #1732: one safety vocabulary
    ("start", "--show-command", "--dry-run"),
    ("job attach", "--override-reason", "--reason"),
    ("job preview", "--dry-run", renames.DROPPED),
    # #1918: start's --skip-...-check are generated aliases now, not old spellings
    *[
        ("deploy push", f"--{name}", ("--override", name))
        for name in (
            "accept-digest",
            "accept-q-version",
            "allow-unpinned",
            "accept-converted-release",
        )
    ],
    # #1857: one vocabulary
    ("job new", "--procname", "--proc"),
    ("deploy check", "--procs", "--proc"),
    ("deploy check", "--deadline", "--verify-timeout"),
    ("deploy verify", "--deadline", "--verify-timeout"),
    ("job attachment contract approve", "--conflict", "--on-conflict"),
    ("job attachment contract approve", "--max-rows", "--max-message-rows"),
    ("job preview", "--sample", "--limit"),
    ("deploy build", "--output", "--output-dir"),
    ("deploy component build", "--output", "--output-dir"),
    ("deploy push", "--qcmd", "--q-cmd"),
    ("deploy push", "--qhome", "--q-home"),
    *[(f"setup odbc {verb}", "--home", "--odbc-home") for verb in ("rollback", "status", "env")],
    ("setup odbc install", "--qhome", "--q-home"),
    # #1852: the bare bypass flags, each now an --override with a reason
    *[
        (path, f"--{name}", renames.bypass_flag(name))
        for path, name in [
            ("deploy build", "allow-dirty"),
            ("deploy build", "allow-computed-names"),
            ("deploy push", "allow-dirty"),
            ("deploy push", "fix-hdb"),
            ("deploy push", "break-lock"),
            ("deploy rollback", "break-lock"),
            ("job backfill", "any-writer"),
            ("job remove", "even-if-edited"),
        ]
    ],
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
    option = new[0] if isinstance(new, tuple) else new  # a bypass: its --override
    assert option == renames.DROPPED or option in _names(command)
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


def test_an_old_bypass_becomes_an_override_with_one_reason(monkeypatch):
    """`--skip-X REASON` and `--accept-Y REASON` are `--override NAME`s; their
    reasons become the one --reason (#1732)."""
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append(old))
    table = {"--skip-a": ("--override", "a"), "--skip-b": ("--override", "b")}
    got = renames._rewrite_renamed(
        ["rdb1", "--skip-a", "why a", "--skip-b=why b"], table, "uqs start", None
    )
    assert got == ["rdb1", "--override", "a", "--override", "b", "--reason", "why a; why b"]
    assert said == ["--skip-a", "--skip-b"]


def test_an_old_bypass_flag_becomes_an_override_with_a_reason_saying_none_was_given(monkeypatch):
    """#1852: `--allow-dirty` took no value. It is `--override allow-dirty`, and with no
    --reason on the line the record says the old flag gave none; with one, that covers it."""
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    table = {"--allow-dirty": renames.bypass_flag("allow-dirty")}
    got = renames._rewrite_renamed(
        ["art.tgz", "--allow-dirty", "prod"], table, "uqs deploy push", None
    )
    assert got == ["art.tgz", "--override", "allow-dirty", "prod",
                   "--reason", renames.legacy_reason("--allow-dirty")]  # fmt: skip
    assert said == [("--allow-dirty", "--override allow-dirty --reason REASON")]
    given = renames._rewrite_renamed(
        ["--allow-dirty", "--reason", "hotfix"], table, "uqs deploy push", None
    )
    assert given == ["--override", "allow-dirty", "--reason", "hotfix"]


def test_a_dropped_flag_is_accepted_and_ignored():
    """`job preview --dry-run`: a preview never wrote, so the flag goes."""
    got = renames._rewrite_renamed(["feed", "--dry-run"], {"--dry-run": renames.DROPPED}, "x", None)
    assert got == ["feed"]


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


@pytest.mark.parametrize("words", [["job", "runs", "upgrade"], ["job", "runs", "migrate"]])
def test_the_ledger_upgrade_runs_at_both_spellings_and_the_old_names_the_new(monkeypatch, words):
    """`job runs migrate` -> `job runs upgrade` (#1737): the run ledger's own
    upgrade, no longer spelled like `data migrate`'s HDB schema migration."""
    from uqs.stack import runs as stack_runs

    monkeypatch.setattr(stack_runs, "migrate", lambda paths, dry_run=False: 0)
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda o, n: said.append(n))
    result = runner.invoke(cli.app, words)
    assert result.exit_code == 0, result.output
    assert "already current" in result.output
    assert said == ([] if words[-1] == "upgrade" else ["uqs job runs upgrade"])


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("summary", "status"),
        ("data hdb-check", "data check"),
        ("data hdb-alter", "data alter"),
        ("config get", "config show"),
        ("deploy verify", "deploy check"),
    ],
)
def test_a_renamed_verb_keeps_its_old_name_hidden(old, new):
    """#1733: one meaning per verb; each old name stays one release, hidden,
    taking exactly the new command's options."""
    assert _command(old).hidden and not _command(new).hidden
    assert _names(_command(old)) == _names(_command(new))


def test_job_runs_status_is_list_unfinished(monkeypatch):
    from uqs.stack import runs as stack_runs

    monkeypatch.setattr(stack_runs, "unfinished", lambda *_a, **_k: [])
    monkeypatch.setattr(stack_runs, "history", lambda *_a, **_k: pytest.fail("the history"))
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda o, n: said.append(n))
    old = runner.invoke(cli.app, ["job", "runs", "status", "--json"])
    new = runner.invoke(cli.app, ["job", "runs", "list", "--unfinished", "--json"])
    assert old.exit_code == new.exit_code == 0, old.output
    assert old.stdout == new.stdout
    assert said == ["uqs job runs list --unfinished"]


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
    assert "runtimes" not in kinds and "processes" not in kinds


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


def test_a_duration_is_seconds_bare_or_with_a_unit():
    """#1857: every span of time is a DURATION; a bare number stays seconds,
    as every duration option took before."""
    from uqs.cli import vocabulary

    assert vocabulary.seconds("30") == 30.0
    assert vocabulary.seconds("500ms") == 0.5
    assert vocabulary.seconds("5m") == 300.0
    assert vocabulary.whole_seconds("1.2") == 2
    with pytest.raises(typer.BadParameter, match="a duration is a number of seconds"):
        vocabulary.seconds("soon")


def test_sources_check_window_minutes_become_last(monkeypatch):
    """`--window N` was minutes; it is `--last Nm` now, read and converted."""
    from uqs.cli import source_check

    seen = {}
    monkeypatch.setattr(source_check.live_check, "check", lambda *a, **k: seen.update(k) or [])
    said = []
    monkeypatch.setattr(source_check, "warn_renamed", lambda o, n: said.append((o, n)))
    result = runner.invoke(cli.app, ["config", "sources", "check", "src1", "--window", "90"])
    assert result.exit_code == 0, result.output
    assert seen["window_minutes"] == 90
    assert said == [("--window 90", "--last 90m")]
    runner.invoke(cli.app, ["config", "sources", "check", "src1", "--last", "2h"])
    assert seen["window_minutes"] == 120


def test_seed_overwrite_is_on_conflict_replace(monkeypatch):
    """`data seed --overwrite` -> `--on-conflict replace` (#1857)."""
    from uqs.cli import seed

    said = []
    monkeypatch.setattr(seed, "warn_renamed", lambda o, n: said.append((o, n)))
    result = runner.invoke(cli.app, ["data", "seed", "--overwrite", "--from-runtime", "nope"])
    assert said == [("--overwrite", "--on-conflict replace")], result.output


def test_alter_rename_is_old_colon_new_and_the_old_equals_still_parses(monkeypatch):
    """`data alter --rename OLD:NEW`, as `data migrate` spells it (#1856)."""
    from uqs.cli import hdb_alter

    said = []
    monkeypatch.setattr(hdb_alter, "warn_renamed", lambda o, n: said.append(n))
    assert hdb_alter._old_rename("amt:amount") == "amt=amount"
    assert hdb_alter._old_rename("amt=amount") == "amt=amount"
    assert said == ["--rename amt:amount"]


def test_tables_are_a_repeatable_table_and_a_comma_list_works_in_one():
    from uqs.cli.hdb_scope import tables_given

    assert tables_given(["trade", "quote,fx"]) == ["trade", "quote", "fx"]
    assert tables_given(None) == []
