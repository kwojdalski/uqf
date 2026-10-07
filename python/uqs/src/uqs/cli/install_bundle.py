"""`uqs job install <bundle>`: the bundle half of `uqs job install` (#800).

A folder holding bundle.json is a bundle (stack/bundles.py): its jobs AND its
tables, catalog entries and process overrides go in together, as copies,
replacing whatever an earlier version of the same bundle put there. Nothing
is asked about individual files - a bundle's contents are decided by its
owner, and every refusal (a conflict with the tree or another bundle) comes
before anything is written.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.prompt import Confirm
from rich.table import Table

from uqs.cli.regenerate import _DERIVED, _regenerate_derived
from uqs.cli.shared import _die, console
from uqs.stack import bundles
from uqs.stack.install import Mode, Status


def _summary(plan: bundles.Plan, root: Path) -> Table:
    table = Table(
        title=f"Bundle {plan.bundle.name} {plan.bundle.version}",
        title_justify="left",
        header_style="bold cyan",
    )
    table.add_column("What")
    table.add_column("Into")
    table.add_column("Status")
    for item in plan.jobs:
        assert item.destination is not None
        table.add_row(item.source.name, str(item.destination.relative_to(root)), item.status.value)
    for path in plan.stale:
        table.add_row("-", str(path.relative_to(root)), "removed: no longer in the bundle")
    if plan.tables:
        table.add_row(", ".join(plan.tables), "src/etl/plant_tables.q", "block")
    if plan.catalog_body:
        table.add_row("catalog entries", "scripts/processes/uqs_catalog.q", "block")
    for proc, fld, value in plan.overrides:
        table.add_row(f"{proc} {fld}={value}", str(bundles.OVERRIDES_FILE), "override")
    return table


def install(folder: Path, root: Path, *, mode: Mode | None, dry_run: bool, yes: bool) -> None:
    if mode is Mode.SYMLINK:
        _die(bundles.BundleError("a bundle installs as copies only - drop --mode symlink"))
        return
    try:
        plan = bundles.plan(bundles.read_bundle(folder), root)
    except bundles.BundleError as exc:
        _die(exc)
        return
    console.print(_summary(plan, root))
    if plan.tests:
        console.print(f"[dim]not installed (q tests): {', '.join(p.name for p in plan.tests)}[/]")
    if dry_run:
        console.print("[dim]--dry-run: nothing installed[/]")
        return
    changes = any(i.status is not Status.SAME for i in plan.jobs) or plan.stale
    if not yes and not Confirm.ask(
        f"Install bundle {plan.bundle.name} {plan.bundle.version}?", default=True, console=console
    ):
        console.print("[dim]Nothing installed.[/]")
        raise typer.Exit(code=1)
    try:
        entry = bundles.install(plan, root)
    except (bundles.BundleError, OSError) as exc:
        _die(bundles.BundleError(str(exc)))
        return
    console.print(
        f"[green]✓[/] bundle {plan.bundle.name} {entry['version']}: "
        f"{len(entry['files'])} job file(s), {len(entry['tables'])} table(s), "
        f"{len(entry['overrides'])} override(s)" + ("" if changes else " - unchanged")
    )
    for script, regen in zip(_DERIVED, _regenerate_derived(root), strict=True):
        if regen.returncode:
            console.print(
                f"[red]✗ could not regenerate[/] - run `python3 {script}`:\n"
                f"{regen.stdout}{regen.stderr}"
            )
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/] regenerated via {script}")
    streaming = [j["procname"] for j in entry["jobs"] if j["kind"] == "streaming"]
    workers = [j["name"] for j in entry["jobs"] if j["kind"] == "worker"]
    if streaming:
        console.print(f"Start: [green]uqs start {' '.join(streaming)}[/]")
    for worker in workers:
        run = f"uqs backfill {worker} --version <v> --from <date> --to <date>"
        console.print(f"Installed, not run: [green]{run}[/]")
