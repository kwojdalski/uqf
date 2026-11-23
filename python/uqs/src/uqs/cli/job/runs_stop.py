"""`uqs job runs stop`: stop a running attached backfill (#2690).

A verb of `job runs` (cli/job/runs.py), apart because it changes state where the
rest of the group reads the ledger.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli.common.concepts import attach_opt, job_arg
from uqs.cli.common.safety import ChangesState, DryRunOpt, dry_run_note
from uqs.cli.common.shared import EXIT_STOPPED, _die, _paths, console
from uqs.cli.common.vocabulary import duration
from uqs.cli.fleet.stop_options import KillOpt
from uqs.cli.job.runs import runs_app
from uqs.paths import UqsError

#: The recorded states of a run that did its job: an ended run reported so exits 0 (#2950).
_ENDED_WELL = ("completed", "idle")

_StopJob = job_arg("The bounded worker whose running backfill to stop")
_StopAttach = attach_opt(
    "The attachment whose destination the backfill is running into", required=True
)


@runs_app.command("stop", cls=ChangesState)
def stop(
    job: _StopJob,
    attach: _StopAttach,
    timeout: Annotated[
        float,
        duration(
            "--timeout",
            help="Seconds (a DURATION) to wait for the run to stop at its next window boundary",
        ),
    ] = 60.0,
    kill: KillOpt = False,
    dry_run: DryRunOpt = False,
) -> None:
    """Stop a running attached backfill: ask it to, wait, and escalate only with --kill.

    Writes the run's stop file, which it checks between windows: the window it is in
    completes or fails as it would anyway, it releases its locks, records `stopped` and
    exits. Without --kill a run that has not stopped by --timeout is reported as still
    running and left alone; a re-run of the same command completes the range.

    Exits 4 when the run stopped (or was killed), 1 when it is still running. A run that
    exits without reading the request - its last window finished the range, or it failed - is
    reported as it recorded ending: exit 0 for completed or idle, 1 for anything else.
    """
    from uqs.attach import backfill_stop

    try:
        record = backfill_stop.find(attach, job)
        state_dir = backfill_stop.run_state_dir(_paths(), attach, record)
        if dry_run:
            console.print(
                f"would stop {job} (pid {record['pid']}) into {attach}"
                + (f", killing it after {timeout:g}s" if kill else ""),
                markup=False,
            )
            console.print(dry_run_note())
            return
        result = backfill_stop.stop(record, state_dir, timeout=timeout, kill=kill)
    except UqsError as exc:
        _die(exc)
    if result.state == backfill_stop.STOPPED:
        console.print(f"{job} (pid {result.pid}): stopped at a window boundary", markup=False)
    elif result.state == backfill_stop.KILLED:
        console.print(f"{job} (pid {result.pid}): killed after {timeout:g}s", markup=False)
    elif result.state == backfill_stop.ENDED:
        done = result.recorded in _ENDED_WELL
        ended = result.recorded or "without recording how it ended (a crash or a signal)"
        console.print(
            f"{job} (pid {result.pid}): exited before reading the stop request - it ended "
            f"{ended}" + ("" if done else f"; its log is under {state_dir / 'logs'}"),
            markup=False,
        )
        raise typer.Exit(code=0 if done else 1)
    else:
        hint = "" if kill else "; its stop request stands - add --kill to end it now"
        console.print(f"{job} (pid {result.pid}): still running{hint}", markup=False)
    raise typer.Exit(code=1 if result.state == backfill_stop.RUNNING else EXIT_STOPPED)
