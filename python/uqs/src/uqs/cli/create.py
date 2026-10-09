"""The command that WRITES code: `uqs job new`, which scaffolds an ETL job into
the tree. Its own module because it creates files rather than acting on a
running fleet. See cli/lifecycle.py for why the split is shaped this way.

There is one way to add a process: declare a job in q. The `new-process`
console wizard, which wrote TorQ scripts registered through a separate
extra_processes.csv with its own port allocation, was a second one and is
gone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.create_backfill import backfill_plan, defined_tables
from uqs.cli.create_bundle import planning_root, refuse_tree_only, write_bundle_plan
from uqs.cli.create_kinds import misplaced
from uqs.cli.create_reaction import scaffold_reaction
from uqs.cli.regenerate import write_plan
from uqs.cli.shared import (
    _die,
    _paths,
    app,
    job_app,
)
from uqs.model.schemas import _DEFINITION
from uqs.paths import (
    TABLES_FILE,
    UqsError,
    UqsPaths,
)
from uqs.scaffold import columns as columns_mod
from uqs.scaffold import external, jobs, normalizer


def _plant_definitions(paths: UqsPaths, root: Path | None = None) -> dict[str, str]:
    """{table: its one-line `name:([]...)` definition}, the tree at `root`'s (default:
    the tree's) and the vendored starter pack's - what a normalizer's sources are read from."""
    out: dict[str, str] = {}
    for path in (paths.torqapphome / "database.q", (root or paths.repo_root) / TABLES_FILE):
        if path.is_file():
            out.update((m.group(1), m.group(0)) for m in _DEFINITION.finditer(path.read_text()))
    return out


def _plant_tables(paths: UqsPaths, root: Path | None = None) -> set[str]:
    """Every table the plant carries: the tree at `root`'s (default: the tree's), plus
    the vendored starter pack's (`quote`, `trade`), which the generated database.q merges in."""
    vendored = paths.torqapphome / "database.q"
    theirs = (
        {m.group(1) for m in _DEFINITION.finditer(vendored.read_text())}
        if vendored.is_file()
        else set()
    )
    return defined_tables(root or paths.repo_root) | theirs


app.add_typer(job_app, name="job")


