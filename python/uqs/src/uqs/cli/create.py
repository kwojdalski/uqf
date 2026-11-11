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

from uqs.cli import completion, safety
from uqs.cli.choices import ChoiceOpt
from uqs.cli.create_backfill import backfill_plan
from uqs.cli.create_bundle import planning_root, refuse_tree_only, write_bundle_plan
from uqs.cli.create_kinds import DEFAULT_KIND, KindOpt, for_kinds, given_options, misplaced
from uqs.cli.create_plant import plant_shapes
from uqs.cli.create_reaction import TriggeredByOpt, scaffold_reaction
from uqs.cli.regenerate import write_plan
from uqs.cli.renames import SLICE, SLICE_COLUMN, refused, renamed
from uqs.cli.shared import (
    JOB_AUTHORING_PANEL,
    _die,
    _paths,
    app,
    job_app,
)
from uqs.cli.vocabulary import q_timespan
from uqs.model.kinds import SCAFFOLD_KINDS
from uqs.paths import UqsError, UqsUsageError
from uqs.scaffold import columns as columns_mod
from uqs.scaffold import cycles, external, jobs, normalizer
from uqs.scaffold import horizon as horizon_scaffold
from uqs.scaffold import transform as transform_mod
from uqs.scaffold import worker as worker_mod

app.add_typer(job_app, name="job")


#: --procname: a process is PROC, or `--proc` as an option, on every command (#1857)
_RENAMES = {"--procname": "--proc"}


_TransformOpt = ChoiceOpt(
    transform_mod.MODES,
    "--transform",
    "{values}: the transform, and for streaming its handler" + for_kinds("--transform"),
)


