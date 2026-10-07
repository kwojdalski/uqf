"""`uqs runtime ...`: comparing the declared runtimes (#763).

`uqs list runtimes` lists them; `uqs runtime diff A B` says what one has that
the other lacks - processes, tables, config layers and commands - read off
both declarations rather than off a table someone keeps by hand.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import _die, _paths, app, console
from uqs.paths import UqsError
from uqs.runtimes import RUNTIMES
from uqs.stack import runtime_report

runtime_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Compare the runtimes: `uqs runtime diff uqf torq`. `uqs list runtimes` lists them.",
)
app.add_typer(runtime_app, name="runtime")

_NAMES = list(RUNTIMES)


@runtime_app.command("diff")
def runtime_diff(
    a: Annotated[str, typer.Argument(help="A runtime", autocompletion=lambda: _NAMES)],
    b: Annotated[str, typer.Argument(help="Another runtime", autocompletion=lambda: _NAMES)],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the difference as JSON, for a script.")
    ] = False,
) -> None:
    """What runtime A has that B does not, and the reverse: processes, tables,
    config layers, and the commands only some runtimes run."""
    try:
        sections = runtime_report.diff(_paths().repo_root, a, b)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        print(json.dumps(sections, indent=2))
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
