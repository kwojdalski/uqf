"""Commands that work only on this tree's own pipelines (#1795).

A command that needs them says so where it is declared, on its Click class -
`cls=needs_pipelines(WHY, ReadOnly)` - as safety.marked states its Effect, and
a runtime without them (`pipelines=False`, such as torq) refuses it with WHY.
`pipelines_only()` reads the marked commands off the tree: there is no list to
keep, and test_runtime_declarations.py finds a command that reaches the
pipelines' modules unmarked.
"""

from __future__ import annotations

from typing import Any

from typer.core import TyperCommand

from uqs import paths as stack_paths
from uqs import runtimes
from uqs.cli import shared
from uqs.paths import UqsError


def needs_pipelines(reason: str, base: type[TyperCommand] = TyperCommand) -> type[TyperCommand]:
    """`base` for a command that works only on this tree's own pipelines -
    its bounded workers, streaming jobs, run ledger, connectors or process
    graph - saying, in `reason`, what it needs. A runtime without them
    (`pipelines=False`, such as torq) refuses it with that reason before
    parsing a word, rather than leave it to fail against processes and tables
    that do not exist; `job new` writes into the tree and is not marked.

    The mark is on the Click class, as safety.marked's Effect is, so a
    command states it once, where it is declared: `pipelines_only()` reads
    it off the tree, and test_runtime_declarations.py finds a command that
    reaches the pipelines' modules unmarked. Compose it with an Effect -
    `needs_pipelines(WHY, ReadOnly)` - and inside `renames.renamed(...,
    mark)`; an alias passes the same `cls=` as its new name."""

    def parse_args(self: Any, ctx: Any, args: list[str]) -> list[str]:
        # completion and target_confirm's parse are resilient: they run nothing
        if not ctx.resilient_parsing:
            _refuse_without_pipelines(ctx, reason)
        return super(marked, self).parse_args(ctx, args)

    marked = type(base.__name__, (base,), {"needs_pipelines": reason, "parse_args": parse_args})
    return marked


def _refuse_without_pipelines(ctx: Any, reason: str) -> None:
    """Refuse the command `ctx` runs, which needs `reason`, on a runtime without
    this tree's pipelines - the runtime the root callback has already chosen."""
    declared = runtimes.RUNTIMES[stack_paths.runtime_from_env()]
    if declared.pipelines:
        return
    path = []
    while ctx.parent is not None:
        path.append(ctx.info_name or "")
        ctx = ctx.parent
    command = " ".join(reversed(path))
    shared._die(
        UqsError(
            f"`uqs {command}` {reason}; "
            f"the {declared.name} runtime has none of this tree's pipelines. "
            f"Use --runtime {runtimes.DEFAULT_RUNTIME}"
        )
    )


def pipelines_only(root: Any = None) -> dict[str, str]:
    """{command path: what it needs} for every visible command marked
    `needs_pipelines`, read off the CLI tree: what a runtime without this
    tree's pipelines refuses, and `uqs runtime diff` reports."""
    from uqs.cli import target_confirm

    out: dict[str, str] = {}

    def walk(cmd: Any, path: tuple[str, ...]) -> None:
        if getattr(cmd, "hidden", False):
            return
        subs = target_confirm.subcommands(cmd)
        if not subs and (reason := getattr(cmd, "needs_pipelines", None)):
            out[" ".join(path)] = reason
        for name, sub in subs.items():
            walk(sub, (*path, name))

    walk(root if root is not None else target_confirm.root_command(), ())
    return dict(sorted(out.items()))
