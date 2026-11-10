"""`uqs job backfill --attachment ATTACHMENT`: the attached-destination branch (#1454).

Apart from cli/backfill.py, which would otherwise carry two commands: the
fleet-path backfill starts a process and returns; this resolves a saved
attachment, asks its stack read-only questions and prints what a run would do.
Everything it decides is in uqs.attach.backfill.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import typer

from uqs.attach import backfill as attached
from uqs.attach import backfill_audit, backfill_run, config, service
from uqs.attach import backfill_plan as planning
from uqs.attach.backfill_policy import EOD_BYPASSED, EOD_FENCE, coordination_line, override_lines
from uqs.cli import backfill_receive
from uqs.cli.json_out import emit
from uqs.cli.shared import EXIT_PARTIAL, EXIT_REFUSED, EXIT_STOPPED, _die, _paths, console, log
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import attached_logs, run_modes
from uqs.stack import backfill as stack_backfill
from uqs.stack.run_modes import MODES


def run(
    attachment: str,
    worker: str,
    version: str,
    bound_from: datetime,
    bound_to: datetime,
    *,
    mode: str | None,
    port: int | None,
    fixture: bool,
    on_conflict: str | None,
    as_json: bool,
    wait: bool = False,
    follow: bool = False,
    stop_on_exit: bool = False,
    level: str | None = None,
    verbose: bool = False,
    trace: bool = False,
    writer: str | None = None,
    force: bool = False,
    waived: Mapping[str, str] | None = None,
) -> None:
    """Validate, plan or run a backfill into `attachment`'s native HDB; non-zero on a refusal.

    `waived` ({finding id: reason}, from safety.given) is honoured by plan, dry-run and run
    on this machine, each admitting afresh with it; refused with --writer and in validate."""
    mode = mode or run_modes.DEFAULT_MODE
    if mode not in MODES:
        _die(UqsUsageError(f"--mode {mode!r} is not one of {', '.join(MODES)}"))
        return
    waived = dict(waived or {})
    try:
        refuse_waivers(waived, mode=mode, writer=writer)
    except UqsError as exc:
        _die(exc)
        return
    if waived:
        log.warning(
            "{}: {} for this backfill into attachment {} - reason: {}",
            waiver_names(waived),
            EOD_BYPASSED,
            attachment,
            "; ".join(sorted(set(waived.values()))),
        )
    if follow and writer is not None:
        _die(
            UqsUsageError(
                "--follow is not supported with --writer: use --wait, and read the extractor's "
                f"log with `{logs_command(attachment, worker)} -f`"
            )
        )
        return
    if writer is not None:
        backfill_receive.extract(
            attachment, worker, version, bound_from, bound_to,
            writer=writer, mode=mode, port=port, fixture=fixture, on_conflict=on_conflict,
            as_json=as_json, wait=wait, verbose=verbose, trace=trace, force=force,
        )  # fmt: skip
        return
    try:
        paths = _paths()
        if mode == "validate":
            out, text = _validate(
                paths,
                attachment,
                worker,
                version,
                bound_from,
                bound_to,
                mode,
                port,
                fixture,
                on_conflict,
            )
        elif mode == "plan":
            att = config.load(attachment)
            out = planning.plan_attached(
                paths,
                attachment,
                worker,
                version,
                bound_from,
                bound_to,
                ipc=service.ipc_for(att),
                port=port,
                fixture=fixture,
                on_conflict=on_conflict,
                waived=waived,
            )
            text = planning.render(out)
        else:
            _execute(
                paths,
                attachment,
                worker,
                version,
                bound_from,
                bound_to,
                mode=mode,
                port=port,
                fixture=fixture,
                on_conflict=on_conflict,
                as_json=as_json,
                wait=wait or follow,
                follow=follow,
                stop_on_exit=stop_on_exit,
                level=level,
                verbose=verbose,
                trace=trace,
                waived=waived,
            )
            return
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit(out)
    else:
        console.print(text, markup=False)
    raise typer.Exit(code=0)


def waiver_names(waived: Mapping[str, str]) -> str:
    """The waivers given, as an operator spelled the flag: `--skip-eod-fence`."""
    return ", ".join(f"--skip-{name}" for name in sorted(waived))


def refuse_waivers(waived: Mapping[str, str], *, mode: str, writer: str | None) -> None:
    """Refuse a waiver where it cannot be honoured (#1828): only this machine's attached
    admission (plan, dry-run and run) applies it."""
    if not waived:
        return
    names = waiver_names(waived)
    unknown = sorted(set(waived) - {EOD_FENCE})
    if unknown:
        raise UqsError(f"{', '.join(unknown)} cannot be waived for an attached backfill")
    if writer is not None:
        raise UqsUsageError(
            f"{names} is not supported with --writer: the receiver on the stack's machine "
            "admits and fences every batch itself, and is never told of a waiver - run the "
            "backfill on that machine with --attachment, or wait for the window to pass"
        )
    if mode == "validate":
        raise UqsError(
            f"{names} means nothing with --mode validate: validate asks the stack nothing, so "
            "there is no end-of-day fence to waive - use --mode plan to see what it lifts"
        )


def _validate(
    paths, attachment, worker, version, bound_from, bound_to, mode, port, fixture, on_conflict
):
    dest = attached.resolve(paths, attachment, worker, port=port, fixture=fixture)
    stack_backfill.backfill_flags(
        worker, version, bound_from, bound_to, on_conflict=on_conflict, mode=mode
    )
    out = {"mode": "validate", "job": worker, "destination": dest.as_dict(), "mutates": False}
    return out, (
        f"{worker} -> attachment {attachment}: destination {dest.hdb_dir} resolves (nothing opened)"
    )


def _execute(
    paths,
    attachment,
    worker,
    version,
    bound_from,
    bound_to,
    *,
    mode,
    port,
    fixture,
    on_conflict,
    as_json,
    wait,
    verbose,
    trace,
    waived=None,
    follow=False,
    stop_on_exit=False,
    level=None,
):
    att = config.load(attachment)
    try:
        run_, plan = backfill_run.launch(
            paths,
            attachment,
            worker,
            version,
            bound_from,
            bound_to,
            ipc=service.ipc_for(att),
            mode=mode,
            port=port,
            fixture=fixture,
            on_conflict=on_conflict,
            verbose=verbose,
            trace=trace,
            waived=waived,
        )
    except UqsError as exc:
        _die(exc)
        return
    info = {"mode": mode, "job": worker, "plan": plan, **run_.as_dict()}
    if not wait:
        _report(
            info,
            as_json,
            f"started {worker} (pid {run_.pid}) into {run_.destination.hdb_dir}; log {run_.log}"
            f"\n  follow it with `{logs_command(attachment, worker)} -f`"
            + _bypass_note(plan, run_.audit),
        )
        raise typer.Exit(code=0)
    if follow:
        _stream(attachment, worker, run_, stop_on_exit=stop_on_exit, level=level)
    elif not as_json:  # with --json, stdout is the one document below (#1685)
        console.print(
            f"[dim]waiting for {worker} (pid {run_.pid}); Ctrl-C stops waiting, not the run[/]"
        )
    runner, result = backfill_run.follow(run_)
    backfill_run.clear_stop(run_)  # a request nothing is left to read
    code = exit_code(runner)
    backfill_audit.outcome(
        run_.audit, run_.run_id, outcome=result.get("outcome") or result.get("state", "unknown"),
        exit_code=code, result=result,
    )  # fmt: skip
    _report(
        {**info, "result": result, **outcome(result.get("state") or "unknown", code)},
        as_json,
        _outcome_text(worker, run_, code, result) + _bypass_note(plan, run_.audit),
    )
    raise typer.Exit(code=code)


#: What `--follow` exits with when Ctrl-C ends the stream and the run goes on: the shell's code
#: for a command ended by SIGINT. Not 0 - nothing is known yet of how the run will end.
EXIT_INTERRUPTED = 130


def _stream(
    attachment: str,
    worker: str,
    run_: backfill_run.Launched,
    *,
    stop_on_exit: bool = False,
    level: str | None = None,
) -> None:
    """`--follow` (#2677): stream the launched run's own log - the file the launch reported -
    until its process exits, every line it wrote included. Ctrl-C ends the stream, never the
    run: it says so, names the command that follows it again, and exits EXIT_INTERRUPTED.

    With `stop_on_exit` the first Ctrl-C asks THIS run to stop instead (its stop file, written
    for the pid this invocation launched and no other) and goes on streaming until the run
    exits, so the operator sees it finish its window and say `stopped`. A second Ctrl-C ends
    the stream and leaves the request where it is: the run will still stop."""
    console.print(
        f"[dim]following {worker} (pid {run_.pid}); log {run_.log}; "
        + (
            "Ctrl-C asks it to stop after its current window; a second one stops following"
            if stop_on_exit
            else "Ctrl-C stops following, not the run"
        )
        + "[/]"
    )

    def ask_to_stop() -> None:
        path = backfill_run.request_stop(run_)
        console.print(
            f"stop requested of {worker} (pid {run_.pid}): it stops after its current window "
            f"({path}); following until it does - Ctrl-C again stops following and leaves the "
            "request in place",
            markup=False,
        )

    try:
        attached_logs.stream(
            run_.log,
            worker,
            until=lambda: run_.process.poll() is not None,
            on_interrupt=ask_to_stop if stop_on_exit else None,
            min_level=level,
        )
    except KeyboardInterrupt:
        console.print(
            f"{worker} (pid {run_.pid}) is still running"
            + ("; its stop request stands" if stop_on_exit and run_.stop_file.exists() else "")
            + f"; follow it again with `{logs_command(attachment, worker)} -f`",
            markup=False,
        )
        raise typer.Exit(code=EXIT_INTERRUPTED) from None


def exit_code(runner: int) -> int:
    """The attached runner's exit code as the CLI's (#2226). The runner exits 2 for a
    refusal - the q launchers' convention - where every `uqs` command's 2 is a usage
    error, so a refusal and a failure are both EXIT_REFUSED; rows written and covered
    that a reader has not reloaded are EXIT_PARTIAL, and a run an operator stopped between two
    windows is EXIT_STOPPED (#2679)."""
    if runner == 0:
        return 0
    if runner == backfill_run.EXIT_STOPPED:
        return EXIT_STOPPED
    return EXIT_PARTIAL if runner == backfill_run.EXIT_NOT_QUERYABLE else EXIT_REFUSED


def outcome(state: str, code: int) -> dict:
    """What every `job backfill --wait --json` document says of how the run ended, locally,
    with --attachment and with --writer alike (#2228): its `state`, the verdict `ok` - false
    exactly when the command exits non-zero - and that `exit_code`."""
    return {"state": state, "ok": code == 0, "exit_code": code}


def _bypass_note(plan, audit=None) -> str:
    """The fence a run started under, said where it was waived - never claimed fenced - and
    where the waiver is recorded (#1829)."""
    lines = override_lines(plan)
    if not plan["coordination"].get("eod_fence_bypassed"):
        return ""
    if audit is not None:
        lines.append(f"  recorded in   {audit}")
    return "\n" + "\n".join([coordination_line(plan["coordination"]), *lines])


def logs_command(attachment: str, worker: str) -> str:
    """The command that reads a run's log through `uqs logs` (#1725)."""
    return f"uqs logs --attachment {attachment} --worker {worker}"


def _outcome_text(worker, run_, code, result) -> str:
    """What the run did, in the words that keep written and queryable apart."""
    outcome = result.get("outcome") or result.get("state", "unknown")
    head = (
        f"{worker}: {outcome} (exit {code}); log {run_.log}\n"
        f"  read it with `{logs_command(run_.destination.attachment, worker)}`"
    )
    if outcome == "written_not_queryable":
        failed = "; ".join(result.get("failed_readers", [])) or "a reader"
        return (
            f"{head}\n  the rows are WRITTEN and covered but NOT queryable: {failed}."
            f"\n  Re-run the same command: nothing is rewritten, the readers are asked again."
        )
    if outcome == "written_forced":
        return (
            f"{head}\n  appended into the writer's HDB WITHOUT CHECKS (--override any-writer); no "
            "reader was told to reload - run `\\l .` on the HDB processes. A re-run of covered "
            "windows sends nothing, but nothing guards against a duplicate append"
        )
    if outcome == "stopped":
        return (
            f"{head}\n  stopped on request between two windows: what it finished is written and "
            "covered; the rest is not. Run the same command again to do the remaining windows"
        )
    if outcome == "queryable":
        readers = ", ".join(result.get("readers", [])) or "none"
        return f"{head}\n  written, covered and queryable (readers reloaded: {readers})"
    if result.get("error"):
        return f"{head}\n  {result['error']}"
    return head


def _report(info, as_json, text):
    if as_json:
        emit(info)
    else:
        console.print(text, markup=False)
