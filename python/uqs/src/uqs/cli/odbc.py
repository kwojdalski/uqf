"""`uqs odbc ...`: a deployment-owned, versioned ODBC setup (#840).

Thin over uqs.stack.odbc_home, whose docstring describes the package, the
home's layout and what env.sh sets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli.shared import _die, app, console
from uqs.paths import UqsError
from uqs.stack import odbc_home

odbc_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A private ODBC setup: install an approved package, load it, roll it back.",
)
app.add_typer(odbc_app, name="odbc")

Home = Annotated[
    Path,
    typer.Option("--home", envvar="UQS_ODBC_HOME", help="The ODBC home directory"),
]


@odbc_app.command("install")
def install(
    package: Annotated[Path, typer.Argument(help="The package, a .tar.gz with package.json")],
    home: Home,
    qhome: Annotated[
        Path | None,
        typer.Option("--qhome", help="The managed QHOME to overlay; default: $QHOME"),
    ] = None,
) -> None:
    """Install an approved package as a new version and make it current."""
    managed = qhome or (Path(os.environ["QHOME"]) if os.environ.get("QHOME") else None)
    try:
        if managed is None:
            raise UqsError("no managed QHOME - set $QHOME or pass --qhome")
        version = odbc_home.install(home, package, managed)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"installed {version.name}; load it with: . {home}/current/{odbc_home.ENV_FILE}")


@odbc_app.command("rollback")
def rollback(home: Home) -> None:
    """Make the previous version current again."""
    try:
        version = odbc_home.rollback(home)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"current is {version.name} again")


@odbc_app.command("status")
def status(home: Home) -> None:
    """The installed versions, and which is current and previous."""
    try:
        print(json.dumps(odbc_home.status(home), indent=2))
    except OSError as exc:
        _die(UqsError(f"cannot read {home}: {exc}"))


@odbc_app.command("env")
def env(home: Home) -> None:
    """The current version's variables, as shell exports."""
    version = odbc_home.current(home)
    if version is None:
        _die(UqsError(f"{home} has no current version - `uqs odbc install` first"))
        return
    print((version / odbc_home.ENV_FILE).read_text(), end="")
