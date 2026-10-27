"""`uqs job backfill --attach NAME`: the attached-destination branch (#1454).

Apart from cli/backfill.py, which would otherwise carry two commands: the
fleet-path backfill starts a process and returns; this resolves a saved
attachment, asks its stack read-only questions and prints what a run would do.
Everything it decides is in uqs.attach.backfill.
"""

from __future__ import annotations

import json
from datetime import datetime

import typer

from uqs.attach import backfill as attached
from uqs.attach import backfill_plan as planning
from uqs.attach import backfill_run, config, service
from uqs.cli import backfill_receive
from uqs.cli.shared import _die, _paths, console
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill


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
) -> None:
    """Validate, plan or run a backfill into `attachment`'s native HDB; non-zero on a refusal."""
    mode = mode or "run"
    if mode not in (*attached.READ_ONLY_MODES, *attached.EXECUTION_MODES):
        _die(UqsError(f"--mode {mode!r} is not one of validate, plan, dry-run, run"))
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
        )
    except UqsError as exc:
        _die(exc)
        return
    info = {"mode": mode, "worker": worker, "plan": plan, **run_.as_dict()}
    if not wait:
        _report(
            info,
            as_json,
            f"started {worker} (pid {run_.pid}) into {run_.destination.hdb_dir}; log {run_.log}",
        )
        raise typer.Exit(code=0)
    if not as_json:  # with --json, stdout is the one document below (#1685)
        console.print(
            f"[dim]following {worker} (pid {run_.pid}); Ctrl-C stops waiting, not the run[/]"
        )
    code, result = backfill_run.follow(run_)
    _report(
        {**info, "result": result, "exit_code": code},
        as_json,
        _outcome_text(worker, run_, code, result),
    )
    raise typer.Exit(code=code)


def _outcome_text(worker, run_, code, result) -> str:
    """What the run did, in the words that keep written and queryable apart."""
    outcome = result.get("outcome") or result.get("state", "unknown")
    head = f"{worker}: {outcome} (exit {code}); log {run_.log}"
    if outcome == "written_not_queryable":
        failed = "; ".join(result.get("failed_readers", [])) or "a reader"
        return (
            f"{head}\n  the rows are WRITTEN and covered but NOT queryable: {failed}."
            f"\n  Re-run the same command: nothing is rewritten, the readers are asked again."
        )
    if outcome == "written_forced":
        return (
            f"{head}\n  appended into the writer's HDB WITHOUT CHECKS (--force); no reader was "
            "told to reload - run `\\l .` on the HDB processes. A re-run of covered windows "
            "sends nothing, but nothing guards against a duplicate append"
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
