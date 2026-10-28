"""`uqs setup odbc ...`: a deployment-owned, versioned ODBC setup (#840).

Under `setup` (#1417) because it prepares the machine the stack runs on -
infrastructure, not a job and not a process.

Thin over uqs.stack.odbc_home, whose docstring describes the package, the
home's layout and what env.sh sets.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import dry_run_note
from uqs.cli.shared import _die, app, console
from uqs.paths import UqsError
from uqs.stack import odbc_home

setup_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Infrastructure setup: prepare what the stack needs on this machine - "
    "a private ODBC setup. Changes no running process.",
)
app.add_typer(setup_app, name="setup")

odbc_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A private ODBC setup: install an approved package, load it, roll it back.",
)
setup_app.add_typer(odbc_app, name="odbc")

Home = Annotated[
    Path,
    typer.Option("--home", envvar=odbc_home.ODBC_HOME_ENV, help="The ODBC home directory"),
]


@odbc_app.command("install")
def install(
    package: Annotated[Path, typer.Argument(help="The package, a .tar.gz with package.json")],
    home: Home,
    qhome: Annotated[
        Path | None,
        typer.Option("--qhome", help="The managed QHOME to overlay; default: $QHOME"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Check the package's approval; install nothing"),
    ] = False,
) -> None:
    """Install an approved package as a new version and make it current."""
    managed = qhome or (Path(os.environ["QHOME"]) if os.environ.get("QHOME") else None)
    try:
        if managed is None:
            raise UqsError("no managed QHOME - set $QHOME or pass --qhome")
        version = odbc_home.install(home, package, managed, dry_run)
    except UqsError as exc:
        _die(exc)
        return
    if dry_run:
        console.print(f"would install {version.name} into {home} and make it current")
        console.print(dry_run_note())
        return
    console.print(f"installed {version.name}; load it with: . {home}/current/{odbc_home.ENV_FILE}")


@odbc_app.command("rollback")
def rollback(
    home: Home,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Name the version it would return to; change nothing"),
    ] = False,
) -> None:
    """Make the previous version current again."""
    try:
        version = odbc_home.rollback(home, dry_run)
    except UqsError as exc:
        _die(exc)
        return
    if dry_run:
        console.print(f"would make {version.name} current again")
        console.print(dry_run_note())
        return
    console.print(f"current is {version.name} again")


StatusJson = json_opt('{"home", "current", "previous", "versions": [name, ...]}')


@odbc_app.command("status")
def status(home: Home, as_json: StatusJson = False) -> None:
    """The installed versions, and which is current and previous."""
    try:
        found = odbc_home.status(home)
    except OSError as exc:
        _die(UqsError(f"cannot read {home}: {exc}"))
        return
    if as_json:
        emit(found)
        return
    table = Table(title=f"ODBC versions in {found['home']}")
    table.add_column("version")
    table.add_column("")
    for name in found["versions"]:
        mark = {found["current"]: "current", found["previous"]: "previous"}.get(name, "")
        table.add_row(name, mark)
    console.print(table)
    if not found["versions"]:
        console.print("[dim]nothing installed - `uqs setup odbc install` first[/]")


@odbc_app.command("env")
def env(home: Home) -> None:
    """The current version's variables, as shell exports."""
    version = odbc_home.current(home)
    if version is None:
        _die(UqsError(f"{home} has no current version - `uqs setup odbc install` first"))
        return
    print((version / odbc_home.ENV_FILE).read_text(), end="")
