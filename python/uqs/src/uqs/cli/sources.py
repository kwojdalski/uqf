"""`uqs config sources`: which sources.csv the stack reads, and what is in it.

Its own module rather than more of cli/config.py, which is about one
process's process.csv row; this is about what every external source
connects to (#718). See uqs.stack.source_settings for the file's rules.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.config import config_app
from uqs.cli.shared import _die, _paths, console
from uqs.model import transports
from uqs.paths import UqsError
from uqs.stack import source_settings

sources_app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Show which sources.csv the stack reads, and each configured source - never a secret.",
)
config_app.add_typer(sources_app, name="sources")


@sources_app.callback()
def sources_show(ctx: typer.Context) -> None:
    """The selected sources.csv and, per source, where its credential comes
    from and anything that would stop it connecting. Secrets are reported as
    set or not set, never shown."""
    if ctx.invoked_subcommand is not None:
        return
    paths = _paths()
    try:
        path, rows = source_settings.describe(paths, transports.names())
    except UqsError as exc:
        _die(exc)
        return
    searched = ", ".join(str(p) for p in source_settings.layers(paths))
    if path is None:
        console.print(
            f"no sources.csv in any config layer (looked in {searched}) - every source "
            "uses UQF_SOURCE_CRED_<SOURCE> or its fixture"
        )
        return
    console.print(f"reading {path}")
    if not rows:
        console.print(
            "no source has a row - every source uses UQF_SOURCE_CRED_<SOURCE> or its fixture"
        )
        return
    table = Table()
    for col in ("source", "transport", "credential from", "secret_env", "problems"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            row["source"],
            row["transport"],
            row["origin"],
            row["secret_env"],
            "; ".join(row["problems"]) or "-",
        )
    console.print(table)


@sources_app.command("stub")
def sources_stub(
    source: Annotated[
        str, typer.Argument(help="The source, as src/etl/sources/<source>.q names it")
    ],
) -> None:
    """Add a SCAFFOLDED row for SOURCE to the operator's sources.csv (the
    application layer, gitignored). Keeps every row the stack reads now and
    never changes an existing one; the source refuses to connect until its
    setting is written."""
    paths = _paths()
    try:
        names = transports.names()
        transport = source_settings.declared_transport(
            paths.repo_root, source, transports.default()
        )
        console.print(source_settings.add_stub(paths, source, transport, names))
    except UqsError as exc:
        _die(exc)
