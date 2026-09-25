"""Verifying the declared dataflow edges against the q scripts themselves.

Extracted from `model/pipelines.py` when that file crossed the 400-line threshold
`test_no_module_is_still_oversized` enforces. The split is along a real seam
rather than at a convenient line: `model/pipelines.py` DECLARES the registry, this
module CHECKS one declaration against the code it describes. They change for
different reasons — a new pipeline touches the first, a new q idiom for
publishing touches the second.

Re-exported from `pipelines` so existing callers keep working.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from uqs.model.pipeline import FROM_DECLARATION, STREAM_RUNNER_SCRIPT, PipelineKind

# `repo_root` is aliased because several functions here take a repo root as
# a PARAMETER of that name, which would shadow the finder.
from uqs.paths import STREAM_DIR, UqsError
from uqs.paths import repo_root as find_repo_root

#
# The dataflow edges declared above are what the generated diagrams draw.
# A declaration nobody checks is just a second place for the truth to rot,
# so these three patterns read the edges back out of the q scripts:
#
#   .sub.subscribe[`trades`quote;...]        direct subscribe
#   .qpipe.subscribe_etl[`markout;`trades`quote]  subscribe via the library
#   h (`.u.upd;`position;...)                publish directly
#   .qpipe.publish[h;`position;...]          publish via the library
#
# A q symbol-vector literal is backtick-joined with no separator
# (`trades`quote), which is why one regex yields the whole list and it is
# split afterwards.
_SUB_DIRECT_RE = re.compile(r"^\s*\.sub\.subscribe\[\s*((?:`[a-zA-Z_][a-zA-Z0-9_]*)+)\s*;", re.M)
_SUB_QPIPE_RE = re.compile(
    r"\.qpipe\.subscribe_etl\[\s*`[a-zA-Z0-9_]*\s*;\s*((?:`[a-zA-Z_][a-zA-Z0-9_]*)+)\s*\]"
)
_PUB_RE = re.compile(
    r"(?:h\s*\(\s*`\.u\.upd|\.qpipe\.publish\[\s*h)\s*;\s*`([a-zA-Z_][a-zA-Z0-9_]*)\s*;"
)

#: A streaming job declares its own edges rather than spelling out the calls:
#: the subscribe, the publish and the timer all happen in the one runner
#: (scripts/processes/torq_stream.q), which is generic, so reading THAT file back tells
#: you nothing about any particular job. The declaration is read instead, by
#: model/declarations.py - the same reader the registry is built from.


#: How many inbound connections one q process will accept at once.
#:
#: This is a LICENCE limit, not a kdb+ one: the community edition in
#: ~/.kx/kc.lic refuses the seventeenth. Measured rather than assumed - a
#: throwaway `\p 19099` server, and a client opening handles until one
#: fails, stops at sixteen and then reports `conn`.
#:
#: It matters here because every streaming job is its own process and every
#: one of them opens a handle to stp1. Exceed it and the plant does not
#: complain: it resets the connection, the process wedges in the retry loop
#: in scripts/processes/torq_stream.q, and `uqs summary` reports it
#: `up` because that is a PID check. Which jobs run then depends on which
#: sixteen won the race to start (#285).
LICENCE_CONNECTION_LIMIT = 16

#: Slots held back from the default start, so an operator can still open a
#: handle to the plant. `uqs query --port 6050`, `uqs schema`
#: and the frontend's health view each take one while they run, and a stack
#: sized exactly to the cap has none to give them - the tooling fails
#: against a stack that is, by its own reckoning, healthy.
INBOUND_RESERVE = 2

#: Vendored processes that hold an stp1 slot on a default start, so the
#: budget counts them alongside this tree's own.
#:
#: They are listed rather than derived because their subscription lives in
#: lib/torq settings files this tree must not edit, and reading those to
#: infer a connection would couple the gate to the vendored tree's layout.
#: A name here that stops connecting costs a spare slot, which the reserve
#: absorbs; one that starts connecting and is missing is caught the first
#: time the stack is started, by the process that fails to get in.
#:
#: `feed1` is a plant client too and is deliberately absent: it is the
#: starter pack's random demo feed, VENDORED_STARTWITHALL_OVERLAY turns it
#: off, and this set counts only what a default start actually runs. Put it
#: back here if that overlay ever does.
VENDORED_PLANT_CLIENTS: frozenset[str] = frozenset({"rdb1", "wdb1", "sctp1", "metrics1"})


def _declared_stream_edges(repo_root: Path) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """procname -> (subscribeto, publishes), read from the job files.

    Every job under src/etl/streaming/ is read, so a job whose file exists but
    whose process is missing is absent from nothing and reported as a
    mismatch rather than silently agreeing. Parsed by model/declarations.py,
    which the registry itself is built from - one reader of q declarations,
    and one that honours strings, because a job's note is prose with `;` in it.
    """
    from uqs.model.declarations import read_file

    edges: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    directory = repo_root / STREAM_DIR
    if not directory.is_dir():
        return edges
    for path in sorted(directory.glob("*.q")):
        for declaration in read_file(path):
            edges[declaration.procname] = (declaration.subscribeto, declaration.publishes)
    return edges


@lru_cache(maxsize=8)
def _stream_edge_cache(repo_root: Path) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """`_declared_stream_edges`, memoised per repo root.

    Every consumer of a resolved edge would otherwise re-read and re-parse
    twenty-odd q files, and `_publishers` is on the path that generates
    `database.q` on every command.
    """
    return _declared_stream_edges(repo_root)


def resolve_edges(
    pipeline: Any, repo_root: Path | None = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(subscribeto, published_tables) for one pipeline, reading the q file
    where the registry defers to it.

    STRICT BY DESIGN. A pipeline that says FROM_DECLARATION and has no
    declaration to read raises, rather than resolving to empty. An empty
    publish set silently drops the pipeline's tables out of the generated
    `database.q`, and a table the plant does not define discards its rows
    without an error - which is the failure #288 exists to prevent, and
    exactly the one this indirection could reintroduce.
    """
    subscribeto = pipeline.subscribeto
    publishes = pipeline.publishes
    needs = subscribeto is FROM_DECLARATION or publishes is FROM_DECLARATION
    if needs:
        root = repo_root or find_repo_root()
        declared = _stream_edge_cache(root).get(pipeline.procname)
        if declared is None:
            raise UqsError(
                f"{pipeline.procname}: declares its edges in q (FROM_DECLARATION) but no "
                f"`.qstream.define`/`.qnorm.define` naming that process was found under "
                f"{STREAM_DIR}. Either the job file is missing, its procname disagrees "
                f"with the registry, or the edges belong back in the Pipeline entry"
            )
        if subscribeto is FROM_DECLARATION:
            subscribeto = declared[0]
        if publishes is FROM_DECLARATION:
            publishes = declared[1]
    if publishes is None:
        publishes = (pipeline.table,) if pipeline.table else ()
    return tuple(subscribeto), tuple(publishes)


