"""The command that WRITES code: `uqs job new`, which scaffolds an ETL job into
the tree. Its own module because it creates files rather than acting on a
running fleet. See cli/lifecycle.py for why the split is shaped this way.

There is one way to add a process: declare a job in q. The `new-process`
console wizard, which wrote TorQ scripts registered through a separate
extra_processes.csv with its own port allocation, was a second one and is
gone.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.create_backfill import backfill_plan, defined_tables
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
    return defined_tables(paths.repo_root) | theirs


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
        typer.Option("--raw-table", help="The table the Python publisher writes (external)"),
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
    if triggered_by is not None or writes is not None:
        scaffold_reaction(
            name,
            triggered_by,
            writes,
            {
                "--kind": kind != "streaming",
                "--subscribe-to": subscribe_to is not None,
                "--publishes": publishes is not None,
                "--dataset": dataset is not None,
                "--raw-table": raw_table is not None,
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
            },
            dry_run=dry_run,
        )
        return
    # Options that shape one kind only are refused on the others, not ignored.
    only = {
        "backfill": {
            "--transport": transport is not None,
            "--partition": partition is not None,
            "--check": check,
            "--dataset": dataset is not None,
            "--source": source is not None,
            "--width": width is not None,
        },
        "streaming": {
            "--period": period is not None,
            "--poll": poll,
            "--cursor-fields": cursor_fields is not None,
        },
        "external": {"--raw-table": raw_table is not None},
        "standing": {"--profile": profile is not None, "--unprofiled": unprofiled is not None},
    }
    if transform is not None and kind not in ("backfill", "streaming"):
        _die(UqsError(f"--transform does not apply to --kind {kind}"))
        return
    for owner, given in only.items():
        fits = kind in (
            ("streaming", "normalizer", "external") if owner == "standing" else (owner,)
        )
        for option in (o for o, used in given.items() if used and not fits):
            _die(UqsError(f"{option} does not apply to --kind {kind}"))
            return
    try:
        shape = columns_mod.resolve_shape(columns, columns_from, _plant_definitions(_paths()))
        if kind == "streaming":
            plan = jobs.streaming_job(
                name,
                subs,
                publishes,
                shape,
                procname,
                known_tables=_plant_tables(_paths()),
                start_with_all=start_with_all,
                period=period,
                profile=profile,
                unprofiled=unprofiled,
                poll=poll,
                cursor=cursor_fields,
                transform=transform,
                definitions=_plant_definitions(_paths()) if transform else None,
            )
        elif kind == "backfill":
            plan = backfill_plan(
                repo_root,
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
            )
        elif kind == "normalizer":
            if publishes:
                _die(UqsError("a normalizer publishes its own NAME - drop --publishes"))
                return
            if not shape:
                _die(UqsError("--kind normalizer needs --columns: its canonical table"))
                return
            definitions = _plant_definitions(_paths())
            plan = normalizer.normalizer(
                name,
                subs,
                columns_mod.as_columns(shape),
                {
                    s: columns_mod.definition_columns(definitions[s])
                    for s in subs
                    if s in definitions
                },
                known_tables=_plant_tables(_paths()),
                procname=procname,
                start_with_all=start_with_all,
                profile=profile,
                unprofiled=unprofiled,
            )
        elif kind == "external":
            if not (raw_table and publishes and shape):
                _die(UqsError("--kind external needs --raw-table, --publishes and --columns"))
                return
            plan = external.external_feed(
                name,
                raw_table,
                publishes,
                shape,
                known_tables=_plant_tables(_paths()),
                procname=procname,
                start_with_all=start_with_all,
                profile=profile,
                unprofiled=unprofiled,
            )
        else:
            _die(
                UqsError(
                    "--kind must be 'backfill', 'normalizer', 'streaming' or 'external', "
                    f"not {kind!r}"
                )
            )
            return
    except UqsError as exc:
        _die(exc)
        return
    write_plan(plan, repo_root, dry_run=dry_run)
