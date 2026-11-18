"""`uqs start JOB --once` and `uqs restart JOB --once`: one unit, then the outcome (#2798).

The shared contract is stack/once.py (the q side is src/etl/core/once.q). This is the command
line's half for the commands that start a stack process: what they refuse, the one start line
they run, and how the outcome is printed and becomes the exit code. `uqs job backfill --once`
reports through `report` too, so the three words - done, no work, failed - read the same wherever
the unit ran.

A restart is a stop and a start, and a running process is exactly what `--once` must not touch,
so `restart --once` is a start that refuses a running process, as `start --once` does: with
nothing running there is nothing to stop, and with something running the answer is no.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NoReturn

import typer

from uqs.cli.json_out import emit
from uqs.cli.safety import dry_run_note
from uqs.cli.shared import _die, _paths, console
from uqs.paths import UqsError
from uqs.stack import once as stack_once
from uqs.stack import runtime, start_lines
from uqs.stack.start_overrides import NONE, Overrides

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


def start(
    procs: list[str] | None,
    port: int | None,
    *,
    profile: str | None,
    trace: bool,
    timeout: float | None,
    skips: Overrides = NONE,
    print_only: bool = False,
    as_json: bool = False,
) -> NoReturn:
    """Start the one job process named, with `-once`, wait for its outcome and leave with its
    code. Refused before anything is started when the process is not a job's, or is running."""
    try:
        if profile is not None:
            raise UqsError(
                "--once runs one unit of one job: name the job process, not --profile (a profile "
                "is a set of processes, and a service in it has no unit)"
            )
        name = stack_once.require_job_process(procs)
        job = stack_once.job_processes()[name]
        extras = _start_flags(trace, timeout)
        if print_only:
            _dry_run(name, port, extras, as_json)
        paths = _paths()
        stack_once.refuse_if_running(paths, name, port)
        launched_at = datetime.now(UTC)
        result = runtime.run_torq_sh(
            paths, ["start", name, "-extras", *extras], base_port=port, capture=as_json, skips=skips
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
