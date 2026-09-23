"""The two commands that WRITE code: `new-job` and `new-process`.

`new-job` scaffolds an ETL job into the tree; `new-process` is the console
wizard for a vendored-stack process. Together here because both create files
rather than act on a running fleet. See cli/lifecycle.py for why the split is
shaped this way.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli.shared import (
    PortOpt,
    _die,
    _paths,
    app,
    console,
)
from uqs.model.declarations import declaration_calls, symbols
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.model.schemas import _DEFINITION
from uqs.paths import (
    MAN_REGISTRY_SCRIPT,
    OPERATIONAL_DOCS_SCRIPT,
    SOURCE_DIR,
    TABLES_FILE,
    WORKER_DIR,
    UqsError,
)
from uqs.scaffold import jobs, wizard, write

#: What a scaffold makes stale, each checked in CI with --check: the registry's
#: derived files (processes.md, src/etl/generated/pipeline_dag.q), and
#: docs/man.q, generated from the qDoc blocks of the q files just written.
_DERIVED = (OPERATIONAL_DOCS_SCRIPT, MAN_REGISTRY_SCRIPT)


def _regenerate_derived(repo_root: Path) -> list[subprocess.CompletedProcess[str]]:
    """Rewrite every derived file a scaffold makes stale, and return each run.

    A scaffold that wrote its files and stopped there left the build red on
    files nobody is meant to edit. SUBPROCESSES rather than calls: this process
    imported the registry before the scaffold appended to it, so an in-process
    call would regenerate from the old one.
    """
    return [
        subprocess.run(
            [sys.executable, str(repo_root / script)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        for script in _DERIVED
    ]


def _unpartitioned_workers_filling(repo_root: Path, dataset: str) -> list[str]:
    """Workers that already fill `dataset` without declaring a partition.

    `.qbw.define` refuses two workers on one dataset AND partition (#60,
    #185), and a scaffolded worker declares none - so a second one on such a
    dataset is a tree that no longer LOADS. Caught here, before anything is
    written, rather than as a bare error from inside a declaration.
    """
    found = []
    for path in sorted((repo_root / WORKER_DIR).glob("*.q")):
        for fn, name, fields in declaration_calls(path.read_text()):
            if fn != "qbw.define":
                continue
            if symbols(fields.get("dataset", "")) == (dataset,) and "partition" not in fields:
                found.append(name)
    return found


def _defined_tables(repo_root: Path) -> set[str]:
    """The plant tables the q file defines, read from the tree being written
    into rather than the one this package was imported from."""
    return {m.group(1) for m in _DEFINITION.finditer((repo_root / TABLES_FILE).read_text())}


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

        uqs new-job markout2 --subscribes trades,quote \\
            --publishes my_metric --columns "sym:symbol, value:float"

    Bounded worker, with its source and transform:

        uqs new-job fx_rates --kind backfill --dataset fx_rates \\
            --columns "sym:symbol, mid:float" --width 1D
    """
    subs = [s.strip() for s in (subscribes or "").split(",") if s.strip()]
    repo_root = _paths().repo_root
    try:
        if kind == "streaming":
            plan = jobs.streaming_job(name, subs, publishes, columns)
        elif kind == "backfill":
            if not dataset:
                _die(UqsError("--kind backfill needs --dataset: the table it fills"))
                return
            claimed = _unpartitioned_workers_filling(repo_root, dataset)
            if claimed:
                _die(
                    UqsError(
                        f"dataset {dataset!r} is already filled by {', '.join(claimed)} with no "
                        "partition, and .qbw.define refuses two workers on one dataset and "
                        "partition - pick another --dataset, or give both workers a partition"
                    )
                )
                return
            # An existing source is reused, not rewritten, and an existing
            # table is not defined twice: a second worker over rows someone
            # already declared is the common case after the first.
            plan = jobs.bounded_worker(
                name,
                dataset,
                columns,
                width=width,
                source=source,
                reuse_source=(repo_root / SOURCE_DIR / f"{source or name}.q").is_file(),
                define_table=dataset not in _defined_tables(repo_root),
            )
        else:
            _die(UqsError(f"--kind must be 'streaming' or 'backfill', not {kind!r}"))
            return
    except UqsError as exc:
        _die(exc)
        return

    console.print(plan.render())
    console.print(f"  then regenerate: {', '.join(str(p) for p in _DERIVED)}")
    if dry_run:
        console.print("[dim]--dry-run: nothing written[/]")
        return
    try:
        written = write.apply_plan(plan, repo_root)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"\n[green]scaffolded {len(written)} file(s)[/]")
    # Not fatal: the job's files are already written, and a refusal here (the
    # generator verifies every declared edge first) is information about the
    # tree to act on, not a reason to pretend the scaffold did not happen.
    for script, regen in zip(_DERIVED, _regenerate_derived(repo_root), strict=True):
        if regen.returncode == 0:
            console.print(f"[green]regenerated[/] via {script}")
        else:
            console.print(
                f"[red]could not regenerate[/] - run `python3 {script}` "
                f"and fix what it reports:\n{regen.stdout}{regen.stderr}"
            )
    for note in plan.notes:
        console.print(f"  [yellow]next[/] {note}")


@app.command("new-process")
def new_process(port: PortOpt = DEFAULT_BASE_PORT) -> None:
    """Interactive wizard: add a new uqf stack process. Opens with a menu of
    recipes - "FX quotes feed" and "cross-rate reprice ETL" are fully
    working (answer a few prompts, no q editing needed), "blank
    publisher"/"blank subscriber" write a Stage-1-only skeleton .q file for
    q/kdb+ users to finish by hand (see docs/guides/uqs.md). Registers
    whatever gets built and optionally starts it to verify it's alive.
    """
    try:
        wizard.run(_paths(), base_port=port)
    except UqsError as exc:
        _die(exc)
