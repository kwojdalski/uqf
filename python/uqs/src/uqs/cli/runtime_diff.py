"""`uqs runtime ...`: comparing the declared runtimes (#763).

`uqs runtime list` lists them; `uqs runtime diff A B` says what one has that
the other lacks - processes, tables, config layers and commands - read off
both declarations rather than off a table someone keeps by hand.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.json_out import emit, json_opt
from uqs.cli.pipelines import pipelines_only
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import _die, _paths, app, console
from uqs.paths import UqsError
from uqs.runtimes import RUNTIMES
from uqs.stack import runtime_report

runtime_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Runtimes: list, diff, prepare.\n\n"
    "Compare the runtimes (`uqs runtime diff uqf torq`) and install the bundles one "
    "declares (`uqs runtime prepare`: `uqs job install` for each); `uqs runtime list` "
    "lists them.",
)
app.add_typer(runtime_app, name="runtime")

_NAMES = list(RUNTIMES)


DiffJson = json_opt(
    '{"<section>": {"only_<a>": [...], "only_<b>": [...]}} - one section each for '
    "processes, tables, layers and commands"
)


@runtime_app.command("diff", cls=ReadOnly)
def runtime_diff(
    a: Annotated[
        str,
        typer.Argument(
            metavar="RUNTIME", help="A runtime, e.g. uqf", autocompletion=lambda: _NAMES
        ),
    ],
    b: Annotated[
        str,
        typer.Argument(
            metavar="RUNTIME",
            help="Another runtime, e.g. uqf or torq",
            autocompletion=lambda: _NAMES,
        ),
    ],
    as_json: DiffJson = False,
) -> None:
    """Compare two runtimes: what each has that the other does not.

    What the first RUNTIME has that the second does not, and the reverse: processes, tables,
    config layers, and the commands only some runtimes run."""
    try:
        sections = runtime_report.diff(_paths().repo_root, a, b, pipelines_only())
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit(sections)
        return
    for section, sides in sections.items():
        if not any(sides.values()):
            console.print(f"[dim]{section}: the same[/]")
            continue
        table = Table(title=section)
        for side in sides:
            table.add_column(side.replace("_", " "))
        left, right = sides.values()
        for i in range(max(len(left), len(right))):
            table.add_row(left[i] if i < len(left) else "", right[i] if i < len(right) else "")
        console.print(table)
