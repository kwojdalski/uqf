"""`uqs data alter`: change one table across an HDB - reorder, rename,
attribute, cast, or undo one of those - dry run by default (#1755).

Only partitions holding the table are touched; partitions that disagree on
its shape are refused before any write; a journal is written beside the HDB
before the first write and `--undo JOURNAL` reverses the run; every partition
is read again at the end. All of that is scripts/gates/hdb_alter.q's
(stack/hdb_alter.py); cli/hdb_scope.py resolves the HDB and runs it.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.hdb_scope import ApplyOpt, AttachOpt, HdbOpt, YesOpt, _table, resolve, run_scoped
from uqs.cli.json_out import json_opt
from uqs.cli.renames import alias_command
from uqs.cli.safety import ConfirmsOnServer
from uqs.cli.shared import _die, data_app
from uqs.paths import UqsError
from uqs.stack.hdb_alter import Request

AlterJson = json_opt(
    '{"ok", "hdb", "resolved_from", "ssh", "warnings", "plan", "result"} - plan and result '
    "are hdb_alter.q's own: partitions, shapes, steps, journal, final_check"
)


@data_app.command("alter", cls=ConfirmsOnServer)
def hdb_alter(
    table: Annotated[
        str | None,
        typer.Argument(
            metavar="[TABLE]",
            help="The table to change (default with --attach: the attachment's own)",
            autocompletion=completion.plant_table,
            show_default=False,
        ),
    ] = None,
    attach: AttachOpt = None,
    hdb: HdbOpt = None,
    reorder: Annotated[
        str | None,
        typer.Option("--reorder", help="C1,C2,...: every existing column, in a new order"),
    ] = None,
    rename: Annotated[
        str | None, typer.Option("--rename", help="OLD=NEW: rename one column (file and .d)")
    ] = None,
    attr: Annotated[
        str | None,
        typer.Option("--attr", help="COL:p|s|g|u|none - set or clear one column's attribute"),
    ] = None,
    cast: Annotated[
        str | None,
        typer.Option(
            "--cast",
            help="COL:TYPE - cast one column (long, float, ... or a type letter); never "
            "to or from symbol",
        ),
    ] = None,
    undo: Annotated[
        str | None,
        typer.Option("--undo", help="A run's journal (as the plan names it): reverse that run"),
    ] = None,
    apply: ApplyOpt = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show the plan and write nothing (the default)"),
    ] = False,
    yes: YesOpt = False,
    as_json: AlterJson = False,
) -> None:
    """Change one table in every partition that holds it, and only those.

    One operation per run: --reorder, --rename, --attr, --cast, or --undo of
    an earlier run. Name the HDB with --attach NAME (run on the stack's host),
    --hdb DIR, or `uqs --target NAME data alter ...` for a release's own;
    with none, this runtime's. Shows the plan and writes nothing unless
    --apply, which asks first (--yes does not). Refused when the partitions
    holding the table disagree on its columns, order or types - `data check`
    repairs a missing table, anything else is for a person to decide.
    """
    given = {"reorder": reorder, "rename": rename, "attr": attr, "cast": cast, "undo": undo}
    chosen = {k: v for k, v in given.items() if v is not None}
    try:
        if len(chosen) != 1:
            raise UqsError(
                "give one operation: --reorder, --rename, --attr, --cast or --undo"
                + (f" (not {len(chosen)})" if chosen else "")
            )
        if dry_run and apply:
            raise UqsError("--dry-run and --apply contradict each other")
        (op, value), *_ = chosen.items()
        handle = resolve(attach, hdb)
        name = _table(handle, table, required=op != "undo")
        req = Request(handle.hdb, name, op, value)
        what = f"Undo {value}" if op == "undo" else f"{op} {value} on {name}"
        run_scoped(handle, req, apply=apply, yes=yes, as_json=as_json, what=what)
    except UqsError as exc:
        _die(exc)


#: Its name before #1733, beside `hdb-check`: `alter` is a domain verb.
alias_command(data_app, "hdb-alter", "uqs data alter", hdb_alter)