@job_app.command(
    "new",
    rich_help_panel=JOB_AUTHORING_PANEL,
    cls=renamed(_RENAMES, refused({**SLICE, **SLICE_COLUMN}, safety.ChangesState)),
)
def new_job(
    ctx: typer.Context,
    name: Annotated[
        str, typer.Argument(metavar="JOB", help="Job name: a q namespace and a filename")
    ],
    kind: KindOpt = DEFAULT_KIND,
    subscribe_to: Annotated[
        str | None,
        typer.Option(
            "--subscribe-to",
            metavar="TABLE,...",
            help=f"Comma-separated tables it reads. Omit for a feed{for_kinds('--subscribe-to')}",
            autocompletion=completion.plant_tables,
        ),
    ] = None,
    outputs: Annotated[
        str | None,
        typer.Option(
            "--outputs",
            metavar="TABLE,...",
            help="Comma-separated tables it produces: a streaming job's are published "
            "(an existing one is published onto), a backfill's is the one it fills, a "
            "reaction's are the ones it writes" + for_kinds("--outputs"),
            autocompletion=completion.plant_tables,
        ),
    ] = None,
    raw_table: Annotated[
        str | None,
        typer.Option(
            "--raw-table",
            help="`connector`: the table the Python publisher writes. backfill: the physical "
            "table a new source's adapter reads, with --raw-columns" + for_kinds("--raw-table"),
        ),
    ] = None,
    raw_columns: Annotated[
        str | None,
        typer.Option(
            "--raw-columns",
            help="The columns the adapter reads from --raw-table, as the source names them: "
            "'CREATED_AT:timestamp, PAYLOAD:any'" + for_kinds("--raw-columns"),
        ),
    ] = None,
    columns: Annotated[
        str | None,
        typer.Option("--schema", help="Its table's schema: 'sym:symbol, venue:g#symbol'"),
    ] = None,
    columns_from: Annotated[
        str | None,
        typer.Option(
            "--schema-from",
            help="Copy an existing plant table's schema instead of --schema",
            autocompletion=completion.plant_tables,
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source", metavar="SOURCE", help=f"Source name; default JOB{for_kinds('--source')}"
        ),
    ] = None,
    width: Annotated[
        str | None,
        q_timespan(
            "--width",
            help="Window width, a DURATION or a q timespan; default "
            f"{worker_mod.DEFAULT_WIDTH}" + for_kinds("--width"),
        ),
    ] = None,
    procname: Annotated[
        str | None,
        typer.Option("--proc", help="The process that runs it (default: JOB1, or JOB_backfill1)"),
    ] = None,
    start_with_all: Annotated[
        bool,
        typer.Option(
            "--start-with-all/--no-start-with-all",
            help="Start it with `uqs start all` (default: on demand)"
            + for_kinds("--start-with-all"),
        ),
    ] = False,
    transport: Annotated[
        str | None,
        typer.Option(
            "--transport",
            help="How a new backfill source is reached - one of the transports "
            ".qetl.source registers (docs/reference/surfaces/current/transports.csv); "
            "default: the default transport there, a q process" + for_kinds("--transport"),
            autocompletion=completion.transports,
        ),
    ] = None,
    period: Annotated[
        str | None,
        q_timespan(
            "--period",
            help="Timer period, a DURATION or a q timespan: a feed's tick, or an etl's added "
            "on_timer" + for_kinds("--period"),
        ),
    ] = None,
    horizon: Annotated[
        str | None,
        q_timespan(
            "--horizon",
            help="How long an event waits before it is evaluated, a DURATION or a q timespan"
            + for_kinds("--horizon"),
        ),
    ] = None,
    poll: Annotated[
        bool,
        typer.Option(
            "--poll", help="A feed as previewable fetch/normalize steps" + for_kinds("--poll")
        ),
    ] = False,
    cursor_fields: Annotated[
        str | None,
        typer.Option(
            "--cursor-fields",
            help="With --poll: a compound cursor's fields, e.g. ts,id"
            + for_kinds("--cursor-fields"),
        ),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="Start profile it joins" + for_kinds("--profile"),
            autocompletion=completion.profiles,
        ),
    ] = None,
    unprofiled: Annotated[
        str | None,
        typer.Option(
            "--unprofiled",
            help="Or the reason it belongs to no profile" + for_kinds("--unprofiled"),
        ),
    ] = None,
    partition: Annotated[
        str | None,
        typer.Option(
            "--slice",
            help="The slice of --outputs it fills: a coverage key such as a symbol, not a "
            "date" + for_kinds("--slice"),
        ),
    ] = None,
    partition_column: Annotated[
        str | None,
        typer.Option(
            "--slice-column",
            help="The column holding --slice; default "
            f"{worker_mod.DEFAULT_PARTITION_COLUMN}{for_kinds('--slice-column')}",
        ),
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help=f"Scaffold a quality check{for_kinds('--check')}")
    ] = False,
    twin_of: Annotated[
        str | None,
        typer.Option(
            "--twin-of",
            help="A streaming job whose published table this backfill refills - the dataset "
            "and columns are taken from it, and `uqs job gaps JOB` then names this worker "
            "JOB_backfill: pass JOB without the suffix, e.g. `uqs job new JOB_hist "
            "--kind backfill --twin-of JOB`" + for_kinds("--twin-of"),
        ),
    ] = None,
    transform: _TransformOpt = None,
    triggered_by: TriggeredByOpt = None,
    bundle: Annotated[
        Path | None,
        typer.Option(
            "--bundle",
            help="Scaffold into this sidecar bundle folder instead of the tree "
            "(made a bundle if it is not one yet)",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Print what would be written, write nothing")
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
            --outputs my_metric --schema "sym:symbol, px:float"

    Bounded worker, with its source and transform - JOB is the base, so this writes worker
    fx_rates_backfill over source fx_rates (a JOB already ending in _backfill is refused):

        uqs job new fx_rates --kind backfill --outputs fx_rates \\
            --schema "sym:symbol, mid:float" --width 1D

    One table in, one out, with the handler written - passthrough or derive:

        uqs job new trades_copy --subscribe-to trades --outputs trades_copy_out \\
            --schema-from trades --transform passthrough

    Polling feed, as steps `uqs job preview` can run without publishing:

        uqs job new rates_feed --outputs rates --schema "sym:symbol, mid:float" --poll

    Reaction, run each time a bounded worker publishes a window of demo_deals:

        uqs job new deal_volume --triggered-by demo_deals --outputs deal_volume
    """
    subs = [s.strip() for s in (subscribe_to or "").split(",") if s.strip()]
    repo_root = _paths().repo_root
    used = given_options(ctx)
    if triggered_by is not None:
        scaffold_reaction(
            name,
            triggered_by,
            outputs,
            {o: v for o, v in used.items() if o != "--outputs"},
            bundle=bundle,
            dry_run=dry_run,
        )
        return
    # Options that shape one kind only are refused on the others, not ignored.
    if refusal := misplaced(kind, used):
        _die(UqsError(refusal))
    # One flag, what the job type does with it differing: a streaming job publishes
    # them, a backfill fills the one (#2102).
    publishes = outputs
    dataset = outputs
    if kind == "backfill" and outputs and "," in outputs:
        _die(UqsUsageError("--kind backfill fills exactly one dataset: give --outputs one table"))

    def _plan(root: Path):
        """The plan, read against `root`: the tree, or the tree with --bundle installed."""
        shapes = plant_shapes(_paths(), root)
        definitions = {name: t.definition() for name, t in shapes.items()}
        known = set(shapes)
        shape = columns_mod.resolve_shape(columns, columns_from, shapes)
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
                partition_column=partition_column,
                check=check,
                transform=transform,
                start_with_all=start_with_all,
                twin_of=twin_of,
                definitions=definitions,
                raw_table=raw_table,
                raw_columns=raw_columns,
                known_tables=known,
            )
        if kind == "normalizer":
            if publishes:
                raise UqsError("a normalizer publishes its own NAME - drop --outputs")
            if not shape:
                raise UqsUsageError("--kind normalizer needs --schema: its canonical table")
            return normalizer.normalizer(
                name,
                subs,
                shape,
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
        if kind == "connector":
            if not (raw_table and publishes and shape):
                raise UqsUsageError("--kind connector needs --raw-table, --outputs and --schema")
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
        if kind == "horizon":
            inputs = {
                s: columns_mod.definition_columns(definitions[s]) for s in subs if s in definitions
            }
            return horizon_scaffold.horizon_job(
                name, subs, shape or None, inputs, horizon, known_tables=known, publishes=publishes,
                procname=procname, start_with_all=start_with_all, profile=profile,
                unprofiled=unprofiled,
            )  # fmt: skip
        raise UqsUsageError(f"--kind must be one of {', '.join(SCAFFOLD_KINDS)}: {kind!r}")

    try:
        if bundle is not None:
            refuse_tree_only(
                {"--profile": profile is not None, "--unprofiled": unprofiled is not None}
            )
        with planning_root(repo_root, bundle) as root:
            plan = _plan(root)
            cycles.refuse_cycles(root, plan)
    except UqsError as exc:
        _die(exc)
    if bundle is not None:
        write_bundle_plan(plan, bundle, dry_run=dry_run)
        return
    write_plan(plan, repo_root, dry_run=dry_run)
