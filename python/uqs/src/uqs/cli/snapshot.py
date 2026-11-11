"""`uqs data snapshot ...`: reading the configuration snapshots results name (#1999).

A markout row carries `config_id`, one reference to the write-once snapshot of the
configuration that produced it: the contract and code revisions, the horizons,
reference-age limit, reporting currency, conversion timing and declared rates, and
the revision of the rules that price an instrument. Both commands here only read it
- from q's store in the status directory, through a short-lived q (stack/snapshots.py)
- so neither can change a snapshot, and nothing here keeps a copy of one.

Not here, on purpose: the other direction, from a result to its snapshot. A reader of
rows has it in the rows (`config_id`) and in the enriched read q offers
(`.qpipe.transform.config_markout.enrich`); a command that scans the HDB for it can be
added when someone asks.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.json_out import emit, json_opt
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import (
    ExportOpt,
    InteractiveOpt,
    _die,
    _export,
    _paths,
    _show,
    console,
    data_app,
)
from uqs.cli.sorting import ReverseOpt, SortOpt, _sorted_items
from uqs.paths import UqsError
from uqs.stack import snapshots
from uqs.timefmt import timestamp

#: What every `data snapshot` command needs: a runtime without it refuses them.
_STORE = "reads this tree's configuration snapshot store"

snapshot_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Configuration snapshots: list, show.\n\n"
    "The write-once record of what a markout was calculated under, named by each "
    "result row's config_id.",
)
data_app.add_typer(snapshot_app, name="snapshot")

ListJson = json_opt(
    '{"snapshots": [{id, kind, datasets, digest, created_at, actor, reason, digest_ok}], '
    '"ok"} - ok is false when any digest does not match its content'
)
ShowJson = json_opt(
    '{"snapshot": {id, kind, datasets, digest, created_at, actor, reason, digest_ok}, '
    '"parameters": {name: value as q text}}'
)


def _record(row: dict) -> dict:
    """One store row as JSON: the id as `id`, the time as ISO-8601 with its offset."""
    return {
        "id": row["snapshot_id"],
        "kind": row["kind"],
        "datasets": snapshots._as_list(row.get("datasets")),
        "digest": row["digest"],
        "created_at": timestamp(row["created_at"], naive="utc"),
        "actor": row["actor"],
        "reason": row["reason"],
        "digest_ok": bool(row["digest_ok"]),
    }


@snapshot_app.command("list", cls=needs_pipelines(_STORE, ReadOnly))
def list_snapshots(
    interactive: InteractiveOpt = False,
    dataset: Annotated[
        str | None,
        typer.Option(
            "--dataset",
            metavar="NAME",
            help="Only the snapshots that configure this markout dataset, e.g. fx_markout",
            autocompletion=completion.markout_datasets,
        ),
    ] = None,
    export: ExportOpt = None,
    sort: SortOpt = None,
    reverse: ReverseOpt = False,
    as_json: ListJson = False,
) -> None:
    """List the configuration snapshots, oldest first.

    Each row is one write-once snapshot: its id (a result's config_id), the datasets
    it configures, when it was recorded, by whom and why, and whether its digest still
    matches its content. A digest that does not is reported, not hidden - the store
    was changed outside the interface - and the command exits 1.

    The list takes every listing's flags: --export, --sort, --reverse, -i.
    """
    try:
        rows = _sorted_items(
            [_record(r) for r in snapshots.listing(_paths(), dataset)], sort, reverse
        )
    except UqsError as exc:
        _die(exc)
    bad = [r["id"] for r in rows if not r["digest_ok"]]
    _export(rows, export)
    if as_json:
        emit({"snapshots": rows, "ok": not bad})
    else:
        _print_list(rows, bad, interactive)
    if bad:
        raise typer.Exit(code=1)


def _print_list(rows: list[dict], bad: list[str], interactive: bool) -> None:
    if not rows:
        console.print("[dim]no configuration snapshot is recorded here yet[/]")
        return
    table = Table(title="configuration snapshots")
    for col in ("id", "datasets", "created (UTC)", "actor", "reason", "digest_ok"):
        table.add_column(col, no_wrap=col == "id")
    for r in rows:
        ok = "yes" if r["digest_ok"] else "[bold red]NO[/]"
        table.add_row(
            r["id"], ", ".join(r["datasets"]), r["created_at"] or "", r["actor"], r["reason"], ok
        )
    _show(table, interactive)
    if bad:
        console.print(
            f"[bold red]digest mismatch: {', '.join(bad)} - the stored content is not what "
            "was recorded; the store was changed outside the interface[/]"
        )


@snapshot_app.command("show", cls=needs_pipelines(_STORE, ReadOnly))
def show(
    snapshot_id: Annotated[
        str,
        typer.Argument(
            metavar="SNAPSHOT_ID",
            help="A snapshot id, from `uqs data snapshot list` or a result's config_id",
            autocompletion=completion.snapshot_ids,
        ),
    ],
    as_json: ShowJson = False,
) -> None:
    """Show one snapshot: every field it recorded and every parameter.

    The digest is checked on the way in: a snapshot whose content no longer matches
    its digest is refused by id, never displayed as if it could be trusted.
    """
    try:
        row, fields = snapshots.show(_paths(), snapshot_id)
    except UqsError as exc:
        _die(exc)
    record = _record(row)
    own = {"snapshot_id", "kind", "digest", "created_at", "actor", "reason"}
    parameters = {f["field"]: f["text"] for f in fields if f["field"] not in own}
    if as_json:
        emit({"snapshot": record, "parameters": parameters})
        return
    table = Table(title=f"snapshot {snapshot_id}")
    table.add_column("field")
    table.add_column("value", overflow="fold")
    for key in ("id", "kind", "datasets", "digest", "created_at", "actor", "reason"):
        value = record[key]
        table.add_row(key, ", ".join(value) if isinstance(value, list) else str(value))
    for name, text in parameters.items():
        table.add_row(name, text)
    console.print(table)
