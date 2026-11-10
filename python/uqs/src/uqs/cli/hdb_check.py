"""`uqs data check`: which HDB partitions lack a table or a column, and -
with --apply, after asking - the empty table or null column written in.

Two scopes (#1755). Without --table, --attachment or --hdb it judges this
runtime's own HDB against every table this runtime declares, as it always
has: stack/hdb_layout.py asks q which partitions there are (in the segments
par.txt names, for a segmented database - #1783), stack/hdb_shape.py judges
what they hold, scripts/gates/fill_hdb_partitions.q fills it, and
stack/hdb_types.py compares the stored types. With any of them it
checks ONE table in the HDB they name - an attached stack's, say, where
filling uqf's whole schema would add tables production never asked for - and
fills only the partitions lacking it, from this runtime's declaration of the
table when there is one, else from a partition that holds it
(scripts/gates/hdb_alter.q -fill, cli/hdb_scope.py).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.hdb_scope import (
    ApplyOpt,
    AttachOpt,
    HdbOpt,
    YesOpt,
    _table,
    confirm,
    resolve,
    run_scoped,
    tables_given,
)
from uqs.cli.json_out import Verbatim, emit, json_opt
from uqs.cli.renames import ATTACHMENT, renamed
from uqs.cli.safety import ConfirmsOnServer
from uqs.cli.shared import _die, _paths, console, data_app
from uqs.paths import UqsError
from uqs.stack import hdb_alter, hdb_fill, hdb_layout, hdb_shape, hdb_sorting, hdb_types
from uqs.stack.hdb_alter import Request
from uqs.timerange import DAY_FORMS, parse_day

CheckJson = json_opt(
    '{"ok", "hdb", "present", "segmented", "partitions", "missing_tables": {partition: [table]}, '
    '"missing_columns": {partition: {table: [column]}}, "types_checked", "type_changes": [...], '
    '"unsorted": [{partition, table, why}]} '
    '- or, with --table, --attachment or --hdb, {"ok", "hdb", "resolved_from", "plan", '
    '"result", ...}'
)


@data_app.command("check", cls=renamed(ATTACHMENT, ConfirmsOnServer))
def hdb_check(
    table: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            metavar="TABLE",
            help="Check and fill only this table (default with --attachment: the attachment's "
            "own); repeatable, and TABLE,TABLE also works",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    template: Annotated[
        str | None,
        typer.Option(
            "--template",
            metavar="DATE",
            help=f"The partition ({DAY_FORMS}) whose table is the schema to fill from, when "
            "this runtime declares none (default: the latest holding it)",
        ),
    ] = None,
    attach: AttachOpt = None,
    hdb: HdbOpt = None,
    apply: ApplyOpt = False,
    yes: YesOpt = False,
    as_json: CheckJson = False,
) -> None:
    """Check the HDB for missing tables and columns, and columns of the wrong type.

    Reports HDB partitions missing a table or a column - the cause behind
    "./2015.01.07/arbitrage. OS reports: No such file or directory" - and
    columns stored as another type than the schema declares.

    A partitioned kdb+ database needs every table in every partition, each
    holding the same columns; a gap fails the whole query, naming whichever
    table sorts first. Without --table, --attachment or --hdb: this runtime's HDB,
    against every table it declares. With one: that table alone, in the HDB
    they name (`uqs --target TARGET data check` is a release's own). --apply
    shows the plan, asks, and writes only what is missing - additive: a table
    directory or column file that exists is never touched.
    """
    tables = tables_given(table)
    if tables or template or attach or hdb:
        try:
            if as_json and len(tables) > 1:
                raise UqsError("--json reports one table: give --table once")
            handle = resolve(attach, hdb)
            schema = hdb_types.declared_schema(handle.paths)
            declared = hdb_shape.declared_tables(handle.paths)
            for name in tables or [_table(handle, None)]:
                with hdb_alter.declaration(schema, name, declared) as decl:
                    day = f"{parse_day('--template', template):%Y.%m.%d}" if template else None
                    req = Request(handle.hdb, name, "fill", schema=decl, template=day)
                    what = f"Write an empty {name} into each partition lacking it"
                    run_scoped(handle, req, apply=apply, yes=yes, as_json=as_json, what=what)
        except UqsError as exc:
            _die(exc)
        return
    _whole_runtime(apply=apply, yes=yes, as_json=as_json)


def _whole_runtime(*, apply: bool, yes: bool, as_json: bool) -> None:
    """Every declared table, in this runtime's own HDB."""
    paths = _paths()
    hdb_root = paths.hdb_dir
    if not hdb_root.is_dir():
        if as_json:
            emit({"ok": True, "hdb": str(hdb_root), "present": False})
        else:
            console.print(f"[yellow]no HDB at {hdb_root}[/] - nothing to check")
        return
    schema = hdb_types.declared_schema(paths)
    try:
        # before any write: a layout q cannot load whole is refused, never filled
        layout = hdb_layout.inspect(paths, hdb_root)
    except UqsError as exc:
        _die(exc)
        return
    if apply:
        try:
            handle = resolve(None, None)
            confirm(handle, "Write every missing declared table and column, fold the markout "
                    "configuration columns (contract_version, rate_timing, report_ccy) into "
                    "configuration snapshots, dropping them, and REWRITE each partition of a table "
                    "with a declared sort order (market_data: sym, source, source_time, p# on sym) "
                    "that is not in it, numbering its rows (rid) - stop the stack first",
                    yes=yes, as_json=as_json)  # fmt: skip
        except UqsError as exc:
            _die(exc)
            return
        # what bootstrap writes, so the filler fills from THIS release's schema
        paths.generated_schema.parent.mkdir(parents=True, exist_ok=True)
        paths.generated_schema.write_text(schema)
        hdb_fill.fill_hdb_partitions(paths, migrate=True)
        try:
            layout = hdb_layout.inspect(paths, hdb_root)
        except UqsError as exc:
            _die(exc)
            return
    declared = hdb_shape.declared_columns(paths)
    expected = set(declared)
    short, thin = hdb_shape.shape_gaps(layout.listing, declared)
    with tempfile.TemporaryDirectory() as tmp:
        schema_file = Path(tmp) / "database.q"
        schema_file.write_text(schema)
        try:
            changed = hdb_types.type_changes(paths, schema_file, layout.dirs)
        except UqsError as exc:
            _die(exc)
            return
    try:
        unordered = hdb_sorting.unsorted(paths, hdb_root, layout.dirs)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit({
            "ok": not (short or thin or changed or unordered),
            "hdb": str(hdb_root),
            "present": True,
            "segmented": layout.segmented,
            "partitions": len(layout.listing),
            # keyed by partition, then table: names, not keys the document spells
            "missing_tables": Verbatim({p: sorted(t) for p, t in short.items()}),
            "missing_columns": Verbatim(
                {p: {t: sorted(c) for t, c in by.items()} for p, by in thin.items()}
            ),
            "types_checked": changed is not None,
            "type_changes": changed or [],
            "unsorted": unordered or [],
        })  # fmt: skip
        if short or thin or changed or unordered:
            raise typer.Exit(code=1)
        return
    if changed is None:
        console.print("[dim]column types not checked: no q interpreter[/]")
    if not short and not thin and not changed and not unordered:
        where = " in the segments par.txt names" if layout.segmented else ""
        console.print(
            f"[green]{hdb_shape.describe(short)}, "
            f"and every declared column[/] ({len(expected)} tables, "
            f"{len(layout.listing)} partitions{where})"
        )
        return
    if short:
        console.print(f"[yellow]{hdb_shape.describe(short)}[/]")
    if thin:
        console.print(f"[yellow]{hdb_shape.describe_columns(thin)}[/]")
    for c in changed or []:
        console.print(
            f"[red]{c['partition']} {c['table']}.{c['column']}: stored as {c['on_disk']}, "
            f"declared {c['declared']}[/] - a migration, never filled"
        )
    for u in unordered or []:
        console.print(
            f"[yellow]{u['partition']} {u['table']}: {u['why']}[/] - `uqs data check --apply` "
            "rewrites it in its declared order (stop the stack first)"
        )
    console.print(
        "\n[dim]`uqs data check --apply` writes an empty copy of each missing "
        "table, and each missing column as its declared type's null (the `clock` of executions and "
        "market_data is attributed from the rows by scripts/gates/hdb_clock_rules.csv instead, and "
        "the markout contract's columns of fx_markout and fx_markout_wide are derived from the "
        "stored rows by scripts/gates/fill_hdb_partitions.q), "
        "after asking. Additive: "
        "an existing table directory or column file is never touched, and a column whose "
        "declared TYPE changed is not repaired here.[/]"
    )
    raise typer.Exit(code=1)
