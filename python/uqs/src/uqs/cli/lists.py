"""Listing, one way for every noun (#1728).

Each noun that adds or removes a thing lists it too, under its own group -
`uqs target list`, `uqs runtime list`, `uqs job list`, `uqs job attachment
list` - and every list takes the same output flags: --json, --export, --sort,
--reverse and -i. `uqs list KIND` stays for the stack's own tables (processes,
profiles, fields, env, dependencies, backfills); a kind that moved
to its noun still works there for one release, logging the command that lists
it now.

No `from __future__ import annotations` here: `_noun_list` builds each
command's signature at runtime, and Typer resolves string annotations against
this module's globals, where a factory's locals are not.
"""

from collections.abc import Callable
from typing import Annotated

import typer
from rich.table import Table

from uqs.cells import cells
from uqs.cli import completion, safety
from uqs.cli.attachment import attachment_app
from uqs.cli.config import config_app
from uqs.cli.json_out import JSON_HELP, emit, records
from uqs.cli.renames import warn_renamed
from uqs.cli.runtime_diff import runtime_app
from uqs.cli.shared import (
    JOB_AUTHORING_PANEL,
    BasePortOpt,
    ExportOpt,
    InteractiveOpt,
    _die,
    _export,
    _paths,
    _show,
    app,
    console,
    job_app,
)
from uqs.cli.sorting import _sorted_items
from uqs.cli.target import target_app
from uqs.deploy import targets
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import housekeeping, listing
from uqs.stack.env import build_env
from uqs.stack.procs import get_process_config

#: Kinds of `uqs list` whose one release is over (#2649): refused, naming the
#: command now, never listed - they are still kinds, which their nouns list.
REFUSED = {
    "targets": "uqs target list",
    "runtimes": "uqs runtime list",
    "jobs": "uqs job list",
    "attachments": "uqs job attachment list",
}

#: Kinds of `uqs list` that moved to their noun's own list -> the command now.
MOVED = {
    # #1858: every kind is its noun's now, and `uqs list` goes with the last
    "processes": "uqs process list",
    "profiles": "uqs profile list",
    "fields": "uqs config field list",
    "env": "uqs config env",
    "dependencies": "uqs graph --upstream --offline",
    "backfills": "uqs job attachment status",
    # process_overrides.csv's settings: `override` is the checks a command
    # bypasses (`uqs override list`), one word with one meaning (#1855)
    "overrides": "uqs config list",
}

ReverseOpt = Annotated[
    bool, typer.Option("--reverse", help="Sort descending. Only meaningful with --sort.")
]
JsonOpt = Annotated[
    bool,
    typer.Option(
        "--json",
        help=f'{JSON_HELP}: {{"<kind>": [{{column: value}}]}}, each value typed - a port a '
        "number, a flag true/false, a set of names a list.",
    ),
]

#: The key a kind's rows are under in its --json document, and in uqs-mcp's
#: list tool's, where it is not the kind's own name.
JSON_KEY = {"named overrides": "overrides", "env": "variables"}
_SORT_HELP = "Sort by this column (case-insensitive, numeric-aware)."


def _print(kind, base_port, export, sort, reverse, interactive, as_json, rows=None) -> None:
    """List `kind` - or `rows()`, when given - as a table, or as JSON, sorted
    and exported as asked."""
    try:
        items = rows() if rows else listing.list_items(_paths(), kind, base_port=base_port)
    except UqsError as exc:
        _die(exc)
    if kind == "targets" and not items and not as_json:
        console.print(
            f"no targets in {targets.declaration_path(_paths().repo_root)} - add one with "
            "`uqs target add`, or `uqs deploy push` to a server"
        )
        return
    items = _sorted_items(items, sort, reverse)
    shown = [cells(item) for item in items]  # what a table cell and an export hold
    if as_json:
        _export(shown, export)  # first: a failed export is then the only document
        emit({JSON_KEY.get(kind, kind): records(items)})
        return
    table = Table(title=f"{kind} ({len(items)})")
    if items:
        for col in items[0]:
            table.add_column(col)
        for item in shown:
            table.add_row(*item.values())
    _show(table, interactive)
    _export(shown, export)


def _noun_list(
    kind: str,
    doc: str,
    rows: Callable[[], list[dict]] | None = None,
    columns=(),
    base_port: bool = False,
) -> Callable[..., None]:
    """The `list` command of the noun whose rows are `kind` - or `rows()`, whose
    `columns` --sort completes; with `base_port`, resolved at `--base-port`."""
    columns = completion.choices(*columns) if rows else completion.columns_of(kind)
    sort_opt = Annotated[
        str | None,
        typer.Option("--sort", help=_SORT_HELP, autocompletion=columns),
    ]

    def command(
        export: ExportOpt = None,
        sort: sort_opt = None,
        reverse: ReverseOpt = False,
        interactive: InteractiveOpt = False,
        as_json: JsonOpt = False,
    ) -> None:
        _print(kind, None, export, sort, reverse, interactive, as_json, rows)

    def at_base_port(
        port: BasePortOpt = None,
        export: ExportOpt = None,
        sort: sort_opt = None,
        reverse: ReverseOpt = False,
        interactive: InteractiveOpt = False,
        as_json: JsonOpt = False,
    ) -> None:
        _print(kind, port, export, sort, reverse, interactive, as_json, rows)

    chosen = at_base_port if base_port else command
    chosen.__doc__ = doc + "\n\n`--sort` takes any of its columns; the order reaches `--export`."
    return chosen


