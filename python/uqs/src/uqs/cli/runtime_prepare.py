"""`uqs runtime prepare`: install the bundles the selected runtime declares (#852).

The declaration is runtime_bundles.json (stack/runtime_bundles.py); `--bundle`
adds folders to it for one run. The composition is resolved the way
`uqs deploy build` resolves it, every bundle is planned and checked before
the first is installed, and nothing is started, queried or backfilled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.regenerate import _DERIVED, _regenerate_derived
from uqs.cli.runtime_diff import runtime_app
from uqs.cli.shared import _die, _paths, console
from uqs.paths import UqsError
from uqs.stack import qtree, runtime_bundles
from uqs.stack.bundle_blocks import BundleError


@runtime_app.command("prepare")
def prepare(
    bundle: Annotated[
        list[Path] | None,
        typer.Option("--bundle", help="A bundle folder beside the declared ones; repeatable"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the resolved composition; write nothing")
    ] = False,
) -> None:
    """Install the selected runtime's declared sidecar bundles into the tree.

    `uqs --runtime crypto runtime prepare` installs what runtime_bundles.json
    lists for crypto, so its jobs, their dependencies and their tables are in
    that runtime's stack and in no runtime that does not declare them. A
    bundle the runtime no longer declares leaves its stack, and stays
    installed. Profiles still decide what starts.
    """
    paths = _paths()
    runtime = paths.runtime
    try:
        members = runtime_bundles.resolve(runtime, paths.repo_root, bundle or [])
        plans = runtime_bundles.plan_all(members, paths.repo_root)
        # A flattened runtime loads a converted copy (stack/qtree.py): the
        # bundles' q is converted with the tree, in memory, before anything
        # is installed - so a refusal fails a dry run too, and writes nothing.
        converted = (
            qtree.check(paths.repo_root, runtime_bundles.planned_q(plans, paths.repo_root))
            if qtree.is_flattened(paths)
            else None
        )
    except (BundleError, UqsError) as exc:
        _die(exc)
        return
    table = Table(title=f"runtime {runtime}", title_justify="left", header_style="bold cyan")
    for col in ("Bundle", "Version", "From", "Jobs", "Tables"):
        table.add_column(col)
    for member, plan in zip(members, plans, strict=True):
        jobs = [i.source.stem for i in plan.jobs]
        table.add_row(
            member.bundle.name,
            member.bundle.version,
            member.source,
            ", ".join(jobs) or "-",
            ", ".join(plan.tables) or "-",
        )
    console.print(table if members else f"[dim]runtime {runtime} declares no bundles[/]")
    if converted is not None:
        console.print(
            f"[dim]q tree: {converted['q_files']} q file(s) convert for PeachQ, "
            f"{converted['transformed']} rewritten; `start` builds and checks the tree[/]"
        )
    if dry_run:
        console.print("[dim]--dry-run: nothing written[/]")
        return
    try:
        runtime_bundles.prepare(runtime, members, paths.repo_root)
    except (BundleError, OSError) as exc:
        _die(BundleError(str(exc)))
        return
    for script, regen in zip(_DERIVED, _regenerate_derived(paths.repo_root), strict=True):
        if regen.returncode:
            console.print(f"[red]✗ could not regenerate[/] - run `python3 {script}`")
            raise typer.Exit(code=1)
    console.print(
        f"[green]✓[/] runtime {runtime}: {len(members)} bundle(s). Nothing was started - "
        f"`uqs --runtime {runtime} start --profile ...` decides what runs."
    )