@job_app.command("new")
def new_job(
    name: Annotated[str, typer.Argument(help="Job name: a q namespace and a filename")],
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="'streaming' (default), 'backfill', 'normalizer' or 'external' "
            "(a Python publisher outside q, plus the q job reshaping what it publishes)",
            autocompletion=completion.choices("streaming", "backfill", "normalizer", "external"),
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
    raw_table: Annotated[
        str | None,
        typer.Option(
            "--raw-table",
            help="external: the table the Python publisher writes. backfill: the physical "
            "table a new source's adapter reads, with --raw-columns",
        ),
    ] = None,
    raw_columns: Annotated[
        str | None,
        typer.Option(
            "--raw-columns",
            help="The columns the adapter reads from --raw-table, as the source names them: "
            "'CREATED_AT:timestamp, PAYLOAD:any' (backfill)",
        ),
    ] = None,
    columns: Annotated[
        str | None,
        typer.Option("--columns", help="Its table's columns: 'sym:symbol, venue:g#symbol'"),
    ] = None,
    columns_from: Annotated[
        str | None,
        typer.Option(
            "--columns-from",
            help="Copy an existing plant table's columns instead of --columns",
            autocompletion=completion.plant_tables,
        ),
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Source name (backfill; defaults to NAME)")
    ] = None,
    width: Annotated[
        str | None,
        typer.Option("--width", help="Backfill window width, as a q timespan (default 1D)"),
    ] = None,
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
        str | None,
        typer.Option(
            "--transport",
            help="How a new backfill source is reached - one of the transports "
            ".qetl.source registers (docs/reference/surfaces/current/transports.csv); "
            "default: the default transport there, a q process",
            autocompletion=completion.transports,
        ),
    ] = None,
    period: Annotated[
        str | None,
        typer.Option("--period", help="Timer period: a feed's tick, or an etl's added on_timer"),
    ] = None,
    poll: Annotated[
        bool,
        typer.Option("--poll", help="A feed as previewable fetch/normalize steps"),
    ] = False,
    cursor_fields: Annotated[
        str | None,
        typer.Option("--cursor-fields", help="With --poll: a compound cursor's fields, e.g. ts,id"),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="Start profile it joins (streaming, normalizer)",
            autocompletion=completion.profiles,
        ),
    ] = None,
    unprofiled: Annotated[
        str | None,
        typer.Option("--unprofiled", help="Or the reason it belongs to no profile"),
    ] = None,
    partition: Annotated[
        str | None,
        typer.Option("--partition", help="The slice of --dataset it fills (backfill)"),
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Scaffold a quality check (backfill)")
    ] = False,
    twin_of: Annotated[
        str | None,
        typer.Option(
            "--twin-of",
            help="A streaming job whose published table this backfill refills - the dataset "
            "and columns are taken from it, and `uqs gaps JOB` then names this worker (backfill)",
        ),
    ] = None,
    transform: Annotated[
        str | None,
        typer.Option(
            "--transform",
            help="passthrough or derive: the transform, and for streaming its handler "
            "(backfill, streaming)",
        ),
    ] = None,
    triggered_by: Annotated[
        str | None,
        typer.Option(
            "--triggered-by",
            help="Scaffold a reaction instead: run when a bounded worker publishes DATASET",
        ),
    ] = None,
    writes: Annotated[
        str | None,
        typer.Option(
            "--writes", help="Comma-separated tables the reaction writes (with --triggered-by)"
        ),
    ] = None,
    bundle: Annotated[
        Path | None,
        typer.Option(
            "--bundle",
            help="Scaffold into this sidecar bundle folder instead of the tree "
            "(made a bundle if it is not one yet)",
        ),
    ] = None,
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

        uqs job new markout2 --subscribe-to trades,quote \\
            --publishes my_metric --columns "sym:symbol, value:float"

    Bounded worker, with its source and transform:

        uqs job new fx_rates --kind backfill --dataset fx_rates \\
            --columns "sym:symbol, mid:float" --width 1D

    One table in, one out, with the handler written - passthrough or derive:

        uqs job new trades_copy --subscribe-to trades --publishes trades_copy_out \\
            --columns-from trades --transform passthrough

    Polling feed, as steps `uqs stream preview` can run without publishing:

        uqs job new rates_feed --publishes rates --columns "sym:symbol, mid:float" --poll

    Reaction, run each time a bounded worker publishes a window of demo_deals:

        uqs job new rebuild_positions --triggered-by demo_deals --writes positions
    """
    subs = [s.strip() for s in (subscribe_to or "").split(",") if s.strip()]
    repo_root = _paths().repo_root
    used = {
        "--kind": kind != "streaming",
        "--subscribe-to": subscribe_to is not None,
        "--publishes": publishes is not None,
        "--dataset": dataset is not None,
        "--raw-table": raw_table is not None,
        "--raw-columns": raw_columns is not None,
        "--columns": columns is not None,
        "--source": source is not None,
        "--width": width is not None,
        "--procname": procname is not None,
        "--start-with-all": start_with_all,
        "--transport": transport is not None,
        "--period": period is not None,
        "--poll": poll,
        "--cursor-fields": cursor_fields is not None,
        "--profile": profile is not None,
        "--unprofiled": unprofiled is not None,
        "--partition": partition is not None,
        "--check": check,
        "--transform": transform is not None,
        "--twin-of": twin_of is not None,
    }
    if triggered_by is not None or writes is not None:
        scaffold_reaction(
            name,
            triggered_by,
            writes,
            used,
            bundle=bundle,
            dry_run=dry_run,
        )
        return
    # Options that shape one kind only are refused on the others, not ignored.
    if refusal := misplaced(kind, used):
        _die(UqsError(refusal))
        return

    def _plan(root: Path):
        """The plan, read against `root`: the tree, or the tree with --bundle installed."""
        definitions = _plant_definitions(_paths(), root)
        known = _plant_tables(_paths(), root)
        shape = columns_mod.resolve_shape(columns, columns_from, definitions)
        if kind == "streaming":
            return jobs.streaming_job(
                name,
                subs,
                publishes,
                shape,
                procname,
                known_tables=known,
                start_with_all=start_with_all,
                period=period,
                profile=profile,
                unprofiled=unprofiled,
                poll=poll,
                cursor=cursor_fields,
                transform=transform,
                definitions=definitions if transform else None,
            )
        if kind == "backfill":
            return backfill_plan(
                root,
                name,
                dataset,
                shape,
                width=width,
                source=source,
                procname=procname,
                transport=transport,
                partition=partition,
                check=check,
                transform=transform,
                start_with_all=start_with_all,
                twin_of=twin_of,
                definitions=definitions if twin_of else None,
                raw_table=raw_table,
                raw_columns=raw_columns,
            )
        if kind == "normalizer":
            if publishes:
                raise UqsError("a normalizer publishes its own NAME - drop --publishes")
            if not shape:
                raise UqsError("--kind normalizer needs --columns: its canonical table")
            return normalizer.normalizer(
                name,
                subs,
                columns_mod.as_columns(shape),
                {
                    s: columns_mod.definition_columns(definitions[s])
                    for s in subs
                    if s in definitions
                },
                known_tables=known,
                procname=procname,
                start_with_all=start_with_all,
                profile=profile,
                unprofiled=unprofiled,
            )
        if kind == "external":
            if not (raw_table and publishes and shape):
                raise UqsError("--kind external needs --raw-table, --publishes and --columns")
            return external.external_feed(
                name,
                raw_table,
                publishes,
                shape,
                known_tables=known,
                procname=procname,
                start_with_all=start_with_all,
                profile=profile,
                unprofiled=unprofiled,
            )
        raise UqsError(
            f"--kind must be 'backfill', 'normalizer', 'streaming' or 'external', not {kind!r}"
        )

    try:
        if bundle is not None:
            refuse_tree_only(
                {"--profile": profile is not None, "--unprofiled": unprofiled is not None}
            )
        with planning_root(repo_root, bundle) as root:
            plan = _plan(root)
    except UqsError as exc:
        _die(exc)
        return
    if bundle is not None:
        write_bundle_plan(plan, bundle, dry_run=dry_run)
        return
    write_plan(plan, repo_root, dry_run=dry_run)
