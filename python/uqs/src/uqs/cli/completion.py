"""Tab completion for the uqs CLI: the values each argument can take.

Every completer reads the registry the command itself resolves against - the
process table, the profiles, the listable kinds, the plant's tables - so what
TAB offers is what the command would accept. A hand-kept list of process names
here would drift from process.csv the first time a pipeline was added.

A completer never raises. It runs inside the shell on every TAB, where a
traceback lands in the middle of the line being typed, so anything that goes
wrong offers nothing instead.

Typer passes each completer the words already parsed in `ctx.params` and the
word being typed as `incomplete`, and keeps only candidates starting with
`incomplete` itself - so a completer returns everything that fits and leaves
the prefix match to Typer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any

import typer

from uqs import paths as stack_paths
from uqs.model import transports as registered_transports
from uqs.model.profiles import PROFILES
from uqs.stack import backfill, listing
from uqs.stack.site_fields import process_csv_fields

Candidates = list[str] | list[tuple[str, str]]


def _never_raises(fn: Callable[..., Candidates]) -> Callable[..., Candidates]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Candidates:
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - see the module docstring
            return []

    return wrapper


def _comma_list(choices: Iterable[str], incomplete: str) -> list[str]:
    """Complete the LAST element of a comma-separated value.

    `--profile fx,arb` offers `fx,arbitrage`: the whole word, because the
    shell replaces the whole word. An element already in the list is not
    offered again.
    """
    head, _, _ = incomplete.rpartition(",")
    taken = set(head.split(",")) if head else set()
    prefix = f"{head}," if head else ""
    return [prefix + c for c in choices if c not in taken]


def _typed(ctx: typer.Context, name: str) -> Any:
    """What the user already typed for the argument `name`.

    Normally `ctx.params`. But when the line ends in an option still waiting
    for its value (`list processes --sort <TAB>`), Click stops parsing there
    and leaves the positional words unassigned in `ctx.args` - so `kind` reads
    as None although `processes` is right there on the line.
    """
    return ctx.params.get(name) or (tuple(ctx.args) or None)


def _ports(base_port: int | None) -> dict[str, str]:
    return listing.configured_ports(stack_paths.default_paths(), base_port=base_port)


def _plant_tables() -> set[str]:
    # Imported here, not at the top: cli/create imports cli/shared, which
    # imports this module to build ProcsArg.
    from uqs.cli.create_plant import plant_tables

    return plant_tables(stack_paths.default_paths())


@_never_raises
def procnames(ctx: typer.Context) -> list[str]:
    """Every process in the registry, minus the ones already typed.

    `all` is offered only as the first word: `start all rdb1` means nothing
    the plain `start all` does not.
    """
    typed = _typed(ctx, "procs") or ()
    names = [n for n in _ports(ctx.params.get("port")) if n not in typed]
    return names if typed else ["all", *names]


@_never_raises
def procname(ctx: typer.Context) -> list[str]:
    """One process, for commands that take exactly one."""
    return list(_ports(ctx.params.get("port")))


@_never_raises
def process_ports(ctx: typer.Context) -> list[tuple[str, str]]:
    """A process's port, with the process named beside it - the number is
    what the option takes, the name is what the reader was looking for."""
    base = ctx.params.get("base_port")
    return [(port, name) for name, port in _ports(base).items() if port]


@_never_raises
def profiles(incomplete: str) -> list[str]:
    return _comma_list(PROFILES, incomplete)


@_never_raises
def transports() -> list[str]:
    """Every transport .qetl.source registers, from the contract surface."""
    return list(registered_transports.names())


@_never_raises
def backfill_workers() -> list[str]:
    """Every worker a backfill process runs, from the registry."""
    return sorted(backfill.backfill_workers())


def columns_of(kind: str) -> Callable[..., Candidates]:
    """`--sort`'s completer for a list of one fixed `kind` (`uqs target list`)."""

    @_never_raises
    def columns() -> list[str]:
        rows = listing.list_items(stack_paths.default_paths(), kind)
        return list(rows[0]) if rows else []

    return columns


