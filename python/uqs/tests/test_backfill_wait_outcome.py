"""`uqs job backfill --wait`: one set of exit codes and one outcome document, whichever
path the run takes - locally, `--attachment`, or `--attachment --writer` (#2226, #2228).

The attached runner exits 2 for a refusal (the q launchers' convention) and 3 for rows
written but not yet queryable; the CLI's 2 is a usage error, so a refusal there is 1, and
3 is the CLI's own EXIT_PARTIAL, which `--wait`'s help names.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import typer.main
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_audit, backfill_remote, backfill_run, config
from uqs.cli import backfill_attached
from uqs.cli.shared import EXIT_PARTIAL, EXIT_REFUSED, EXIT_STOPPED, EXIT_USAGE
from uqs.stack import backfill as stack_backfill
from uqs.stack import backfill_wait

runner = CliRunner()
ARGV = ["job", "backfill", "demo_deals_backfill", "--source-version", "v1"]
ARGV += ["--from", "2026-09-13", "--to", "2026-09-15", "--wait"]


def _local(monkeypatch, state: str, code: int):
    monkeypatch.setattr(
        stack_backfill, "start", lambda *a, **k: SimpleNamespace(returncode=0, stdout="")
    )
    monkeypatch.setattr(backfill_wait, "wait_for_outcome", lambda *a, **k: (state, code, ""))
    return runner.invoke(cli.app, [*ARGV, "--json"])


class _Run:
    pid, run_id, audit, log = 42, "r1", None, Path("/l/r1.log")
    destination = SimpleNamespace(attachment="a", hdb_dir=Path("/h"))

    def as_dict(self) -> dict:
        return {"pid": self.pid, "log": str(self.log)}


def _attached(monkeypatch, runner_code: int, result: dict, *, writer: bool = False):
    monkeypatch.setattr(stack_backfill, "resolve_version", lambda _w, v: v)
    monkeypatch.setattr(config, "load", lambda _a: None)
    monkeypatch.setattr(backfill_attached.service, "ipc_for", lambda _a: None)
    monkeypatch.setattr(backfill_run, "launch", lambda *a, **k: (_Run(), {"coordination": {}}))
    monkeypatch.setattr(backfill_remote, "launch_remote", lambda *a, **k: (_Run(), {}))
    monkeypatch.setattr(backfill_audit, "outcome", lambda *a, **k: None)
    monkeypatch.setattr(backfill_run, "follow", lambda _r: (runner_code, result))
    monkeypatch.setattr(backfill_run, "clear_stop", lambda _r: None)
    argv = [*ARGV, "--attachment", "a", "--mode", "run", "--json"]
    if writer:
        argv += ["--writer", "h:5000"]
    return runner.invoke(cli.app, argv)


@pytest.mark.parametrize(
    ("state", "code"), [("completed", 0), ("idle", 0), ("failed", 1), ("abandoned", 1)]
)
def test_a_local_wait_exits_and_reports_the_outcome_its_help_names(monkeypatch, state, code):
    result = _local(monkeypatch, state, code)
    assert result.exit_code == code, result.output
    doc = json.loads(result.output)
    assert (doc["state"], doc["ok"], doc["exit_code"]) == (state, code == 0, code)
    assert "exit" not in doc


#: The runner's exit -> the CLI's, as `--wait`'s help names them.
RUNNER_CODES = [
    (0, {"state": "completed", "outcome": "queryable"}, 0),
    (1, {"state": "partial", "outcome": "partial"}, EXIT_REFUSED),
    (2, {}, EXIT_REFUSED),  # a refusal inside the runner: never the CLI's usage error
    (3, {"state": "completed", "outcome": "written_not_queryable"}, EXIT_PARTIAL),
    (4, {"state": "stopped", "outcome": "stopped"}, EXIT_STOPPED),  # an operator's stop (#2679)
]


@pytest.mark.parametrize("writer", [False, True], ids=["attachment", "writer"])
@pytest.mark.parametrize(("runner_code", "said", "code"), RUNNER_CODES)
def test_an_attached_wait_exits_with_the_clis_code_not_the_runners(
    monkeypatch, runner_code, said, code, writer
):
    result = _attached(monkeypatch, runner_code, said, writer=writer)
    assert result.exit_code == code, result.output
    doc = json.loads(result.output)
    assert doc["ok"] is (code == 0) and doc["exit_code"] == code
    assert doc["state"] == said.get("state", "unknown") and doc["result"] == said


def test_every_wait_document_says_its_outcome_under_the_same_keys(monkeypatch):
    local = json.loads(_local(monkeypatch, "completed", 0).output)
    attached = json.loads(_attached(monkeypatch, 0, {"state": "completed"}).output)
    sent = json.loads(_attached(monkeypatch, 0, {"state": "completed"}, writer=True).output)
    for doc in (local, attached, sent):
        assert {"state", "ok", "exit_code"} <= doc.keys()


def test_the_partial_code_is_the_clis_own_and_the_help_names_every_code():
    assert EXIT_PARTIAL not in (0, EXIT_REFUSED, EXIT_USAGE)
    # typer vendors its own click, so the group is reached by attribute, not class
    backfill = cast(Any, typer.main.get_command(cli.app)).commands["job"].commands["backfill"]
    wait = next(p for p in backfill.params if p.name == "wait")
    for code in ("0 for completed", "1 for failed", f"{EXIT_PARTIAL} for rows written"):
        assert code in " ".join((wait.help or "").split()), code
