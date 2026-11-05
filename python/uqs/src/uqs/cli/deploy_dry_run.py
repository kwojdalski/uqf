"""What `uqs deploy build --dry-run`, `push --dry-run`, `prune --dry-run` and
`rollback --dry-run` say (#1880, #1902).

Beside cli/deploy.py, which is at its line budget. build's dry run printed the
raw JSON composition - with no --json asked for, no "would build" and no note -
so it now says what it would build, as prose, and ends as every dry run does.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from uqs.cli.safety import dry_run_note
from uqs.cli.shared import console

BuildDryRun = Annotated[
    bool,
    typer.Option("--dry-run", "-n", help="Show the release it would build; build nothing"),
]


def say_composition(composition: dict) -> None:
    """The runtime and each bundle a build would carry, then the note."""
    bundles = composition["bundles"]
    count = f"{len(bundles) or 'no'} bundle{'' if len(bundles) == 1 else 's'}"
    console.print(f"would build a {composition['runtime']} release carrying {count}", markup=False)
    for b in bundles:
        console.print(
            f"  {b['name']} {b['version']} ({b['source']}) from {b['folder']}", markup=False
        )
    console.print(dry_run_note())


def end(dry_run: bool, code: int, as_json: bool = False) -> None:
    """A dry run's note, once it planned everything: after the plan on
    stdout, or - under --json, where stdout is the one document - on stderr
    (#2125)."""
    if dry_run and not code:
        (Console(stderr=True) if as_json else console).print(dry_run_note())
