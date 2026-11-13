"""`uqs data replay`: replay a tickerplant log into the HDB.

Registers the `data` group on the app (see cli/shared.py), which
cli/hdb_check.py's `data check` joins.

What it wraps is TorQ's tickerlogreplay, unchanged. What it adds is aim: which
log, which database, which schema and which stack, all read from the processes
that are up. See stack/replay.py for why that is read from `ps` rather than
from the configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs import timerange
from uqs.cli import completion, safety
from uqs.cli.hdb_scope import AttachOpt, HdbOpt, local_hdb, tables_given
from uqs.cli.json_out import emit, json_opt
from uqs.cli.range_options import DateOpt, DayFromOpt, DayLastOpt, DayToOpt
from uqs.cli.safety import ConfirmsOnServer, dry_run_note
from uqs.cli.shared import (
    BasePortOpt,
    _die,
    _paths,
    app,
    console,
    data_app,
)
from uqs.paths import UqsError
from uqs.stack import replay as stack_replay

app.add_typer(data_app, name="data")


def _day(
    day: str | None, range_from: str | None, range_to: str | None, last: float | None = None
) -> str | None:
    """The one day to replay, as the log directories spell it (2026.09.23), or
    None for the plant's newest. A log is one day's, so the range must be one -
    given any way a range is given, `--last` too (#1858): `--last 1d` is
    yesterday, as on every whole-partition command (#2245)."""
    if day is None and range_from is None and range_to is None and last is None:
        return None
    window = timerange.resolve(range_from, range_to, day, last)
    return window.day("data replay replays").strftime("%Y.%m.%d")


#: Why `data replay --attachment` is refused (#2237).
_NO_ATTACHMENT = (
    "a replay runs this machine's TorQ tickerlogreplay over a log here, and cannot check or "
    "pause an attached stack's writers - replay on that stack's own host"
)

ReplayJson = json_opt(
    '{"ok", "plan": [{what, value, from}], "start", "applied", "exit_code"} - exit_code '
    "null on the dry run"
)


@data_app.command("replay", cls=ConfirmsOnServer)
def replay(
    proc: Annotated[
        str | None,
        typer.Argument(
            metavar="PROC",
            help="Which running tickerplant's log to replay. Only needed when more than one is up",
            autocompletion=completion.procname,
            show_default=False,
        ),
    ] = None,
    day: DateOpt = None,
    range_from: DayFromOpt = None,
    range_to: DayToOpt = None,
    last: DayLastOpt = None,
    dir_: Annotated[
        str | None,
        typer.Option(
            "--tplogdir", help="Replay this log directory, instead of resolving a running plant"
        ),
    ] = None,
    hdb: HdbOpt = None,
    attach: AttachOpt = None,
    schema: Annotated[
        str | None,
        typer.Option(
            "--schema-file", help="Load this schema file, instead of the plant's -schemafile"
        ),
    ] = None,
    table: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            metavar="TABLE",
            help="Replay only this table; repeatable, and TABLE,TABLE also works. Default: "
            "every table in the log",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    port: BasePortOpt = None,
    apply: safety.ApplyOpt = False,
    yes: safety.ApplyYesOpt = False,
    as_json: ReplayJson = False,
) -> None:
    """Replay a tickerplant log into the HDB, aimed by what is running.

    With nothing passed it finds the tickerplant that is up, takes the log
    directory and schema file off that process's own start line, finds the
    database the HDB process has open (`--hdb DIR` names another), and
    replays the plant's newest day that end of day has handed over into it.
    Every one of those is an option, and an option that is given wins.
    `--base-port` defaults to the runtime's own, as on every command; a plant
    running under another is refused, naming the `--base-port` to give.

    THIS EMPTIES WHAT IT WRITES. TorQ's replay defaults are kept whole, which
    means the tables being replayed are cleared in the partitions it touches
    before it writes them. So without --apply it prints the plan and the start
    line and runs nothing; --apply shows the plan, asks, then replays; --yes
    does not ask.

    The day is --date D, --from D --to D+1, or --last 1d (yesterday): a log
    is one day's, so a range of anything else is refused. Default: the plant's newest day that
    end of day has handed over; today (the live day) is refused, as is any day
    the wdb still holds.

    e.g. `uqs data replay --date 2026-09-22 --table quote --table trade`
    """
    try:
        hdb_dir = local_hdb(attach, hdb, command="data replay", why=_NO_ATTACHMENT)
        plan = stack_replay.plan(
            procname=proc,
            date=_day(day, range_from, range_to, last),
            log_dir=Path(dir_) if dir_ else None,
            hdb_dir=hdb_dir,
            schema_file=Path(schema) if schema else None,
            tables=tables_given(table) or None,
            base_port=_paths().runtime_declaration.resolve_base_port(port),
            base_port_given=port is not None,
            wdb_dir=_paths().torqdata / "wdbhdb",
        )
    except UqsError as exc:
        _die(exc)

    line = " ".join([stack_replay.REPLAY_PROCNAME, "-extras", *plan.flags()])
    doc = {
        "plan": [{"what": w, "value": v, "from": f} for w, v, f in plan.rows()],
        "start": line,
        "applied": False,
        "exit_code": None,
    }
    if not as_json:
        _show(plan)
    if not apply:
        if as_json:
            emit({"ok": True, **doc})
            return
        console.print(f"would start: {line}", markup=False, highlight=False)
        console.print(dry_run_note("with --apply"))
        return

    tables = ", ".join(plan.tables) if plan.tables else "every table in the log"
    try:
        safety.json_needs_yes(as_json, yes, "replay")
        safety.confirm(
            f"Replay {tables} into {plan.hdb_dir}, emptying them first in the partitions "
            "it touches?",
            yes=yes,
        )
        result = stack_replay.start(_paths(), plan, capture=as_json)
    except UqsError as exc:
        _die(exc)
    if as_json:
        for said in (result.stdout, result.stderr):
            if said:
                typer.echo(said.rstrip("\n"), err=True)  # stdout is the document's
        emit({**doc, "ok": result.returncode == 0, "applied": True,
              "exit_code": result.returncode})  # fmt: skip
    raise typer.Exit(code=result.returncode)


def _show(plan: stack_replay.ReplayPlan) -> None:
    rendered = Table(title="replay plan")
    rendered.add_column("what")
    # Folded, not truncated: every value here is a path, and a path with its
    # middle replaced by an ellipsis is exactly the part that distinguishes
    # the log this replay found from the one the reader expected.
    rendered.add_column("value", overflow="fold")
    rendered.add_column("from", style="dim")
    for what, value, source in plan.rows():
        rendered.add_row(what, value, source)
    console.print(rendered)
