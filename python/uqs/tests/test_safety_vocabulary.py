"""One safety vocabulary (#1732): --dry-run/-n, --yes/-y, --override NAME --reason.

test_cli_conventions.py holds the tree to the flags; this checks what a few of
the new dry runs and refusals do."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import safety
from uqs.paths import UqsError
from uqs.stack import hdb_migrate
from uqs.stack import procs as stack_procs

runner = CliRunner()


def test_given_refuses_an_unknown_name_and_a_reason_alone():
    with pytest.raises(
        UqsError, match="has no override 'nope'.*takes: profile-budget-check, q-version-check"
    ):
        safety.given("restart", ["nope"], "x")
    with pytest.raises(UqsError, match="none was given"):
        safety.given("start", None, "a reason with nothing to excuse")
    assert safety.given("start", ["q-version-check"], " old q ") == {"q-version-check": "old q"}


def test_override_list_names_each_command_and_what_it_bypasses():
    rows = {b.name: b for b in safety.catalog()}
    assert rows["profile-budget-check"].commands == ("start", "restart")
    assert rows["accept-digest"].commands == ("deploy push",)
    assert (
        "torq-version:COMPONENT" in rows
        and "job attachment add" in rows["torq-version:COMPONENT"].commands
    )


def test_data_migrate_is_dry_unless_apply_which_asks(monkeypatch, terminal):
    """#1854: dry by default, and it stays so - the default no longer flips. --apply
    shows the plan and asks; --yes does not ask; -n is no option of it (#2752)."""
    calls = []
    monkeypatch.setattr(hdb_migrate, "migrate", lambda *a, **kw: calls.append(kw) or "plan")
    argv = ["data", "migrate", "trade", "--date", "2026-09-01"]
    assert runner.invoke(cli.app, argv).exit_code == 0
    assert [c["apply"] for c in calls] == [False]
    assert runner.invoke(cli.app, [*argv, "-n"]).exit_code == 2
    calls.clear()
    declined = runner.invoke(cli.app, [*argv, "--apply"], input="n\n")
    assert declined.exit_code == 1 and [c["apply"] for c in calls] == [False]
    calls.clear()
    asked = runner.invoke(cli.app, [*argv, "--apply"], input="y\n")
    assert asked.exit_code == 0 and [c["apply"] for c in calls] == [False, True]
    calls.clear()
    ok = runner.invoke(cli.app, [*argv, "--apply", "--yes"])
    assert ok.exit_code == 0 and [c["apply"] for c in calls] == [True]


def test_config_set_dry_run_checks_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(stack_procs, "list_process_names", lambda _p: ["rdb1"])
    monkeypatch.setattr(stack_procs, "_write_overrides", lambda *_a: pytest.fail("wrote"))
    result = runner.invoke(cli.app, ["config", "set", "rdb1", "startwithall=1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would set rdb1.startwithall = 1" in result.output
    refused = runner.invoke(cli.app, ["config", "set", "rdb1", "nosuchfield=1", "-n"])
    assert refused.exit_code == 1, "a dry run is checked as a real one is"