target_app.command("list", cls=safety.ReadOnly)(
    _noun_list("targets", "List this machine's deploy targets.")
)
runtime_app.command("list", cls=safety.ReadOnly)(
    _noun_list("runtimes", "List the runtimes: whether each is here, and up.")
)
job_app.command("list", rich_help_panel=JOB_AUTHORING_PANEL, cls=safety.ReadOnly)(
    _noun_list("jobs", "List every streaming job, normalizer and bounded worker in the tree.")
)
attachment_app.command("list", cls=safety.ReadOnly)(
    _noun_list("attachments", "List every attachment: its state, and whether its publisher runs.")
)
config_app.command("list", cls=safety.ReadOnly)(
    _noun_list(
        "overrides",
        "List every process.csv field `uqs config set` changed: process_overrides.csv, by process.",
    )
)

#: The stack's own tables, each under its noun (#1858): what `uqs list KIND`
#: listed, a verb with seven kinds after every other noun listed its own.
process_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Processes the stack declares: list.\n\nAt the base port in effect; what runs is "
    "`uqs status`.",
)
app.add_typer(process_app, name="process")
process_app.command("list", cls=safety.ReadOnly)(
    _noun_list(
        "processes",
        "List every declared process, with `config set`'s changes applied.\n\nIts procname, "
        "proctype, port and startwithall, and the tables it reads and writes. Declared, not "
        "running: `uqs status` asks the stack.",
        base_port=True,
    )
)
profile_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Profiles: the named start sets `--profile` takes - list.",
)
app.add_typer(profile_app, name="profile")
profile_app.command("list", cls=safety.ReadOnly)(
    _noun_list("profiles", "List every start profile: what it starts, and its connection cost.")
)
field_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Fields: the process.csv columns `uqs config set` can change - list.",
)
config_app.add_typer(field_app, name="field")
field_app.command("list", cls=safety.ReadOnly)(
    _noun_list("fields", "List every process.csv column `uqs config set PROC FIELD` accepts.")
)
config_app.command("env", cls=safety.ReadOnly)(
    _noun_list(
        "env",
        "Show the stack's resolved environment: KDBBASEPORT, KDBHDB and the rest.\n\nAs "
        "`config show` resolves placeholders and torq.sh starts processes.",
        base_port=True,
    )
)
attachment_app.command("status", cls=safety.ReadOnly)(
    _noun_list(
        "backfills",
        "Show each destination a `uqs job backfill --attachment` has run into.\n\nIts "
        "attachment, worker, state, outcome, whether its rows are queryable, and its latest log.",
    )
)


def _housekeeping() -> list[dict]:
    paths = _paths()
    try:
        localtime = get_process_config(paths, "housekeeping1")["localtime"] != "0"
    except UqsError:  # a runtime without housekeeping1: the rules it would run
        localtime = True
    return housekeeping.effective_schedule(paths, build_env(paths), localtime)


housekeeping_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="housekeeping1's schedule: show.",
)
config_app.add_typer(housekeeping_app, name="housekeeping")
housekeeping_app.command("show", cls=safety.ReadOnly)(
    _noun_list(
        "housekeeping",
        "Show housekeeping1's effective schedule: when it runs, and each rule it runs.\n\n"
        "Log and tickerplant-log retention, and HDB compression by the declared policy, "
        "as generated into TORQDATA (stack/housekeeping.py).",
        _housekeeping,
        ("runs", "function", "on", "matching", "except", "older_than"),
    )
)

#: `uqs override ...`: the named checks a command can bypass (#1732).
override_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Overrides: the checks a command bypasses with `--override NAME --reason TEXT`: list.",
)
app.add_typer(override_app, name="override")


def _bypasses() -> list[dict]:
    return [
        {"override": b.name, "commands": list(b.commands), "effect": b.description}
        for b in safety.catalog()
    ]


override_app.command("list", cls=safety.ReadOnly)(
    _noun_list(
        "named overrides",
        "List every named override: the commands that take it, and the check it bypasses.",
        _bypasses,
        ("override", "commands", "effect"),
    )
)


def _kinds() -> list[str]:
    return sorted(k for k in listing.LISTABLE_KINDS if k not in MOVED and k not in REFUSED)


#: Every kind is its noun's (#1858): `uqs list` stays one release, hidden.
@app.command("list", hidden=True, cls=safety.ReadOnly)
def list_items(
    kind: Annotated[
        str | None,
        typer.Argument(
            metavar="KIND",
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
    """List one of the stack's own tables; with no KIND, list the kinds.

    Run with no argument to see the
    kinds: 'processes', 'profiles', 'fields' (process.csv's settable columns),
    'env' (the resolved KDBBASEPORT, KDBHDB, ...), 'dependencies' and
    'backfills' (what each attached backfill did). A noun's own records are
    listed by its group: `uqs target list`, `uqs runtime list`, `uqs job
    list`, `uqs job attachment list`, `uqs config list` (process_overrides.csv).

    `--sort` takes any column the chosen kind produces, which differ between
    kinds. The order reaches `--export` too, so an exported CSV matches what
    was on screen.
    """
    if kind is None:
        if as_json:
            emit({"kinds": _kinds()})
        else:
            console.print(f"Available kinds: {', '.join(_kinds())}")
        return
    if kind in REFUSED:
        _die(UqsUsageError(f"`uqs list {kind}` is `{REFUSED[kind]}`"))
    if kind in MOVED:
        warn_renamed(f"uqs list {kind}", MOVED[kind])
    _print(kind, port, export, sort, reverse, interactive, as_json)
