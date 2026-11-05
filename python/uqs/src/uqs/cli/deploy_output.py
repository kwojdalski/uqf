"""What `uqs deploy push`, `rollback` and `prune` print (#2125): text by
default, or - with --json, as on every other command - one document through
json_out.emit, `ok` its success flag. Anything else they say - a dry run's
plan and note, a new target's registration, prune's question - goes to stderr
under --json, so stdout parses whatever happens.

Beside cli/deploy.py, which is at its line budget.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import typer

from uqs.cli import deploy_dry_run as dry
from uqs.cli.json_out import emit, json_opt
from uqs.cli.shared import EXIT_REFUSED, EXIT_USAGE, json_refusal
from uqs.deploy import config
from uqs.deploy import report as deploy_report
from uqs.deploy.config import Config
from uqs.deploy.report import Report
from uqs.logger import get_logger

log = get_logger()

#: The stage a deploy command refuses its own command line at: a usage error,
#: exit 2 like any other (#1885); every other stage's failure exits 1.
ARGUMENTS = "arguments"


def failed(prefix: str, stage: str, exc: Exception, details: dict | None = None) -> None:
    """Refuse the command: exit 2 for its own command line, else 1. `details`
    go into the --json refusal beside `ok` and `error` - what a push did
    before a later target failed."""
    message = f"{prefix}: FAILED at {stage}: {config.redact(str(exc))}"
    log.error("{}", message)
    json_refusal(message, details)
    raise typer.Exit(code=EXIT_USAGE if stage == ARGUMENTS else EXIT_REFUSED)


PushJson = json_opt(
    '{"ok", "deployments": [{target, release, revision, artifact, dirty, ssh, dest, profile, '
    "status, stage, error, previous_release, processes, checks, rollback, ...}]} - one per "
    "target, in order; status planned on a dry run, whose plan goes to stderr"
)
RollbackJson = json_opt(
    '{"ok", "rollback": "planned"|"done"|"failed", "from", "to", "stop", '
    '"start": {profile, extra_processes}, "current", ...} - with "reason" and "restore" '
    "when it failed"
)
PruneJson = json_opt(
    '{"ok", "prune": "planned"|"done"|"declined", "kept": [{release, why}], "removed", '
    '"freed_bytes", "runtimes": {kept, removed}} - ok false when declined'
)


class PushOutput:
    """One push's output, over every target it deploys: under --json, one
    document with a row per target, printed once at the end."""

    def __init__(self, as_json: bool, dry_run: bool) -> None:
        self.as_json, self.dry_run = as_json, dry_run
        self.deployments: list[dict] = []
        #: where a dry run's plan goes
        self.plan = sys.stderr if as_json else sys.stdout

    def say(self, text: str) -> None:
        """A line beside the result: a new target's registration."""
        typer.echo(text, err=self.as_json)

    def on_result(self, cfg: Config) -> Callable[[Report], None]:
        """What driver.deploy hands `cfg`'s report to."""

        def take(report: Report) -> None:
            row = {"target": cfg.target, **report.as_dict()}
            self.deployments.append(row)
            if not self.as_json and not self.dry_run:
                typer.echo(deploy_report.render(row))

        return take

    def end(self, code: int) -> None:
        if self.as_json:
            emit({"ok": code == 0, "deployments": self.deployments})
        dry.end(self.dry_run, code, self.as_json)


def show(result: dict, render: Callable[[dict], str], *, as_json: bool, dry_run: bool) -> None:
    """rollback's or prune's result: the document, or its text, then a dry
    run's note."""
    if as_json:
        emit(result)
    else:
        typer.echo(render(result))
    dry.end(dry_run, 0 if result["ok"] else 1, as_json)
