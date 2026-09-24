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
from uqs.model.pipelines import PROCESS_CSV_FIELDS
from uqs.model.profiles import PROFILES
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.stack import backfill, listing

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


def _ports(base_port: int) -> dict[str, str]:
    return listing.configured_ports(stack_paths.default_paths(), base_port=base_port)


def _plant_tables() -> set[str]:
    # Imported here, not at the top: cli/create imports cli/shared, which
    # imports this module to build ProcsArg.
    from uqs.cli.create import _plant_tables

    return _plant_tables(stack_paths.default_paths())


@_never_raises
def procnames(ctx: typer.Context) -> list[str]:
    """Every process in the registry, minus the ones already typed.

    `all` is offered only as the first word: `start all rdb1` means nothing
    the plain `start all` does not.
    """
    typed = _typed(ctx, "procs") or ()
    names = [n for n in _ports(ctx.params.get("port") or DEFAULT_BASE_PORT) if n not in typed]
    return names if typed else ["all", *names]


@_never_raises
def procname(ctx: typer.Context) -> list[str]:
    """One process, for commands that take exactly one."""
    return list(_ports(ctx.params.get("port") or DEFAULT_BASE_PORT))


@_never_raises
def process_ports(ctx: typer.Context) -> list[tuple[str, str]]:
    """A process's port, with the process named beside it - the number is
    what the option takes, the name is what the reader was looking for."""
    base = ctx.params.get("base_port") or DEFAULT_BASE_PORT
    return [(port, name) for name, port in _ports(base).items() if port]


@_never_raises
def profiles(incomplete: str) -> list[str]:
    return _comma_list(PROFILES, incomplete)


@_never_raises
def backfill_workers() -> list[str]:
    """Every worker a backfill process runs, from the registry."""
    return sorted(backfill.backfill_workers())


@_never_raises
def list_kinds() -> list[str]:
    return sorted(listing.LISTABLE_KINDS)


@_never_raises
def list_columns(ctx: typer.Context) -> list[str]:
    """The columns `--sort` can take, which differ by kind - so they are read
    off the kind's own rows. Nothing when there are no rows to read them off."""
    typed = _typed(ctx, "kind")
    kind = typed[0] if isinstance(typed, tuple) else typed
    if kind not in listing.LISTABLE_KINDS:
        return []
    rows = listing.list_items(stack_paths.default_paths(), kind)
    return list(rows[0]) if rows else []


@_never_raises
def csv_fields() -> list[str]:
    return list(PROCESS_CSV_FIELDS)


@_never_raises
def summary_columns(incomplete: str) -> list[str]:
    if "," not in incomplete:
        return ["all", "status", *listing.SUMMARY_ALL_COLUMNS]
    return _comma_list(listing.SUMMARY_ALL_COLUMNS, incomplete)


@_never_raises
def plant_table() -> list[str]:
    return sorted(_plant_tables())


@_never_raises
def plant_tables(incomplete: str) -> list[str]:
    return _comma_list(sorted(_plant_tables()), incomplete)


def choices(*values: str) -> Callable[[], list[str]]:
    """A completer for an option whose values are a fixed set."""

    def complete() -> list[str]:
        return list(values)

    return complete
