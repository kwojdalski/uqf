"""Who feeds whom: the process graph, and whether it is actually satisfied.

The graph already existed in three places before this module - the
`subscribe_to`/`publishes` fields on each `Pipeline`, the `.qetl.dag` registry in
q that `src/etl/generated/pipeline_dag.q` feeds, and the diagrams derived
from both. What none of them did was answer the question an operator
actually has, which is not "what is the shape of the graph" but "is the
thing I just started going to receive anything".

It will not tell you on its own. A subscriber started without its producer
subscribes SUCCESSFULLY - the table is defined on the plant whether or not
anybody publishes to it - then heartbeats, reports `up`, and receives
nothing for as long as you leave it. There is no error and no symptom
except an output table that stays empty (#290). That became easy to hit
rather than theoretical once seven processes went `startwithall=0` (#285)
and the direct-arbitrage chain landed three processes deep and entirely on
demand (#289).

WARNINGS, NOT REFUSALS. Everything here reports; nothing blocks. Starting a
subscriber before its feed is a legitimate order - it is how you avoid
missing the first batch - and several tables are published by things that
are not processes in this list at all. A guard that refused would make
those workflows impossible, which is a worse failure than the one it
prevents.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from uqs.logger import get_logger
from uqs.model.plant_schema import _publishers
from uqs.model.registry import PIPELINES
from uqs.paths import PACKAGE_DIR, repo_root

log = get_logger()

#: Where this tree's own connectors sit: the uqs.external package, whose
#: `*_feed.py` modules each declare a FEED (external/feeds.py).
CONNECTOR_DIR = PACKAGE_DIR / "src" / "uqs" / "external"


def external_producers(root: Path) -> dict[str, str]:
    """Every table a discovered connector says it produces, and who produces it.

    Tables that ALSO come from something outside the process list, and what
    that something is - each connector's own `produces` (#1776), so a
    connector is the one place that says what it feeds, and a list kept by
    hand beside them cannot drift.

    Additive, not a fallback. `crypto_book` and `crypto_trades` have a
    declared producer (`cryptomock1`) *and* an external one (cryptorust's
    recorder, which the mock exists to stand in for), and the recorder is
    the normal case - cryptomock1 is off by default precisely so the two do
    not interleave. Treating the declared producer as the only one would
    make `marketdata1` and `executions1` warn on every healthy stack, and a
    warning that is usually wrong is one an operator learns to scroll past,
    which costs more than it saves.

    Read from each module's text rather than by importing it: `model/` sits
    below `external/` and imports nothing above it (test_module_split.py).
    `external.feeds.discover` refuses a module whose text and import disagree.
    """
    out: dict[str, str] = {}
    for path in sorted((root / CONNECTOR_DIR).glob("*_feed.py")):
        for table, who in feed_produces(path).items():
            out[table] = f"{out[table]} or {who}" if table in out else who
    return out


def feed_produces(path: Path) -> dict[str, str]:
    """{table: who} from every `ExternalFeed(..., produces={...})` in `path`'s text.

    Raises ValueError for a `produces=` that is not a dict of string literals
    or module-level string constants - this module's own, or one it imports
    by name from a sibling module."""
    stat = path.stat()
    return dict(_feed_produces(str(path), stat.st_mtime_ns, stat.st_size))


@functools.lru_cache(maxsize=64)
def _feed_produces(path: str, _mtime: int, _size: int) -> tuple[tuple[str, str], ...]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names = _constants(tree, Path(path).parent)
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee(node.func) == "ExternalFeed"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "produces":
                continue
            value = keyword.value
            if not isinstance(value, ast.Dict) or any(k is None for k in value.keys):
                raise ValueError(f"{path}: a feed's produces= must be a dict literal")
            for k, v in zip(value.keys, value.values, strict=True):
                out.append((_text(k, names, path), _text(v, names, path)))
    return tuple(out)


def _callee(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _text(node: ast.expr | None, names: dict[str, str], path: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    raise ValueError(
        f"{path}: a feed's produces= holds {ast.unparse(node) if node else '**'}, which is "
        "not a string literal or a module-level string constant - model/dependencies.py "
        "reads it without importing the connector"
    )


def _constants(tree: ast.Module, folder: Path, follow: bool = True) -> dict[str, str]:
    """Module-level `NAME = "text"` bindings, and names imported from a sibling."""
    out: dict[str, str] = {}
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else (
            node.target if isinstance(node, ast.AnnAssign) else None
        )  # fmt: skip
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                out[target.id] = value.value
        elif follow and isinstance(node, ast.ImportFrom) and node.module:
            sibling = folder / f"{node.module.rsplit('.', 1)[-1]}.py"
            if node.module.startswith("uqs.external.") and sibling.is_file():
                theirs = _constants(ast.parse(sibling.read_text(encoding="utf-8")), folder, False)
                for alias in node.names:
                    if alias.name in theirs:
                        out[alias.asname or alias.name] = theirs[alias.name]
    return out


def producers_by_table(pipelines: Iterable[Any] = PIPELINES) -> dict[str, set[str]]:
    """{table: the procnames declared to publish onto it}.

    The same `_publishers` the generated `database.q` is built from, so the
    graph an operator is warned about and the schema the plant loads cannot
    describe different systems.
    """
    return _publishers(pipelines)


def inputs_by_process(pipelines: Iterable[Any] = PIPELINES) -> dict[str, tuple[str, ...]]:
    """{procname: the tables it subscribes to}.

    A process whose subscription is chosen at runtime (`tap1`, via
    `-tables`) has no fixed input to check and is absent rather than
    reported as depending on nothing.
    """
    out: dict[str, tuple[str, ...]] = {}
    for pipeline in pipelines:
        if pipeline.subscribes_dynamic:
            continue
        if subscribe_to := pipeline.subscribed_tables:
            out[pipeline.procname] = subscribe_to
    return out


def outputs_by_process(pipelines: Iterable[Any] = PIPELINES) -> dict[str, tuple[str, ...]]:
    """{procname: the tables it publishes onto}.

    Resolved the same way `_publishers` resolves them - an explicit
    `publishes` if the pipeline declares one, otherwise its single `table` -
    so this and the generated `database.q` cannot describe different systems.
    """
    out: dict[str, tuple[str, ...]] = {}
    for pipeline in pipelines:
        if declared := pipeline.published_tables:
            out[pipeline.procname] = declared
    return out


def depends_on_by_process(pipelines: Iterable[Any] = PIPELINES) -> dict[str, tuple[str, ...]]:
    """{procname: the processes that publish what it subscribes to}.

    The edge an operator actually reasons about. `inputs_by_process` answers
    "what table does this need"; this answers "and who do I have to start to
    get it", which is the question behind every `up, but idle` process.

    A table produced from outside the process list entirely - Databento's
    feed handler, cryptorust's recorder - resolves to the name in
    `external_producers` rather than being dropped, because "nothing in this
    list provides it" and "nothing provides it" are different facts and only
    one of them is a problem. A process never lists itself: a normalizer that
    republishes onto a table it also reads would otherwise appear to be its
    own dependency.
    """
    producers = producers_by_table(pipelines)
    external = external_producers(repo_root())
    out: dict[str, tuple[str, ...]] = {}
    for procname, tables in inputs_by_process(pipelines).items():
        names: list[str] = []
        for table in tables:
            for source in sorted(producers.get(table, set())):
                if source != procname and source not in names:
                    names.append(source)
            if table in external and table not in names:
                names.append(f"({table}: external)")
        if names:
            out[procname] = tuple(names)
    return out


def worker_edges(
    pipelines: Iterable[Any] = PIPELINES,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]:
    """{procname: (inputs, outputs, depends on)} for every backfill process,
    from what its bounded worker declares.

    Kept apart from `inputs_by_process` deliberately. A worker's source is a
    system outside the stack - a DuckDB file, another q process, Databento -
    not a plant table, so nothing in the process list publishes it. Folding it
    in would have `starved_processes` report every running backfill as
    unfed, the warning-that-is-usually-wrong `external_producers` exists to
    avoid. The source is labelled as one, so it does not read as a table.
    """
    out = {}
    for pipeline in pipelines:
        if pipeline.worker is None:
            continue
        source = pipeline.source
        out[pipeline.procname] = (
            (f"{source} (source)",) if source else (),
            (pipeline.dataset,) if pipeline.dataset else (),
            (f"({source}: external)",) if source else (),
        )
    return out


def dependency_rows(pipelines: Iterable[Any] = PIPELINES) -> list[dict[str, str]]:
    """One row per (process, input table): who needs what, and who provides it.

    The listing behind `uqs list dependencies`. Sorted by consumer so
    a process's inputs read together.
    """
    producers = producers_by_table(pipelines)
    external = external_producers(repo_root())
    rows: list[dict[str, str]] = []
    for procname, tables in sorted(inputs_by_process(pipelines).items()):
        for table in tables:
            sources = sorted(producers.get(table, set()))
            if table in external:
                sources.append(external[table])
            rows.append(
                {
                    "process": procname,
                    "needs": table,
                    "published_by": " or ".join(sources) if sources else "(nothing declares it)",
                }
            )
    return rows


def unfed_inputs(
    procname: str,
    running: Iterable[str],
    pipelines: Iterable[Any] = PIPELINES,
) -> list[str]:
    """Human-readable lines for *procname*'s inputs that nothing running publishes.

    Empty means every table it subscribes to has a declared producer that is
    up. `running` is the caller's own view of the fleet - the summary's
    status column, plus whatever the same command is about to start, so
    `uqs start marketdata1 superbook1 arbitrage1` is silent rather
    than warning about a process it starts in the same breath.

    A table with no declared producer at all is reported with what to start
    instead, from `external_producers`, rather than as an error - some tables
    genuinely come from outside the process list.
    """
    running = set(running)
    producers = producers_by_table(pipelines)
    externals = external_producers(repo_root())
    lines: list[str] = []
    for table in inputs_by_process(pipelines).get(procname, ()):
        declared = producers.get(table, set())
        if declared & running:
            continue
        # An external source means the table may well be flowing from
        # outside this stack, so this is context rather than a fault, and
        # it is said that way.
        external = externals.get(table)
        if declared:
            names = " ".join(sorted(declared))
            line = (
                f"{procname} subscribes to `{table}`, which no running process "
                f"publishes - start `uqs start {names}`"
            )
            lines.append(
                f"{line}, unless it is coming from {external}." if external else f"{line}."
            )
        elif external:
            lines.append(
                f"{procname} subscribes to `{table}`, which no process in this "
                f"stack publishes - it comes from {external}."
            )
        else:
            lines.append(
                f"{procname} subscribes to `{table}`, which nothing declares a "
                f"producer for at all - check the registry."
            )
    return lines


def starved_processes(
    running: Iterable[str],
    pipelines: Iterable[Any] = PIPELINES,
) -> dict[str, list[str]]:
    """{procname: why it is starved} for every RUNNING process going hungry.

    Only processes that are themselves up: a stopped consumer with a stopped
    producer is not a fault, it is a stopped chain. A process here is one
    that answers `ps`, heartbeats, and reports `up` while receiving nothing
    - the exact state that has no other symptom.
    """
    running = set(running)
    starved = {}
    for procname in sorted(running):
        reasons = unfed_inputs(procname, running, pipelines)
        if reasons:
            starved[procname] = reasons
    return starved