def _symbol_list(match_text: str) -> tuple[str, ...]:
    """A q symbol vector, e.g. trades+quote, split into ("trades", "quote")."""
    return tuple(part for part in match_text.split("`") if part)


def _strip_q_comments(source: str) -> str:
    """Drop q line comments so a `.u.upd` inside prose is not read as code.

    q treats `/` as a comment only at line start or after whitespace, which
    is exactly the distinction needed here - the publish calls all sit
    inside expressions where no bare `/` precedes them.
    """
    out = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("/"):
            continue
        out.append(line)
    return "\n".join(out)


def verify_pipeline_edges(scripts_dir: Path, pipelines: Sequence[Any]) -> list[str]:
    """Check every pipeline's declared edges against its own q script.

    `pipelines` is passed in rather than imported, so a test can check a
    doctored registry. Procnames are already unique: model/registry.py refuses
    a duplicate before a registry exists.

    Returns a list of human-readable mismatches - empty means the registry
    and the code agree, so the generated diagrams describe what actually
    runs. Pipelines whose edges are chosen at runtime
    (``subscribes_dynamic``) are skipped. A publish through ``.qpipe.publish``
    is read at its call site, where the table is still a literal.
    """
    problems: list[str] = []

    # A streaming job's edges are in its own file, not in the runner that
    # starts it - the runner is generic and mentions no table at all.
    stream_edges = _declared_stream_edges(scripts_dir.parent)

    for pipeline in pipelines:
        script = scripts_dir / pipeline.script
        if not script.is_file():
            problems.append(f"{pipeline.procname}: script {script} does not exist")
            continue

        if pipeline.script == STREAM_RUNNER_SCRIPT:
            if pipeline.procname not in stream_edges:
                problems.append(
                    f"{pipeline.procname}: runs {STREAM_RUNNER_SCRIPT} but no job under "
                    f"{STREAM_DIR} claims that process - .qstream.define's "
                    "procname is how the runner finds out which job it is, so this "
                    "process would refuse to start"
                )
                continue
            # An edge the registry defers to the q file cannot disagree with
            # it - there is one declaration, not two - so there is nothing to
            # compare and the check is skipped rather than passed. Only an
            # edge still spelled in the Pipeline entry is checked, which is
            # what this function is for: two copies that could drift.
            subscribeto, publishes = stream_edges[pipeline.procname]
            if pipeline.subscribeto is not FROM_DECLARATION and subscribeto != tuple(
                pipeline.subscribeto
            ):
                problems.append(
                    f"{pipeline.procname}: declares subscribeto={pipeline.subscribeto!r} "
                    f"but its streaming job subscribes to {subscribeto!r}"
                )
            if pipeline.publishes is not FROM_DECLARATION and publishes != tuple(
                pipeline.published_tables
            ):
                problems.append(
                    f"{pipeline.procname}: declares publishes={pipeline.published_tables!r} "
                    f"but its streaming job publishes {publishes!r}"
                )
            continue

        source = _strip_q_comments(script.read_text())

        if not pipeline.subscribes_dynamic:
            found: list[str] = []
            for match in _SUB_DIRECT_RE.finditer(source):
                found.extend(_symbol_list(match.group(1)))
            for match in _SUB_QPIPE_RE.finditer(source):
                found.extend(_symbol_list(match.group(1)))
            if tuple(found) != tuple(pipeline.subscribeto):
                problems.append(
                    f"{pipeline.procname}: declares subscribeto={pipeline.subscribeto!r} "
                    f"but {pipeline.script} subscribes to {tuple(found)!r}"
                )

        published = [match.group(1) for match in _PUB_RE.finditer(source)]
        if tuple(published) != tuple(pipeline.published_tables):
            problems.append(
                f"{pipeline.procname}: declares publishes={pipeline.published_tables!r} "
                f"but {pipeline.script} publishes {tuple(published)!r}"
            )

    # Every plant client the default start brings up costs one of the
    # licence's sixteen inbound connections, and the plant does not refuse
    # the seventeenth in a way anyone notices: it resets the handle, the
    # process retries forever inside torq_stream.q's init, and a PID check
    # calls it `up`. So the sixteen that get in are whichever sixteen won
    # the race - a topology that changes on every boot and cannot be read
    # off any file (#285).
    #
    # Counted here, against the registry, so growing the stack past what it
    # can run fails when the process is DECLARED rather than the next time
    # someone starts it. A pipeline that should not start with the stack
    # says so with startwithall="0" and a note, which is the distinction
    # this check exists to force: declaring a job and running it are
    # separate decisions.
    # A backfill is bounded: it registers with discovery, runs its window
    # and exits, and never subscribes to the plant - so it is not a client
    # even on the day one is set to start with the stack.
    plant_clients = sorted(
        {
            pipeline.procname
            for pipeline in pipelines
            if pipeline.startwithall == "1" and pipeline.kind is not PipelineKind.BACKFILL
        }
        | VENDORED_PLANT_CLIENTS
    )
    allowance = LICENCE_CONNECTION_LIMIT - INBOUND_RESERVE
    if len(plant_clients) > allowance:
        problems.append(
            f"the default start opens {len(plant_clients)} tickerplant connections "
            f"but only {allowance} are available ({LICENCE_CONNECTION_LIMIT} on the "
            f"licence, {INBOUND_RESERVE} held back for ad-hoc handles): "
            f"{', '.join(plant_clients)}. The plant resets the extras and they wedge "
            f'in the retry loop while still reporting `up`, so set startwithall="0" '
            f"on the ones that need not run by default - with a note saying why - "
            f"or raise LICENCE_CONNECTION_LIMIT if the licence has changed"
        )

    return problems
