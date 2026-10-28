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
        "TORQ_VERSION:COMPONENT" in rows
        and "job attachment add" in rows["TORQ_VERSION:COMPONENT"].commands
    )


def test_data_migrate_needs_dry_run_or_apply_until_it_writes_by_default(monkeypatch):
    """It was dry by default; a script written then must not start writing
    unasked, so for one release a bare migrate is refused (#1732)."""
    calls = []
    monkeypatch.setattr(hdb_migrate, "migrate", lambda *a, **kw: calls.append(kw) or "plan")
    assert runner.invoke(cli.app, ["data", "migrate", "trade", "2026-09-01"]).exit_code == 1
    assert not calls
    assert runner.invoke(cli.app, ["data", "migrate", "trade", "2026-09-01", "-n"]).exit_code == 0
    assert calls[-1]["apply"] is False
    ok = runner.invoke(cli.app, ["data", "migrate", "trade", "2026-09-01", "--apply"])
    assert ok.exit_code == 0 and calls[-1]["apply"] is True


def test_config_set_dry_run_checks_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(stack_procs, "list_process_names", lambda _p: ["rdb1"])
    monkeypatch.setattr(stack_procs, "_write_overrides", lambda *_a: pytest.fail("wrote"))
    result = runner.invoke(cli.app, ["config", "set", "rdb1", "startwithall", "1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would set rdb1.startwithall = 1" in result.output
    refused = runner.invoke(cli.app, ["config", "set", "rdb1", "nosuchfield", "1", "-n"])
    assert refused.exit_code == 1, "a dry run is checked as a real one is"
