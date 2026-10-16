"""The `uqs` command groups (#1417): job operations under `job`, the machine's
setup under `setup`, external publishers under `connector` - and the old
top-level paths gone, with nothing kept to forward them.

What each moved command does is held by its own tests (test_backfill,
test_gaps, test_cli_runs, test_stream_preview, test_cli's connector tests,
test_odbc_home), which call it at its new path. These hold the shape.
"""

from __future__ import annotations

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from uqs import cli

runner = CliRunner()
REMOVED = ("odbc", "stream", "gaps", "run", "backfill", "feed")


def _group(*path: str) -> TyperGroup:
    """The registered group at `path`, read off the Typer object rather than
    a rendered help screen, whose boxes and width depend on the terminal."""
    group = typer.main.get_command(cli.app)
    for name in path:
        assert isinstance(group, TyperGroup)
        group = group.commands[name]
    assert isinstance(group, TyperGroup)
    return group


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ((), {"job", "setup", "connector"}),
        (("job",), {"new", "remove", "install", "backfill", "preview", "gaps", "runs", "attach"}),
        (("job", "attach"), {"plan", "validate", "status", "apply", "verify", "start"}),
        (("job", "runs"), {"status", "list", "show", "audit", "migrate"}),
        (("setup",), {"odbc"}),
        (("setup", "odbc"), {"install", "status", "env", "rollback"}),
        (("connector",), {"start", "stop", "status"}),
    ],
)
def test_every_moved_command_is_at_its_new_path(path, expected):
    assert expected <= set(_group(*path).commands)


def test_the_old_top_level_paths_are_not_commands():
    assert not set(REMOVED) & set(_group().commands)


def test_root_help_shows_the_groups_and_none_of_the_old_paths():
    root = _group()
    assert not any(root.commands[name].hidden for name in ("job", "setup", "connector"))
    assert not set(REMOVED) & set(root.commands)
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0


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
    assert f"No such command '{argv[0]}'" in " ".join(result.output.split())


def test_job_help_keeps_authoring_apart_from_operations():
    panels = {
        name: getattr(cmd, "rich_help_panel", None) for name, cmd in _group("job").commands.items()
    }
    assert {n for n, p in panels.items() if p == "Authoring"} == {"new", "remove", "install"}
    assert {n for n, p in panels.items() if p == "Operations"} == {
        "backfill",
        "preview",
        "gaps",
        "runs",
        "attach",
    }


def test_setup_help_says_it_is_infrastructure():
    assert (_group().commands["setup"].help or "").startswith("Infrastructure setup")


def test_connector_help_says_it_supervises_external_publishers_not_torq_processes():
    text = _group().commands["connector"].help or ""
    assert "supervise external processes, not the TorQ-managed q processes" in text
