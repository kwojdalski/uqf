"""`uqs backfill`: run a bounded worker over a range.

Its own command rather than `uqs start <process>`, because a backfill takes
arguments no other process does - which worker, which source release, which
window - and they reach the process as flags on its start line. See
stack/backfill.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.shared import PortOpt, _debug_requested, _die, _paths, app, console
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill

_BOUND_HELP = (
    "A date or datetime: 2026-09-13, 2026-09-13T06:00, 2026-09-13T06:00+02:00, "
    "or a q timestamp such as 2026.09.13D06:00. No offset means UTC"
)


@app.command()
def backfill(
    ctx: typer.Context,
    worker: Annotated[
        str,
        typer.Argument(
            help="The bounded worker to run, e.g. demo_deals_backfill",
            autocompletion=completion.backfill_workers,
        ),
    ],
    range_from: Annotated[
        str, typer.Option("--from", help=f"Inclusive start of the range. {_BOUND_HELP}")
    ],
    range_to: Annotated[
        str, typer.Option("--to", help=f"Exclusive end of the range. {_BOUND_HELP}")
    ],
    version: Annotated[
        str | None,
        typer.Option(
            "--version",
            help="The source_version to record coverage under. Optional when the worker "
            "declares a default; a new value re-fetches windows already covered (a restatement)",
        ),
    ] = None,
    on_conflict: Annotated[
        str | None,
        typer.Option(
            "--on-conflict",
            help="For this run: what a write does with a row whose row_key is already "
            "there - upsert, replace, ignore, append or fail. Default: the worker's own "
            "(upsert unless it declares otherwise)",
            autocompletion=completion.choices(*stack_backfill.ON_CONFLICT),
        ),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option(
            "--mode",
            help="validate: check the worker and range, open nothing. plan: also list the "
            "windows a run would fetch, from the ledgers, read-only. dry-run: also fetch and "
            "transform, and write nothing. run (the default): all of it",
            autocompletion=completion.choices(*stack_backfill.MODES),
        ),
    ] = None,
    port: PortOpt = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Log at DEBUG inside the backfill process too: parsed flags, the "
            "worker's declaration, every window, each stage's timing",
        ),
    ] = False,
    trace: Annotated[
        bool,
        typer.Option(
            "--trace",
            help="Log at TRACE inside the backfill process: every query the source is "
            "sent - the SQL, or the q lambda and its bounds - and the rows and time it "
            "took. Includes everything --debug shows",
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait",
            help="Wait for the run's outcome and exit with it: 0 for completed or idle, "
            "1 for failed or a process that died. Without it, the exit code says only "
            "whether torq.sh started the process",
        ),
    ] = False,
) -> None:
    """Run a bounded worker over [--from, --to), recording coverage under --version.

    The range is required: a backfill that guessed one would publish the wrong
    window and record it as covered. --version is required too unless the
    worker declares a default source_version - one whose source is never
    restated. Pass a new --version to re-fetch windows already covered. The process registers with
    discovery, so the fleet has to be up, and it exits when the range is
    done - follow it with `uqs logs <process> -f`.

    e.g. `uqs backfill demo_deals_backfill --version v1 --from 2026-09-13 --to 2026-09-15`

    `--mode plan` lists the windows a run would fetch without opening the
    source or writing anything; `--mode dry-run` fetches them and writes
    nothing. Their output is in the process's log, like a run's.

    `--debug` (or `uqs --debug backfill ...`) starts the process with
    `-verbose`, so its log - `uqs logs <process>` - carries DEBUG lines.
    `--trace` starts it with `-trace`: every query sent to the source, at TRACE,
    and the DEBUG lines `--debug` would show, so each query sits beside its
    window.

    The exit code is torq.sh's: whether the process STARTED, not how the run
    ended. `--wait` follows the run to its outcome and exits with that - what
    a script or a scheduler wants.
    """
    if wait and mode in stack_backfill.NO_STATUS_MODES:
        _die(UqsError(f"--wait has nothing to wait for with --mode {mode}: it writes no status"))
        return
    try:
        paths = _paths()
        resolved = stack_backfill.resolve_version(worker, version)
        bound_from = stack_backfill.parse_bound("--from", range_from)
        bound_to = stack_backfill.parse_bound("--to", range_to)
        launched_at = datetime.now(UTC)
        result = stack_backfill.start(
            paths,
            worker,
            resolved,
            bound_from,
            bound_to,
            base_port=port,
            verbose=_debug_requested(ctx, debug),
            trace=trace,
            on_conflict=on_conflict,
            mode=mode,
        )
        if result.returncode != 0 or not wait:
            raise typer.Exit(code=result.returncode)
        procname = stack_backfill.procname_for(worker)
        console.print(
            f"[dim]waiting for {procname}'s outcome (Ctrl-C stops waiting, not the run)[/]"
        )
        state, code, error = stack_backfill.wait_for_outcome(
            paths, procname, resolved, bound_from, bound_to, launched_at
        )
    except UqsError as exc:
        _die(exc)
        return
    colour = "green" if code == 0 else "red"
    console.print(f"[{colour}]{procname}: {state}[/] - `uqs logs {procname}` for its log")
    if error:
        console.print(f"  {error}", markup=False)
    raise typer.Exit(code=code)
