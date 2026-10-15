"""The `uqs` command groups (#1417): job operations under `job`, the machine's
setup under `setup`, external publishers under `connector` - and the old
top-level paths gone, with nothing kept to forward them.

What each moved command does is held by its own tests (test_backfill,
test_gaps, test_cli_runs, test_stream_preview, test_cli's connector tests,
test_odbc_home), which call it at its new path. These hold the shape.
"""

from __future__ import annotations

import re

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from uqs import cli

runner = CliRunner()
REMOVED = ("odbc", "stream", "gaps", "run", "backfill", "feed")


def _commands(*path: str) -> set[str]:
    group = typer.main.get_command(cli.app)
    ctx = typer.Context(group)
    for name in path:
        assert isinstance(group, TyperGroup)
        found = group.get_command(ctx, name)
        assert found is not None, f"no command {name!r}"
        group, ctx = found, typer.Context(found, parent=ctx)
    assert isinstance(group, TyperGroup)
    return set(group.list_commands(ctx))


def _listed(help_text: str) -> set[str]:
    """The command names a help screen's panels list - the first word of each
    row, not every word, since descriptions mention feeds and runs."""
    return set(re.findall(r"^│ ([a-z][\w-]*)  ", help_text, re.M))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ((), {"job", "setup", "connector"}),
        (("job",), {"new", "remove", "install", "backfill", "preview", "gaps", "runs"}),
        (("job", "runs"), {"status", "list", "show", "audit", "migrate"}),
        (("setup",), {"odbc"}),
        (("setup", "odbc"), {"install", "status", "env", "rollback"}),
        (("connector",), {"start", "stop", "status"}),
    ],
)
def test_every_moved_command_is_at_its_new_path(path, expected):
    assert expected <= _commands(*path)


def test_the_old_top_level_paths_are_not_commands():
    assert not set(REMOVED) & _commands()


def test_root_help_lists_the_groups_and_none_of_the_old_paths():
    result = runner.invoke(cli.app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    listed = _listed(result.stdout)
    assert {"job", "setup", "connector"} <= listed
    assert not set(REMOVED) & listed


@pytest.mark.parametrize(
    "argv",
    [
        ["odbc", "status"],
        ["stream", "preview", "vectorize2"],
        ["gaps", "demo_markout", "--from", "2026-09-13", "--to", "2026-09-14"],
        ["run", "list"],
        ["backfill", "demo_deals_backfill", "--from", "2026-09-13", "--to", "2026-09-14"],
        ["feed", "status"],
    ],
)
def test_an_old_path_is_refused_as_no_such_command(argv):
    result = runner.invoke(cli.app, argv)
    assert result.exit_code != 0
    assert f"No such command '{argv[0]}'" in result.output


def test_job_help_keeps_authoring_apart_from_operations():
    result = runner.invoke(cli.app, ["job", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    authoring, operations = result.stdout.split("Authoring")[1], result.stdout.split("Authoring")[0]
    assert "Operations" in operations
    assert _listed(operations) >= {"backfill", "preview", "gaps", "runs"}
    assert _listed(authoring) == {"new", "remove", "install"}


def test_setup_help_says_it_is_infrastructure():
    result = runner.invoke(cli.app, ["setup", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "Infrastructure setup" in result.stdout


def test_connector_help_says_it_supervises_external_publishers_not_torq_processes():
    result = runner.invoke(cli.app, ["connector", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    text = " ".join(result.stdout.split())
    assert "supervise external processes, not the TorQ-managed q processes" in text
