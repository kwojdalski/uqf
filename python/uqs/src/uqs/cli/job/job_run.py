"""`uqs job run JOB --once`: one unit of a job, then the outcome (#2798).

The shared contract is stack/once.py (the q side is src/etl/core/once.q). This is the command
line's half: it takes a job by the name `uqs job list` shows, finds the process that runs it,
refuses what has no single unit, runs the one start line and turns the outcome into the exit
code. `uqs job backfill --once` reports through `report` too, so the three words - done, no
work, failed - read the same wherever the unit ran.

`--once` is a job's, not a process's: `uqs start` and `uqs restart` manage TorQ processes and
the fleet, where a profile, `all` or a service has no unit to run, so they take no `--once`.
`--once` is required for now; a job run without it - the job's process in the foreground - is
`uqs start PROCESS -f` until it earns its own form here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, NoReturn

import typer

from uqs.cli.common import completion
from uqs.cli.common.json_out import emit, json_opt
from uqs.cli.common.safety import ChangesState, dry_run_note
from uqs.cli.common.shared import JOB_OPERATIONS_PANEL, BasePortOpt, _die, _paths, console, job_app
from uqs.cli.fleet import launch_options
from uqs.cli.fleet.launch_options import OnceOpt, OnceTimeoutOpt, TraceOpt
from uqs.cli.fleet.lifecycle import DryRunOpt
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import once as stack_once
from uqs.stack import runtime, start_lines

JsonOpt = json_opt(
    '{"once", "process", "job", "outcome", "exit_code", ...}',
    "With --dry-run, the start line",
)

#: What each outcome is called and coloured in a person's output.
_STYLE = {"done": "green", "no_work": "yellow", "failed": "red", "stopped": "red"}


def report(procname: str, job: str, outcome: stack_once.Outcome, as_json: bool) -> NoReturn:
    """Print the unit's outcome - one JSON document, or a line - and leave with its code."""
    if as_json:
        emit(stack_once.as_document(procname, job, outcome))
        raise typer.Exit(code=outcome.code)
    doc = outcome.document
    colour = _STYLE.get(outcome.outcome, "red")
    words = outcome.outcome.replace("_", " ")
    rows = f", {doc['rows']} row(s) published" if doc.get("rows") else ""
    console.print(f"[{colour}]{procname}: {words}[/]{rows} - `uqs logs {procname}` for its log")
    if outcome.reason:
        console.print(f"  {outcome.reason}", markup=False)
    raise typer.Exit(code=outcome.code)


def _start_flags(trace: bool, timeout: float | None) -> list[str]:
    """What torq.sh's `-extras` carries for a one-unit start: `-trace` when asked, then `-once`."""
    return [*(runtime.TRACE_EXTRAS if trace else ()), *stack_once.flags(timeout)]


@job_app.command("run", rich_help_panel=JOB_OPERATIONS_PANEL, cls=ChangesState)
def run(
    job: Annotated[
        str,
        typer.Argument(
            metavar="JOB",
            help="The job to run, as `uqs job list` names it, e.g. demo_deals_feed",
            autocompletion=completion.streaming_jobs,
        ),
    ],
    once: OnceOpt = False,
    once_timeout: OnceTimeoutOpt = None,
    port: BasePortOpt = None,
    trace: TraceOpt = False,
    print_only: DryRunOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Run ONE unit of a job in a fresh process, then exit with its outcome.

    Takes the job's name - `uqs job list` shows each job and the process
    that runs it - and starts that process with `-once`: one poll (a polling
    feed), one cycle (a timer job), or one input batch and its processing (a
    streaming subscriber). A bounded worker needs a range, so it runs one
    window through `uqs job backfill --once` instead.

    A process of the job that is already running is refused: never stopped,
    never reused. `--dry-run` shows the start line and runs nothing; `--json`
    prints the outcome as one document. `--once` is required.
    """
    refused = launch_options.once_refusal(once=once, once_timeout=once_timeout, follow=False)
    if refused is None and not once:
        refused = UqsUsageError(
            "`uqs job run` needs --once: it runs one unit of the job and exits. To keep the "
            "job's process running, `uqs start <process>` (its process is in `uqs job list`)"
        )
    if refused is not None:
        _die(refused)
    try:
        name = stack_once.process_of(job)
        extras = _start_flags(trace, once_timeout)
        if print_only:
            _dry_run(name, port, extras, as_json)
        paths = _paths()
        stack_once.refuse_if_running(paths, name, port)
        launched_at = datetime.now(UTC)
        result = runtime.run_torq_sh(
            paths, ["start", name, "-extras", *extras], base_port=port, capture=as_json
        )
        if result.returncode:
            if as_json:
                emit(
                    {
                        "once": True,
                        "process": name,
                        "job": job,
                        "started": False,
                        "ok": False,
                        "exit_code": result.returncode,
                        "output": (result.stdout or "").splitlines(),
                    }
                )
            raise typer.Exit(code=result.returncode)
        if not as_json:
            console.print(
                f"[dim]waiting for {name}'s one unit (Ctrl-C stops waiting, not the run)[/]"
            )
        outcome = stack_once.wait(paths, name, launched_at, base_port=port)
    except UqsError as exc:
        _die(exc)
    report(name, job, outcome, as_json)


def _dry_run(name: str, port: int | None, extras: list[str], as_json: bool) -> NoReturn:
    """`--once --dry-run`: the start line, `-once` and all, composed and never run."""
    try:
        lines = start_lines.plan(_paths(), name, port, None, extras=extras)
    except UqsError as exc:
        _die(exc)
    if as_json:
        document: dict[str, Any] = {
            "dry_run": True,
            "once": True,
            "start_lines": [{"process": s.procname, "line": s.line, "log": s.log} for s in lines],
            "ok": True,
        }
        emit(document)
        raise typer.Exit(code=0)
    for line in lines:
        console.print(line.printed(), markup=False, highlight=False)
    console.print(dry_run_note())
    raise typer.Exit(code=0)
