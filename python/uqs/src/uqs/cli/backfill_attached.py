"""`uqs job backfill --attach ATTACHMENT`: the attached-destination branch (#1454).

Apart from cli/backfill.py, which would otherwise carry two commands: the
fleet-path backfill starts a process and returns; this resolves a saved
attachment, asks its stack read-only questions and prints what a run would do.
Everything it decides is in uqs.attach.backfill.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

import typer

from uqs.attach import backfill as attached
from uqs.attach import backfill_audit, backfill_run, config, service
from uqs.attach import backfill_plan as planning
from uqs.attach.backfill_policy import EOD_BYPASSED, EOD_FENCE, coordination_line, override_lines
from uqs.cli import backfill_receive
from uqs.cli.shared import _die, _paths, console, log
from uqs.paths import UqsError
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
    verbose: bool = False,
    trace: bool = False,
    writer: str | None = None,
    force: bool = False,
    waived: Mapping[str, str] | None = None,
) -> None:
    """Validate, plan or run a backfill into `attachment`'s native HDB; non-zero on a refusal.

    `waived` ({finding id: reason}, from safety.given) is honoured by plan, dry-run and run
    on this machine, each admitting afresh with it; refused with --writer and in validate."""
    mode = mode or "run"
    if mode not in MODES:
        _die(UqsError(f"--mode {mode!r} is not one of {', '.join(MODES)}"))
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
                wait=wait,
                verbose=verbose,
                trace=trace,
                waived=waived,
            )
            return
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        typer.echo(json.dumps(out, indent=2, sort_keys=True))
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
        raise UqsError(
            f"{names} is not supported with --writer: the receiver on the stack's machine "
            "admits and fences every batch itself, and is never told of a waiver - run the "
            "backfill on that machine with --attach, or wait for the window to pass"
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
    out = {"mode": "validate", "worker": worker, "destination": dest.as_dict(), "mutates": False}
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
    info = {"mode": mode, "worker": worker, "plan": plan, **run_.as_dict()}
    if not wait:
        _report(
            info,
            as_json,
            f"started {worker} (pid {run_.pid}) into {run_.destination.hdb_dir}; log {run_.log}"
            f"\n  follow it with `{logs_command(attachment, worker)} -f`"
            + _bypass_note(plan, run_.audit),
        )
        raise typer.Exit(code=0)
    if not as_json:  # with --json, stdout is the one document below (#1685)
        console.print(
            f"[dim]following {worker} (pid {run_.pid}); Ctrl-C stops waiting, not the run[/]"
        )
    code, result = backfill_run.follow(run_)
    backfill_audit.outcome(
        run_.audit, run_.run_id, outcome=result.get("outcome") or result.get("state", "unknown"),
        exit_code=code, result=result,
    )  # fmt: skip
    _report(
        {**info, "result": result, "exit_code": code},
        as_json,
        _outcome_text(worker, run_, code, result) + _bypass_note(plan, run_.audit),
    )
    raise typer.Exit(code=code)


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
    return f"uqs logs --attach {attachment} --worker {worker}"


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
    if outcome == "queryable":
        readers = ", ".join(result.get("readers", [])) or "none"
        return f"{head}\n  written, covered and queryable (readers reloaded: {readers})"
    if result.get("error"):
        return f"{head}\n  {result['error']}"
    return head


def _report(info, as_json, text):
    if as_json:
        typer.echo(json.dumps(info, indent=2, sort_keys=True, default=str))
    else:
        console.print(text, markup=False)
