"""Listing, one way for every noun (#1728).

Each noun that adds or removes a thing lists it too, under its own group -
`uqs target list`, `uqs runtime list`, `uqs job list`, `uqs job attachment
list` - and every list takes the same output flags: --json, --export, --sort,
--reverse and -i. `uqs list KIND` stays for the stack's own tables (processes,
profiles, fields, overrides, env, dependencies, backfills); a kind that moved
to its noun still works there for one release, logging the command that lists
it now.

No `from __future__ import annotations` here: `_noun_list` builds each
command's signature at runtime, and Typer resolves string annotations against
this module's globals, where a factory's locals are not.
"""

import json
from collections.abc import Callable
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli import completion
from uqs.cli.renames import BASE_PORT, renamed, warn_renamed
from uqs.cli.runtime_diff import runtime_app
from uqs.cli.shared import (
    JOB_AUTHORING_PANEL,
    JOB_OPERATIONS_PANEL,
    BasePortOpt,
    ExportOpt,
    InteractiveOpt,
    _die,
    _export,
    _paths,
    _show,
    _sorted_items,
    app,
    console,
    job_app,
)
from uqs.cli.target import target_app
from uqs.deploy import targets
from uqs.paths import UqsError
from uqs.stack import listing

#: Kinds of `uqs list` that moved to their noun's own list -> the command now.
MOVED = {
    "targets": "uqs target list",
    "runtimes": "uqs runtime list",
    "jobs": "uqs job list",
    "attachments": "uqs job attachment list",
}

#: `uqs job attachment ...`: the attachments `uqs job attach` makes.
attachment_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Attachments: jobs put on a TorQ stack already running (`uqs job attach`): list.",
)
job_app.add_typer(attachment_app, name="attachment", rich_help_panel=JOB_OPERATIONS_PANEL)

ReverseOpt = Annotated[
    bool, typer.Option("--reverse", help="Sort descending. Only meaningful with --sort.")
]
JsonOpt = Annotated[
    bool, typer.Option("--json", help="Print the rows as a JSON list, for a script.")
]
_SORT_HELP = "Sort by this column (case-insensitive, numeric-aware)."


def _print(kind, base_port, export, sort, reverse, interactive, as_json) -> None:
    """List `kind` as a table, or as JSON, sorted and exported as asked."""
    try:
        items = listing.list_items(_paths(), kind, base_port=base_port)
    except UqsError as exc:
        _die(exc)
        return
    if kind == "targets" and not items and not as_json:
        console.print(
            f"no targets in {targets.declaration_path(_paths().repo_root)} - add one with "
            "`uqs target add`, or `uqs deploy push` to a server"
        )
        return
    items = _sorted_items(items, sort, reverse)
    if as_json:
        print(json.dumps(items, indent=2))
        _export(items, export)
        return
    table = Table(title=f"{kind} ({len(items)})")
    if items:
        for col in items[0]:
            table.add_column(col)
        for item in items:
            table.add_row(*item.values())
    _show(table, interactive)
    _export(items, export)


def _noun_list(kind: str, doc: str) -> Callable[..., None]:
    """The `list` command of the noun whose rows are `kind`."""
    sort_opt = Annotated[
        str | None,
        typer.Option("--sort", help=_SORT_HELP, autocompletion=completion.columns_of(kind)),
    ]

    def command(
        export: ExportOpt = None,
        sort: sort_opt = None,
        reverse: ReverseOpt = False,
        interactive: InteractiveOpt = False,
        as_json: JsonOpt = False,
    ) -> None:
        _print(kind, None, export, sort, reverse, interactive, as_json)

    command.__doc__ = doc + "\n\n`--sort` takes any of its columns; the order reaches `--export`."
    return command


target_app.command("list")(_noun_list("targets", "List this machine's deploy targets."))
runtime_app.command("list")(
    _noun_list("runtimes", "List the runtimes: whether each is here, and up.")
)
job_app.command("list", rich_help_panel=JOB_AUTHORING_PANEL)(
    _noun_list("jobs", "List every streaming job, normalizer and bounded worker in the tree.")
)
attachment_app.command("list")(
    _noun_list("attachments", "List every attachment: its state, and whether its publisher runs.")
)


def _kinds() -> list[str]:
    return sorted(k for k in listing.LISTABLE_KINDS if k not in MOVED)


@app.command("list", cls=renamed(BASE_PORT))
def list_items(
    kind: Annotated[
        str | None,
        typer.Argument(
            help="Which of the stack's tables - run with no argument to see every kind",
            autocompletion=completion.list_kinds,
        ),
    ] = None,
    port: BasePortOpt = None,
    export: ExportOpt = None,
    sort: Annotated[
        str | None,
        typer.Option("--sort", help=_SORT_HELP, autocompletion=completion.list_columns),
    ] = None,
    reverse: ReverseOpt = False,
    interactive: InteractiveOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """List one of the stack's own tables - run with no argument to see the
    kinds: 'processes', 'profiles', 'fields' (process.csv's settable columns),
    'overrides' (process_overrides.csv), 'env' (the resolved KDBBASEPORT,
    KDBHDB, ...), 'dependencies' and 'backfills' (what each attached backfill
    did). A noun's own records are listed by its group: `uqs target list`,
    `uqs runtime list`, `uqs job list`, `uqs job attachment list`.

    `--sort` takes any column the chosen kind produces, which differ between
    kinds. The order reaches `--export` too, so an exported CSV matches what
    was on screen.
    """
    if kind is None:
        if as_json:
            print(json.dumps(_kinds()))
        else:
            console.print(f"Available kinds: {', '.join(_kinds())}")
        return
    if kind in MOVED:
        warn_renamed(f"uqs list {kind}", MOVED[kind])
    _print(kind, port, export, sort, reverse, interactive, as_json)