@_never_raises
def csv_fields() -> list[str]:
    return list(process_csv_fields())


@_never_raises
def summary_sort_column() -> list[str]:
    return list(listing.SUMMARY_ALL_COLUMNS)


@_never_raises
def summary_columns(incomplete: str) -> list[str]:
    if "," not in incomplete:
        from uqs.cli.summary_columns import SUMMARY_PRESETS

        return [*SUMMARY_PRESETS, *listing.SUMMARY_ALL_COLUMNS]
    return _comma_list(listing.SUMMARY_ALL_COLUMNS, incomplete)


@_never_raises
def plant_table() -> list[str]:
    return sorted(_plant_tables())


@_never_raises
def plant_tables(incomplete: str) -> list[str]:
    return _comma_list(sorted(_plant_tables()), incomplete)


@_never_raises
def attachments() -> list[str]:
    """Every saved attachment - what `--attach` and `job attachment ...` resolve."""
    from uqs.attach import config

    return config.names()


@_never_raises
def targets() -> list[str]:
    """Every declared deployment target - deploy_targets.toml, as `uqs target list`
    reads it."""
    from uqs.deploy import targets as deploy_targets

    return sorted(deploy_targets.read(stack_paths.repo_root()))


@_never_raises
def jobs() -> list[str]:
    """Every declared job: streaming jobs and bounded workers alike."""
    from uqs.model.jobs import job_rows

    return [row["job"] for row in job_rows(stack_paths.repo_root())]


@_never_raises
def streaming_jobs() -> list[str]:
    """Every streaming job - what `job gaps`, `job preview` and `--twin-of` take."""
    from uqs.model.jobs import job_rows

    return [
        r["job"]
        for r in job_rows(stack_paths.repo_root())
        if r["kind"] not in ("backfill", "reaction")
    ]


@_never_raises
def sources() -> list[str]:
    """Every source q registers (.qetl.source), from q_facts."""
    from uqs.generated import q_facts

    return sorted(q_facts.SOURCE_TRANSPORTS)


@_never_raises
def source_list(incomplete: str) -> list[str]:
    """The sources, completing the last of a comma-separated list."""
    from uqs.generated import q_facts

    return _comma_list(sorted(q_facts.SOURCE_TRANSPORTS), incomplete)


@_never_raises
def bounded_datasets() -> list[str]:
    """Every dataset a bounded worker fills - q's own list (q_facts)."""
    from uqs.generated import q_facts

    return sorted(q_facts.BOUNDED_DATASET_TIME_COLUMNS)


@_never_raises
def markout_datasets() -> list[str]:
    """Every markout dataset on the plant: the plant tables that carry `config_id`, the
    column that names a row's configuration snapshot (#1999)."""
    from uqs.model import plant_metadata

    return sorted(
        name
        for name, table in plant_metadata.load().tables.items()
        if "config_id" in [c.name for c in table.columns]
    )


@_never_raises
def snapshot_ids() -> list[str]:
    """Every configuration snapshot recorded in this runtime's store."""
    from uqs.stack import snapshots

    return snapshots.ids(stack_paths.default_paths())


@_never_raises
def overrides(ctx: typer.Context) -> list[tuple[str, str]]:
    """The overrides the command being typed takes - `uqs override list`'s
    catalogue (cli/safety.py) - each with what it bypasses. One waived per
    component is offered as `name:`, for the component to follow."""
    from uqs.cli import safety

    command = " ".join(ctx.command_path.split()[1:])
    return [
        (g.name.removesuffix("COMPONENT"), g.description)
        for g in safety.catalog()
        if command in g.commands
    ]


def choices(*values: str) -> Callable[[], list[str]]:
    """A completer for an option whose values are a fixed set. The set is kept
    on it as `choice_values`, which test_cli_conventions.py reads to hold the
    option's help to it."""

    def complete() -> list[str]:
        return list(values)

    complete.choice_values = values  # ty: ignore[unresolved-attribute]
    return complete
