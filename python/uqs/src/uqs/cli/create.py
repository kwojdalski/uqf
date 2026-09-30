"""The command that WRITES code: `new-job`, which scaffolds an ETL job into
the tree. Its own module because it creates files rather than acting on a
running fleet. See cli/lifecycle.py for why the split is shaped this way.

There is one way to add a process: declare a job in q. The `new-process`
console wizard, which wrote TorQ scripts registered through a separate
extra_processes.csv with its own port allocation, was a second one and is
gone.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.shared import (
    _die,
    _paths,
    app,
    console,
)
from uqs.model.declarations import declaration_calls, symbols
from uqs.model.schemas import _DEFINITION
from uqs.paths import (
    CONTRACT_SURFACE_SCRIPT,
    MAN_REGISTRY_SCRIPT,
    OPERATIONAL_DOCS_SCRIPT,
    SOURCE_DIR,
    TABLES_FILE,
    WORKER_DIR,
    UqsError,
    UqsPaths,
    q_interpreter,
)
from uqs.scaffold import jobs, normalizer, worker, write

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


def _export_contract_surface(repo_root: Path) -> subprocess.CompletedProcess[str] | None:
    """Re-export the contract surface, or None when no q is installed.

    Separate from `_regenerate_derived` because it needs q: it loads the tree
    to read what is defined. Every scaffold adds `.qpipe.job.<name>` names, so
    without this the contract-surface hook fails on the next commit, for a
    change nobody made by hand.
    """
    if q_interpreter() is None:
        return None
    return subprocess.run(
        [sys.executable, str(repo_root / CONTRACT_SURFACE_SCRIPT), "export"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _unpartitioned_workers_filling(repo_root: Path, dataset: str) -> list[str]:
    """Workers that already fill `dataset` without declaring a partition.

    `.qetl.job.bounded.define` refuses two workers on one dataset AND partition (#60,
    #185), and a scaffolded worker declares none - so a second one on such a
    dataset is a tree that no longer LOADS. Caught here, before anything is
    written, rather than as a bare error from inside a declaration.
    """
    found = []
    for path in sorted((repo_root / WORKER_DIR).glob("*.q")):
        for fn, name, fields in declaration_calls(path.read_text()):
            if fn != "qetl.job.bounded.define":
                continue
            if symbols(fields.get("dataset", "")) == (dataset,) and "partition" not in fields:
                found.append(name)
    return found


def _defined_tables(repo_root: Path) -> set[str]:
    """The plant tables the q file defines, read from the tree being written
    into rather than the one this package was imported from."""
    return {m.group(1) for m in _DEFINITION.finditer((repo_root / TABLES_FILE).read_text())}


def _plant_definitions(paths: UqsPaths) -> dict[str, str]:
    """{table: its one-line `name:([]...)` definition}, this tree's and the
    vendored starter pack's - what a normalizer's source schemas are read from."""
    out: dict[str, str] = {}
    for path in (paths.torqapphome / "database.q", paths.repo_root / TABLES_FILE):
        if path.is_file():
            out.update((m.group(1), m.group(0)) for m in _DEFINITION.finditer(path.read_text()))
    return out


def _plant_tables(paths: UqsPaths) -> set[str]:
    """Every table the plant carries: this tree's, plus the vendored starter
    pack's (`quote`, `trade`), which the generated database.q merges in."""
    vendored = paths.torqapphome / "database.q"
    theirs = (
        {m.group(1) for m in _DEFINITION.finditer(vendored.read_text())}
        if vendored.is_file()
        else set()
    )
    return _defined_tables(paths.repo_root) | theirs


@app.command("new-job")
def new_job(
    name: Annotated[str, typer.Argument(help="Job name: a q namespace and a filename")],
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="'streaming' (default), 'backfill' or 'normalizer'",
            autocompletion=completion.choices("streaming", "backfill", "normalizer"),
        ),
    ] = "streaming",
    subscribe_to: Annotated[
        str | None,
        typer.Option(
            "--subscribe-to",
            help="Comma-separated tables it reads. Omit for a feed.",
            autocompletion=completion.plant_tables,
        ),
    ] = None,
    publishes: Annotated[
        str | None,
        typer.Option(
            "--publishes",
            help="Comma-separated tables it writes (streaming); an existing one is published onto",
            autocompletion=completion.plant_tables,
        ),
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
    procname: Annotated[
        str | None,
        typer.Option(
            "--procname", help="The process that runs it (default: NAME1, or NAME_backfill1)"
        ),
    ] = None,
    start_with_all: Annotated[
        bool,
        typer.Option(
            "--start-with-all",
            help="Start it with `uqs start all` (streaming, normalizer; default: on demand)",
        ),
    ] = False,
    transport: Annotated[
        str,
        typer.Option(
            "--transport",
            help="How a new backfill source is reached: 'ipc' (a q process) or 'odbc'",
            autocompletion=completion.choices("ipc", "odbc"),
        ),
    ] = "ipc",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be written, write nothing")
    ] = False,
) -> None:
    """Scaffold a new ETL job: its q files, its table, and the lists that register them.

    Writes the SHAPE, never the logic. The generated handler throws and the
    generated test fails, on purpose - a scaffold that left something green
    behind would make "generated" and "implemented" look the same from
    outside, which is the state that produces a process reporting `up` while
    publishing nothing.

    Streaming, reading two tables and writing one:

        uqs new-job markout2 --subscribe-to trades,quote \\
            --publishes my_metric --columns "sym:symbol, value:float"

    Bounded worker, with its source and transform:

        uqs new-job fx_rates --kind backfill --dataset fx_rates \\
            --columns "sym:symbol, mid:float" --width 1D
    """
    subs = [s.strip() for s in (subscribe_to or "").split(",") if s.strip()]
    repo_root = _paths().repo_root
    if transport != "ipc" and kind != "backfill":
        # A streaming job reads the plant; only a backfill's source has a transport.
        _die(UqsError("--transport is for a backfill's source - drop it for --kind " + kind))
        return
    try:
        if kind == "streaming":
            plan = jobs.streaming_job(
                name,
                subs,
                publishes,
                columns,
                procname,
                known_tables=_plant_tables(_paths()),
                start_with_all=start_with_all,
            )
        elif kind == "backfill":
            if start_with_all:
                # A backfill runs a window and exits; `uqs start all` starts
                # standing processes, and .qetl.job.bounded.define has no such key.
                _die(UqsError("--start-with-all is for standing jobs, not a backfill - drop it"))
                return
            if not dataset:
                _die(UqsError("--kind backfill needs --dataset: the table it fills"))
                return
            claimed = _unpartitioned_workers_filling(repo_root, dataset)
            if claimed:
                _die(
                    UqsError(
                        f"dataset {dataset!r} is already filled by {', '.join(claimed)} with no "
                        "partition, and .qetl.job.bounded.define refuses two workers "
                        "on one dataset and "
                        "partition - pick another --dataset, or give both workers a partition"
                    )
                )
                return
            # An existing source is reused, not rewritten, and an existing
            # table is not defined twice: a second worker over rows someone
            # already declared is the common case after the first.
            plan = worker.bounded_worker(
                name,
                dataset,
                columns,
                width=width,
                source=source,
                procname=procname,
                transport=transport,
                reuse_source=(repo_root / SOURCE_DIR / f"{source or name}.q").is_file(),
                define_table=dataset not in _defined_tables(repo_root),
            )
        elif kind == "normalizer":
            if publishes:
                _die(UqsError("a normalizer publishes its own NAME - drop --publishes"))
                return
            if not columns:
                _die(UqsError("--kind normalizer needs --columns: its canonical table"))
                return
            definitions = _plant_definitions(_paths())
            plan = normalizer.normalizer(
                name,
                subs,
                jobs.parse_columns(columns),
                {
                    s: normalizer.definition_columns(definitions[s])
                    for s in subs
                    if s in definitions
                },
                known_tables=_plant_tables(_paths()),
                procname=procname,
                start_with_all=start_with_all,
            )
        else:
            _die(UqsError(f"--kind must be 'streaming', 'backfill' or 'normalizer', not {kind!r}"))
            return
    except UqsError as exc:
        _die(exc)
        return

    console.print(plan.render())
    console.print(
        f"  then regenerate: {', '.join(str(p) for p in (*_DERIVED, CONTRACT_SURFACE_SCRIPT))}"
    )
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
    surface = _export_contract_surface(repo_root)
    if surface is None:
        console.print(
            f"[yellow]no q on PATH[/] - run `uv run python {CONTRACT_SURFACE_SCRIPT} export` where "
            "q is installed, or the contract-surface hook fails on the next commit"
        )
    elif surface.returncode == 0:
        console.print(f"[green]regenerated[/] via {CONTRACT_SURFACE_SCRIPT}")
    else:
        console.print(
            f"[red]could not export the contract surface[/] - the new files may not load in q:\n"
            f"{surface.stdout}{surface.stderr}"
        )
    for note in plan.notes:
        console.print(f"  [yellow]next[/] {note}")
