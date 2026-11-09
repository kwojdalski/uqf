"""Old spellings a rename keeps for one release (#1738 rule 6, #1729).

Each renamed option still parses under its old name, hidden from --help, and
logs the new spelling when used. The release after removes the row in
`renamed(...)` - and this file's case for it.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import typer
from plain_cli import PlainRunner

from uqs import cli
from uqs.cli import lists, renames

runner = PlainRunner()  # CI renders usage errors in colour

#: (command path, old spelling, new spelling): every rename still kept.
RENAMED = [
    # #1857: one vocabulary
    ("job new", "--procname", "--proc"),
    ("deploy check", "--procs", "--proc"),
    ("deploy check", "--deadline", "--verify-timeout"),
    ("job attachment contract approve", "--conflict", "--on-conflict"),
    ("job attachment contract approve", "--max-rows", "--max-message-rows"),
    ("job preview", "--sample", "--limit"),
    ("deploy build", "--output", "--output-dir"),
    ("deploy component build", "--output", "--output-dir"),
    ("deploy push", "--qcmd", "--q-cmd"),
    ("deploy push", "--qhome", "--q-home"),
    *[(f"setup odbc {verb}", "--home", "--odbc-home") for verb in ("rollback", "status", "env")],
    ("setup odbc install", "--qhome", "--q-home"),
    # #1855: one meaning per word
    *[
        (path, "--attach", "--attachment")
        for path in (
            "logs",
            "job backfill",
            "job receive",
            "job runs list",
            "job runs show",
            "job attachment contract approve",
            "job attachment contract list",
            "data check",
            "data alter",
        )
    ],
    ("job attachment add", "--stack-id", "--base-port"),
    ("job attachment add", "--runtime-only", "--transient"),
    ("deploy build", "--q-target", "--q-version"),
    ("deploy build", "--python-runtime", "--python-component"),
    *[
        ("deploy push", f"--{c}-runtime{sha}", f"--{c}-component{sha}")
        for c in ("q", "torq", "python")
        for sha in ("", "-sha256")
    ],
    ("target adopt", "--release", "--release-dir"),
    # #1890: one name per thing across surfaces
    ("data replay", "--dir", "--tplogdir"),
    ("logs", "--run", "--stamp"),
    ("deploy build", "--kafka-home", "--kfk-home"),
    ("deploy build", "--with-kafka-connector", "--kafka-connector"),
    ("connector start", "--topic", "--topics"),
    ("data replay", "--schema", "--schema-file"),
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
    assert option in _names(command)
    assert old not in _names(command), f"`{path} {old}` still has a meaning of its own"
    assert command.renames[old] == new


def test_old_spellings_are_rewritten_and_each_warned_about_once(monkeypatch):
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    table = {"--port": "--base-port", "--sample": "--limit"}
    got = renames._rewrite_renamed(
        ["rdb1", "--port", "7000", "--port=7001", "--sample", "5", "--", "--port"], table
    )
    assert got == ["rdb1", "--base-port", "7000", "--base-port=7001",
                   "--limit", "5", "--", "--port"]  # fmt: skip
    assert said == [("--port", "--base-port"), ("--sample", "--limit")]


def test_an_old_bypass_flag_becomes_an_override_with_a_reason_saying_none_was_given(monkeypatch):
    """#1852: `--allow-dirty` took no value. It is `--override allow-dirty`, and with no
    --reason on the line the record says the old flag gave none; with one, that covers it."""
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    table = {"--allow-dirty": renames.bypass_flag("allow-dirty")}
    got = renames._rewrite_renamed(["art.tgz", "--allow-dirty", "prod"], table)
    assert got == ["art.tgz", "--override", "allow-dirty", "prod",
                   "--reason", renames.legacy_reason("--allow-dirty")]  # fmt: skip
    assert said == [("--allow-dirty", "--override allow-dirty --reason REASON")]
    given = renames._rewrite_renamed(["--allow-dirty", "--reason", "hotfix"], table)
    assert given == ["--override", "allow-dirty", "--reason", "hotfix"]


def test_a_new_spelling_is_left_alone(monkeypatch):
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    args = ["--base-port", "7000", "--no-gateway"]
    assert renames._rewrite_renamed(args, {"--port": "--base-port"}) == args
    assert said == []


def test_a_kind_whose_one_release_is_over_is_refused_naming_its_noun(log_text):
    """`uqs list runtimes` -> `uqs runtime list` (#1728) kept one release; now
    refused, naming the command, and never offered as a kind (#2649)."""
    result = runner.invoke(cli.app, ["list", "runtimes"])
    assert result.exit_code == 2
    assert "`uqs list runtimes` is `uqs runtime list`" in log_text()
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
    first, second = (args + [None])[:2]
    assert shared._proc_first(first, second, port_given=False) == (proc, rest)


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


def test_sources_check_window_is_refused_naming_the_range(monkeypatch):
    """`--window N` was minutes; it was read as `--last Nm` for a release, and
    is refused now, naming the shared range that replaced it (#2249)."""
    from uqs.cli import source_check

    monkeypatch.setattr(source_check.live_check, "check", lambda *a, **k: pytest.fail("ran"))
    result = runner.invoke(cli.app, ["config", "sources", "check", "src1", "--window", "90"])
    assert result.exit_code == 2
    said = " ".join(re.sub("[│╭╮╰╯─]", " ", result.output).split())
    assert "`--window MINUTES` is now the shared time range: --last DURATION" in said


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


def test_list_overrides_is_config_list(monkeypatch):
    """`uqs list overrides` -> `uqs config list` (#1855): `override` is the
    checks a command bypasses (`uqs override list`), not process_overrides.csv."""
    from uqs.stack import listing

    rows = [{"procname": "rdb1", "field": "port", "value": "6061"}]
    monkeypatch.setitem(listing.LISTABLE_KINDS, "overrides", lambda *_a: rows)
    said = []
    monkeypatch.setattr(lists, "warn_renamed", lambda o, n: said.append((o, n)))
    old = runner.invoke(cli.app, ["list", "overrides", "--json"])
    new = runner.invoke(cli.app, ["config", "list", "--json"])
    assert old.exit_code == new.exit_code == 0, old.output
    assert old.stdout == new.stdout
    assert said == [("uqs list overrides", "uqs config list")]
    assert "overrides" not in runner.invoke(cli.app, ["list", "--json"]).stdout


def test_attach_still_names_the_attachment_end_to_end(monkeypatch):
    """`--attach NAME` keeps reaching the command, as `--attachment NAME`."""
    from uqs.cli import runs

    seen = []
    monkeypatch.setattr(runs, "_where", lambda attach: seen.append(attach) or {})
    monkeypatch.setattr(runs.stack_runs, "history", lambda *_a, **_k: [])
    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda o, n: said.append((o, n)))
    result = runner.invoke(cli.app, ["job", "runs", "list", "--attach", "pb-uat", "--json"])
    assert result.exit_code == 0, result.output
    assert seen == ["pb-uat"]
    assert said == [("--attach", "--attachment")]


def test_kind_external_is_kind_connector(monkeypatch):
    """`job new --kind external` -> `--kind connector` (#1890): the Python
    publisher outside q is a connector, as `uqs connector` names it."""
    from uqs.cli import create_kinds

    said = []
    monkeypatch.setattr(renames, "warn_renamed", lambda o, n: said.append((o, n)))
    assert create_kinds.canonical("external") == "connector"
    assert create_kinds.canonical("streaming") == "streaming"
    assert said == [("--kind external", "--kind connector")]


def test_a_renamed_environment_variable_is_refused_naming_its_new_name():
    from uqs.interpreter import removed_variables_problem

    said = removed_variables_problem({"UQS_REQUIRE_LIVE_SOURCES": "1"})
    assert said is not None and "UQF_LIVE_SOURCES_REQUIRED=1" in said
