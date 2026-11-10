"""`uqs job backfill`: run a bounded worker over a range.

Its own command rather than `uqs start <process>`, because a backfill takes
arguments no other process does - which worker, which source release, which
window - and they reach the process as flags on its start line. See
stack/backfill.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer

from uqs import timerange
from uqs.attach.backfill_forced import ANY_WRITER
from uqs.cli import backfill_attached, completion, safety
from uqs.cli.choices import ChoiceOpt, alternatives
from uqs.cli.concepts import attach_opt
from uqs.cli.json_out import JSON_HELP, emit
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.renames import ATTACHMENT, bypass_flag, renamed, warn_renamed
from uqs.cli.shared import (
    JOB_OPERATIONS_PANEL,
    BasePortOpt,
    _debug_requested,
    _die,
    _paths,
    console,
    job_app,
    log,
)
from uqs.generated import q_facts
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import backfill as stack_backfill
from uqs.stack import backfill_wait as stack_backfill_wait
from uqs.stack import run_modes

_OnConflictOpt = ChoiceOpt(
    stack_backfill.ON_CONFLICT,
    "--on-conflict",
    "For this run: what a write does with a row whose row_key is already there - "
    "{values}. Default: the worker's own "
    f"({q_facts.DEFAULT_IO_STRATEGY} unless it declares otherwise)",
)
_AttachOpt = attach_opt(
    "The saved attachment whose native HDB to backfill into "
    "(`uqs job attachment add`), taking the stack and the destination from it. plan "
    "lists the windows, the native date partitions, the writers and the readers to "
    "reload, and changes nothing; run and dry-run admit afresh and start the runner "
    "(completed historical dates only)"
)


@job_app.command(
    "backfill",
    rich_help_panel=JOB_OPERATIONS_PANEL,
    cls=renamed(
        {
            "--port": "--base-port",
            "--force": bypass_flag(ANY_WRITER),
            "--any-writer": bypass_flag(ANY_WRITER),
            "--debug": "--debug",
            "--version": "--source-version",
            **ATTACHMENT,
        },
        needs_pipelines(
            "runs this tree's bounded workers",
            safety.marked(
                safety.Effect.CHANGES_STATE,
                dry_run_starts="its dry run starts the worker, which fetches and transforms "
                "every window (--mode dry-run, #1732)",
            ),
        ),
    ),
)
def backfill(
    ctx: typer.Context,
    worker: Annotated[
        str,
        typer.Argument(
            metavar="JOB",
            help="The bounded worker to run, e.g. demo_deals_backfill",
            autocompletion=completion.backfill_workers,
        ),
    ],
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    version: Annotated[
        str | None,
        typer.Option(
            "--source-version",
            help="The source_version to record coverage under. Optional when the worker "
            "declares a default; a new value re-fetches windows already covered (a restatement)",
        ),
    ] = None,
    on_conflict: _OnConflictOpt = None,
    mode: Annotated[
        str | None,
        typer.Option(
            "--mode",
            help=alternatives(
                run_modes.MODE_OPTION_MODES,
                {m: run_modes.NOTES[m] for m in run_modes.MODE_OPTION_MODES},
                default=run_modes.DEFAULT_MODE,
                quote="`",
            )
            + ". A dry run - fetch and transform, write nothing - is --dry-run",
            autocompletion=completion.choices(*run_modes.MODE_OPTION_MODES),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Fetch and transform every window, and write nothing: --mode dry-run",
        ),
    ] = False,
    fixture: Annotated[
        bool,
        typer.Option(
            "--fixture/--live",
            help="Let a worker whose source has no credential write the source's fixture "
            "(a demo), or (--live, the default) refuse it outside a dry run. A fixture's "
            "coverage is recorded under <version>~fixture, so a later live run still fetches "
            "the range",
        ),
    ] = False,
    attach: _AttachOpt = None,
    writer: Annotated[
        str | None,
        typer.Option(
            "--writer",
            metavar="HOST:PORT",
            help="With --attachment: extract here and send each window to the receiver "
            "`uqs job receiver start` started on the stack's machine, which writes its HDB",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help=f"{JSON_HELP}: what started (with --wait, its outcome) - or, with "
            "--attachment, the admission plan",
        ),
    ] = False,
    port: BasePortOpt = None,
    trace: Annotated[
        bool,
        typer.Option(
            "--trace",
            help="Log at TRACE inside the backfill process: every query the source is "
            "sent - the SQL, or the q lambda and its bounds - and the rows and time it "
            "took. Includes everything `uqs --debug` shows",
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait",
            help="Wait for the run's outcome and exit with it: 0 for completed or idle; "
            "1 for failed, partial, refused or a process that died; with --attachment, 3 "
            "for rows written and covered that a reader has not reloaded - run the same "
            "command again to ask the readers again. Without it, the exit code says only "
            "whether the process started",
        ),
    ] = False,
    follow: Annotated[
        bool,
        typer.Option(
            "--follow",
            "-f",
            help="With --attachment: --wait, streaming the run's own log as it goes. Ctrl-C "
            "stops the stream; the run keeps running",
        ),
    ] = False,
    override: safety.OverrideOpt = None,
    reason: safety.ReasonOpt = None,
) -> None:
    """Run a bounded worker over [--from, --to), recording coverage under --source-version.

    The range is required - --from/--to, --date or --last: a backfill that
    guessed one would publish the wrong window and record it as covered.
    --source-version is required too unless the worker declares a default
    source_version - one whose source is never restated. Pass a new
    --source-version to re-fetch windows already covered. The process registers with discovery, so
    the stack has to be up, and it exits when the range is done - follow it with
    `uqs logs <process> -f`.

    e.g.

        uqs job backfill demo_deals_backfill --source-version v1 --from 2026-09-13 --to 2026-09-15
        uqs job backfill demo_deals_backfill --source-version v1 --date 2026-09-13

    `--mode plan` lists the windows a run would fetch without opening the
    source or writing anything; `--dry-run` (`--mode dry-run`) fetches them and
    writes nothing. Their output is in the process's log, like a run's. Every
    mode but a run rehearses against the data directory as the stack's last
    start left it, and never bootstraps it: on a tree not yet started it is
    refused, naming `uqs start`.

    `uqs --debug job backfill ...` starts the process with
    `-verbose`, so its log - `uqs logs <process>` - carries DEBUG lines.
    `--trace` starts it with `-trace`: every query sent to the source, at TRACE,
    and the DEBUG lines `uqs --debug` would show, so each query sits beside its
    window.

    The exit code is torq.sh's: whether the process STARTED, not how the run
    ended. `--wait` waits for the run's outcome and exits with that - what
    a script or a scheduler wants. With `--attachment`, `--follow` (`-f`) is
    `--wait` that also streams the run's own log as it goes; Ctrl-C stops the
    stream and leaves the run running.

    With `--attachment`, `--skip-eod-fence --reason TEXT` (`--override eod-fence`)
    admits a plan, dry run or run within the native writers' end-of-day fence
    and gives it no end-of-day write deadline - "EOD fence bypassed; no EOD
    write deadline". Every other coordination check still refuses: the active
    date, a reload in progress, an unknown clock, and the rest.
    """
    try:
        waived = safety.given("job backfill", override, reason)
    except UqsError as exc:
        _die(exc)
        return
    # `--override any-writer` (a bare --any-writer until #1852) is the writer's,
    # not a coordination waiver: it travels as `force`.
    why = waived.pop(ANY_WRITER, None)
    force = why is not None
    if force:
        log.warning("uqs job backfill: --override {} ({})", ANY_WRITER, why)
    if waived and attach is None:
        _die(
            UqsUsageError(
                f"{backfill_attached.waiver_names(waived)} is for --attachment: only a backfill "
                "into an attached native HDB is fenced at its writers' end of day, and this one "
                "is not"
            )
        )
        return
    if mode == "dry-run" and not dry_run:  # one spelling of a dry run (#1858)
        warn_renamed("--mode dry-run", "--dry-run")
        dry_run = True
    if dry_run:
        if mode not in (None, "dry-run"):
            _die(UqsError(f"--dry-run is --mode dry-run: not with --mode {mode}"))
            return
        mode = "dry-run"
    if force and writer is None:
        _die(
            UqsUsageError(
                "--override any-writer is for --writer: it writes through the process at HOST:PORT"
            )
        )
        return
    if writer is not None and attach is None:
        _die(
            UqsUsageError(
                "--writer is for --attachment: it names the receiver of an attached destination"
            )
        )
        return
    if follow and attach is None:
        _die(
            UqsUsageError(
                "--follow is for --attachment: this backfill runs as a stack process - use "
                "--wait, and follow its log with `uqs logs <process> -f`"
            )
        )
        return
    if follow and as_json:  # stdout is one JSON document (#1685); a stream is not one
        _die(UqsUsageError("--follow does not apply with --json: use --wait --json"))
        return
    if follow and mode in run_modes.READ_ONLY_MODES:
        _die(UqsError(f"--follow has nothing to follow with --mode {mode}: it starts no run"))
        return
    if wait and mode in run_modes.READ_ONLY_MODES:
        _die(UqsError(f"--wait has nothing to wait for with --mode {mode}: it writes no status"))
        return
    try:
        bound_from, bound_to = timerange.resolve(range_from, range_to, day, last).bounds
    except UqsError as exc:
        _die(exc)
        return
    if attach is not None:
        try:
            resolved = stack_backfill.resolve_version(worker, version)
        except UqsError as exc:
            _die(exc)
            return
        backfill_attached.run(
            attach,
            worker,
            resolved,
            bound_from,
            bound_to,
            mode=mode,
            port=port,
            fixture=fixture,
            on_conflict=on_conflict,
            as_json=as_json,
            wait=wait,
            follow=follow,
            verbose=_debug_requested(ctx),
            trace=trace,
            writer=writer,
            force=force,
            waived=waived,
        )
        return
    try:
        paths = _paths()
        resolved = stack_backfill.resolve_version(worker, version)
        launched_at = datetime.now(UTC)
        result = stack_backfill.start(
            paths,
            worker,
            resolved,
            bound_from,
            bound_to,
            base_port=port,
            verbose=_debug_requested(ctx),
            trace=trace,
            on_conflict=on_conflict,
            mode=mode,
            fixture=fixture,
            **({"capture": True} if as_json else {}),
        )
        procname = stack_backfill.procname_for(worker)
        started = {
            "job": worker, "process": procname, "source_version": resolved,
            "range_from": bound_from.isoformat(), "range_to": bound_to.isoformat(),
            "mode": mode or "run", "started": result.returncode == 0,
            "output": (result.stdout or "").splitlines() if as_json else [],
        }  # fmt: skip
        if result.returncode != 0 or not wait:
            if as_json:
                emit(started)
            elif result.returncode == 0 and dry_run:
                console.print(safety.dry_run_note())
            raise typer.Exit(code=result.returncode)
        if not as_json:
            console.print(
                f"[dim]waiting for {procname}'s outcome (Ctrl-C stops waiting, not the run)[/]"
            )
        state, code, error = stack_backfill_wait.wait_for_outcome(
            paths, procname, resolved, bound_from, bound_to, launched_at, base_port=port
        )
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit({**started, **backfill_attached.outcome(state, code), "error": error})
        raise typer.Exit(code=code)
    colour = "green" if code == 0 else "red"
    console.print(f"[{colour}]{procname}: {state}[/] - `uqs logs {procname}` for its log")
    if error:
        console.print(f"  {error}", markup=False)
    if code == 0 and dry_run:
        console.print(safety.dry_run_note())
    raise typer.Exit(code=code)
