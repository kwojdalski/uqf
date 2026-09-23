"""The two commands that WRITE code: `new-job` and `new-process`.

`new-job` scaffolds an ETL job into the tree; `new-process` is the console
wizard for a vendored-stack process. Together here because both create files
rather than act on a running fleet. See cli/lifecycle.py for why the split is
shaped this way.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqf_stack import core
from uqf_stack.cli.shared import (
    PortOpt,
    _die,
    _paths,
    app,
    console,
)
from uqf_stack.scaffold import jobs, wizard


@app.command("new-job")
def new_job(
    name: Annotated[str, typer.Argument(help="Job name: a q namespace and a filename")],
    kind: Annotated[
        str, typer.Option("--kind", help="'streaming' (default) or 'backfill'")
    ] = "streaming",
    subscribes: Annotated[
        str | None,
        typer.Option("--subscribes", help="Comma-separated tables it reads. Omit for a feed."),
    ] = None,
    publishes: Annotated[
        str | None, typer.Option("--publishes", help="The table it writes (streaming)")
    ] = None,
    dataset: Annotated[
        str | None, typer.Option("--dataset", help="The table it fills (backfill)")
    ] = None,
    columns: Annotated[
        str | None,
        typer.Option("--columns", help="Its table's columns: 'sym:symbol, value:float'"),
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Source name (backfill; defaults to NAME)")
    ] = None,
    width: Annotated[
        str, typer.Option("--width", help="Backfill window width, as a q timespan")
    ] = "1D",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be written, write nothing")
    ] = False,
) -> None:
    """Scaffold a new ETL job: its q files, its table, and its registry entry.

    Writes the SHAPE, never the logic. The generated handler throws and the
    generated test fails, on purpose - a scaffold that left something green
    behind would make "generated" and "implemented" look the same from
    outside, which is the state that produces a process reporting `up` while
    publishing nothing.

    Streaming, reading two tables and writing one:

        uqf-stack new-job markout2 --subscribes trades,quote \\
            --publishes my_metric --columns "sym:symbol, value:float"

    Bounded worker, with its source and transform:

        uqf-stack new-job fx_rates --kind backfill --dataset fx_rates \\
            --columns "sym:symbol, mid:float" --width 1D
    """
    subs = [s.strip() for s in (subscribes or "").split(",") if s.strip()]
    try:
        if kind == "streaming":
            plan = jobs.streaming_job(name, subs, publishes, columns)
        elif kind == "backfill":
            if not dataset:
                _die(core.UqfStackError("--kind backfill needs --dataset: the table it fills"))
                return
            if not columns:
                _die(core.UqfStackError("--kind backfill needs --columns for its dataset"))
                return
            plan = jobs.bounded_worker(name, dataset, columns, width=width, source=source)
        else:
            _die(core.UqfStackError(f"--kind must be 'streaming' or 'backfill', not {kind!r}"))
            return
    except core.UqfStackError as exc:
        _die(exc)
        return

    console.print(plan.render())
    if dry_run:
        console.print("[dim]--dry-run: nothing written[/]")
        return
    try:
        written = jobs.apply_plan(plan, _paths().repo_root)
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(f"\n[green]scaffolded {len(written)} file(s)[/]")
    for note in plan.notes:
        console.print(f"  [yellow]next[/] {note}")


@app.command("new-process")
def new_process(port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Interactive wizard: add a new uqf stack process. Opens with a menu of
    recipes - "FX quotes feed" and "cross-rate reprice ETL" are fully
    working (answer a few prompts, no q editing needed), "blank
    publisher"/"blank subscriber" write a Stage-1-only skeleton .q file for
    q/kdb+ users to finish by hand (see docs/guides/uqf-stack.md). Registers
    whatever gets built and optionally starts it to verify it's alive.
    """
    try:
        wizard.run(_paths(), base_port=port)
    except core.UqfStackError as exc:
        _die(exc)
