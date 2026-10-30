"""`uqs data check`: which HDB partitions lack a table or a column, and -
with --apply, after asking - the empty table or null column written in.

Two scopes (#1755). Without --table, --attach or --hdb it judges this
runtime's own HDB against every table this runtime declares, as it always
has: stack/hdb_shape.py reads the shape, scripts/gates/fill_hdb_partitions.q
fills it, stack/hdb_types.py compares the stored types. With any of them it
checks ONE table in the HDB they name - an attached stack's, say, where
filling uqf's whole schema would add tables production never asked for - and
fills only the partitions lacking it, from this runtime's declaration of the
table when there is one, else from a partition that holds it
(scripts/gates/hdb_alter.q -fill, cli/hdb_scope.py).
"""

from __future__ import annotations

import json
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
)
from uqs.cli.renames import alias_command
from uqs.cli.shared import _die, _paths, console, data_app
from uqs.paths import UqsError
from uqs.stack import hdb_alter, hdb_fill, hdb_shape, hdb_types
from uqs.stack.hdb_alter import Request


@data_app.command("check")
def hdb_check(
    table: Annotated[
        str | None,
        typer.Option(
            "--table",
            help="Check and fill only this table (default with --attach: the attachment's own)",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    template: Annotated[
        str | None,
        typer.Option(
            "--template",
            help="YYYY.MM.DD: the partition whose table is the schema to fill from, when this "
            "runtime declares none (default: the latest holding it)",
        ),
    ] = None,
    attach: AttachOpt = None,
    hdb: HdbOpt = None,
    apply: ApplyOpt = False,
    yes: YesOpt = False,
    fix: Annotated[
        bool, typer.Option("--fix", hidden=True, help="Retired: --apply, which asks first")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the findings as JSON, to script against")
    ] = False,
) -> None:
    """Report HDB partitions missing a table or a column - the cause behind
    "./2015.01.07/arbitrage. OS reports: No such file or directory" - and
    columns stored as another type than the schema declares.

    A partitioned kdb+ database needs every table in every partition, each
    holding the same columns; a gap fails the whole query, naming whichever
    table sorts first. Without --table, --attach or --hdb: this runtime's HDB,
    against every table it declares. With one: that table alone, in the HDB
    they name (`uqs --target NAME data check` is a release's own). --apply
    shows the plan, asks, and writes only what is missing - additive: a table
    directory or column file that exists is never touched.
    """
    if fix:
        _die(UqsError(
            "--fix is now --apply (#1732): `uqs data check --apply` shows what it would "
            "write and asks first; add --yes where nothing can ask"
        ))  # fmt: skip
        return
    if table or template or attach or hdb:
        try:
            handle = resolve(attach, hdb)
            name = _table(handle, table)
            schema = hdb_types.declared_schema(handle.paths)
            declared = hdb_shape.declared_tables(handle.paths)
            with hdb_alter.declaration(schema, name, declared) as decl:
                day = template.replace("-", ".") if template else None
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
            print(json.dumps({"hdb": str(hdb_root), "present": False}))
        else:
            console.print(f"[yellow]no HDB at {hdb_root}[/] - nothing to check")
        return
    schema = hdb_types.declared_schema(paths)
    if apply:
        try:
            handle = resolve(None, None)
            confirm(handle, "Write every missing declared table and column", yes=yes,
                    as_json=as_json)  # fmt: skip
        except UqsError as exc:
            _die(exc)
            return
        # what bootstrap writes, so the filler fills from THIS release's schema
        paths.generated_schema.parent.mkdir(parents=True, exist_ok=True)
        paths.generated_schema.write_text(schema)
        hdb_fill.fill_hdb_partitions(paths)
    declared = hdb_shape.declared_columns(paths)
    expected = set(declared)
    short, thin = hdb_shape.shape_gaps(hdb_shape.listing(hdb_root), declared)
    with tempfile.TemporaryDirectory() as tmp:
        schema_file = Path(tmp) / "database.q"
        schema_file.write_text(schema)
        try:
            changed = hdb_types.type_changes(paths, schema_file)
        except UqsError as exc:
            _die(exc)
            return
    if as_json:
        print(json.dumps({
            "hdb": str(hdb_root),
            "present": True,
            "missing_tables": {p: sorted(t) for p, t in short.items()},
            "missing_columns": {p: {t: sorted(c) for t, c in by.items()} for p, by in thin.items()},
            "types_checked": changed is not None,
            "type_changes": changed or [],
        }, indent=2))  # fmt: skip
        if short or thin or changed:
            raise typer.Exit(code=1)
        return
    if changed is None:
        console.print("[dim]column types not checked: no q interpreter[/]")
    if not short and not thin and not changed:
        console.print(
            f"[green]{hdb_shape.describe(short)}, "
            f"and every declared column[/] ({len(expected)} tables)"
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
    console.print(
        "\n[dim]`uqs data check --apply` writes an empty copy of each missing "
        "table, and each missing column as its declared type's null, after asking. Additive: "
        "an existing table directory or column file is never touched, and a column whose "
        "declared TYPE changed is not repaired here.[/]"
    )
    raise typer.Exit(code=1)


#: Its name before #1733: `check` actively probes and exits non-zero on a problem.
alias_command(data_app, "hdb-check", "uqs data check", hdb_check)
