"""Reading and changing one process's configuration, and reading its output.

Split out of cli/inspect.py when that file passed the 400-line budget adding
an interactive qcon session to `query`. The line it was split on is what each command is ABOUT:
`inspect` answers questions about the DATA in a running stack - the HDB's
shape, a q expression, a table's columns - while these four are about a
PROCESS: the row it runs under, the catalogue it comes from, and what it has
written to its log.

Imported immediately after `inspect` in cli/entry.py, which keeps `--help`
listing these commands exactly where they were before the split.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import ChangesState, ReadOnly, dry_run_note
from uqs.cli.shared import (
    BasePortOpt,
    ExportOpt,
    _die,
    _export,
    _paths,
    app,
    console,
)
from uqs.paths import UqsError
from uqs.stack import process_unset, site_fields
from uqs.stack import procs as stack_procs

#: `uqs config show/set/unset/list`: process.csv rows, set in process_overrides.csv (#1855).
config_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Process configuration: show, set, unset, list, env, housekeeping, field, sources.\n\n"
    "process.csv fields, set in process_overrides.csv; the resolved env; housekeeping1's "
    "schedule; the sources the stack reads.",
)
app.add_typer(config_app, name="config")


GetJson = json_opt(
    '{"process", "fields": [{field, value}]} - or, given a FIELD, {"process", "field", "value"}'
)


@config_app.command("show", cls=ReadOnly)
def config_get(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    field: Annotated[
        str | None, typer.Argument(metavar="FIELD", autocompletion=completion.csv_fields)
    ] = None,
    port: BasePortOpt = None,
    raw: Annotated[
        bool, typer.Option("--unresolved", help="Show unresolved ${VAR}/{VAR}+N placeholders as-is")
    ] = False,
    export: ExportOpt = None,
    as_json: GetJson = False,
) -> None:
    """Show a process's effective process.csv row, or one FIELD of it.

    ${VAR}/{VAR}+N placeholders resolve as torq.sh would; --unresolved shows them literal."""
    try:
        row = stack_procs.get_process_config(_paths(), procname, base_port=port, resolve=not raw)
    except UqsError as exc:
        _die(exc)
    if field is not None:
        try:
            value = site_fields.field_value(row, field)
        except UqsError as exc:
            _die(exc)
    if as_json and field is not None:
        emit({"process": procname, "field": field, "value": value})
        return
    if as_json:
        emit({"process": procname, "fields": [{"field": k, "value": v} for k, v in row.items()]})
        _export([{"field": k, "value": v} for k, v in row.items()], export)
        return
    if field is not None:
        print(value)  # the value alone, as `$(uqs config show ...)` takes it
        return
    table = Table(title=f"{procname} config")
    table.add_column("field")
    table.add_column("value")
    for k, v in row.items():
        table.add_row(k, v)
    console.print(table)
    _export([{"field": k, "value": v} for k, v in row.items()], export)


@config_app.command("set", cls=ChangesState)
def config_set(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    pairs: Annotated[
        list[str],
        typer.Argument(
            metavar="FIELD=VALUE...",
            help="process.csv fields and their values, as `uqs target set` takes its "
            "settings: port=6061 startwithall=0",
            autocompletion=completion.csv_fields,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Check the setting and show it; write nothing"),
    ] = False,
) -> None:
    """Set PROC's process.csv fields in process_overrides.csv; `uqs config unset` removes one."""
    settings = _pairs(pairs)
    try:
        for field, value in settings:  # every one checked before any is written
            stack_procs.set_process_config(_paths(), procname, field, value, True)
        if not dry_run:
            for field, value in settings:
                stack_procs.set_process_config(_paths(), procname, field, value, False)
    except UqsError as exc:
        _die(exc)
    for field, value in settings:
        verb = "would set" if dry_run else "set"
        console.print(f"{verb} {procname}.{field} = {value}", markup=False)
    if dry_run:
        console.print(dry_run_note())


def _pairs(words: list[str]) -> list[tuple[str, str]]:
    """FIELD=VALUE words as (field, value)."""
    out = []
    for word in words:
        field, sep, value = word.partition("=")
        if not sep or not field:
            _die(UqsError(f"{word!r} is not FIELD=VALUE"))
        out.append((field, value))
    return out


@config_app.command("unset", cls=ChangesState)
def config_unset(
    procname: Annotated[
        str, typer.Argument(metavar="PROC", help="The process", autocompletion=completion.procname)
    ],
    fields: Annotated[
        list[str],
        typer.Argument(
            metavar="FIELD...",
            help="Fields whose override to remove, so process.csv's own value applies",
            autocompletion=completion.csv_fields,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Check the change and show it; write nothing"),
    ] = False,
) -> None:
    """Remove PROC's override of each FIELD: process.csv's own value applies again."""
    try:
        process_unset.unset_process_config(_paths(), procname, fields, dry_run)
    except UqsError as exc:
        _die(exc)
    verb = "would unset" if dry_run else "unset"
    console.print(f"{verb} {procname}.{', '.join(fields)}", markup=False)
    if dry_run:
        console.print(dry_run_note())
