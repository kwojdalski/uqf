"""Verifying the declared dataflow edges against the q scripts themselves.

Extracted from `pipelines.py` when that file crossed the 400-line threshold
`test_no_module_is_still_oversized` enforces. The split is along a real seam
rather than at a convenient line: `pipelines.py` DECLARES the registry, this
module CHECKS one declaration against the code it describes. They change for
different reasons — a new pipeline touches the first, a new q idiom for
publishing touches the second.

Re-exported from `pipelines` so existing callers keep working.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from torq_orchestrator.pipeline import PipelineKind

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
#: you nothing about any particular job. The declaration is read instead -
#: from src/etl/streaming/<job>.q, found by the procname it claims.
#:
#:     .qstream.register[`markout;`procname`subscribes`publishes`on_batch...!(
#:         `markout1;
#:         `trades`quote;
#:         enlist `execution_quality;
#:
#: `enlist `x` and an empty `symbol$()` are both spelled here, because a job
#: that publishes exactly one table and a job that publishes none are the two
#: cases this registry most needs to tell apart.
_REGISTER_RE = re.compile(
    r"\.qstream\.register\[\s*`([a-zA-Z_][a-zA-Z0-9_]*)\s*;(.*?)\)\]\s*;", re.S
)
#: A normalizer registers with .qstream from inside .qnorm.define, so its
#: file carries no `.qstream.register[` literal to read. Its edges are its
#: declaration's: subscribes is the key side of `sources`, publishes is the
#: normalizer's own name.
#:
#:     .qnorm.define[`executions;`procname`output`sources!(
#:         `executions1;
#:         .qsub.executions.executions;
#:         `trades`crypto_trades!`executions_from_trades`executions_from_crypto_trades)];
_NORMALIZER_RE = re.compile(r"\.qnorm\.define\[\s*`([a-zA-Z_][a-zA-Z0-9_]*)\s*;(.*?)\)\]\s*;", re.S)
_STREAM_DIR = Path("src") / "etl" / "streaming"
_WORKER_DIR = Path("src") / "etl" / "workers"

#: A bounded worker declares itself the way a streaming job does, and is
#: found the same way - by reading the declaration rather than a list kept
#: beside it.
_WORKER_RE = re.compile(r"\.qbw\.define\[\s*`([a-zA-Z_][a-zA-Z0-9_]*)")

#: Workers with no process, and the reason. Empty: one script serves every
#: worker, so a process costs one registry entry, and a worker nobody can
#: start is a worker nobody runs.
WORKERS_WITHOUT_A_PROCESS: frozenset[str] = frozenset()

#: The one process script every streaming job runs under. Spelled here rather
#: than imported from `pipelines`, which imports this module.
STREAM_RUNNER = "processes/torq_stream.q"

#: Procnames a streaming job claims that deliberately have no Pipeline entry,
#: with the reason. Empty, and that is the point: the rule is that every job
#: the tree registers can be started by the stack.
#:
#: A job that only ever runs standalone is not an exception to want - the
#: publish seam means the same job file runs under either runner, so an
#: entry here says "this job cannot be started the normal way", which needs
#: an argument stronger than "it was written for the other runner".
RUNS_WITHOUT_A_PROCESS: frozenset[str] = frozenset()

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
#: in scripts/processes/torq_stream.q, and `uqf-stack summary` reports it
#: `up` because that is a PID check. Which jobs run then depends on which
#: sixteen won the race to start (#285).
PLANT_CONNECTION_BUDGET = 16

#: Slots held back from the default start, so an operator can still open a
#: handle to the plant. `uqf-stack query --port 6050`, `uqf-stack schema`
#: and the frontend's health view each take one while they run, and a stack
#: sized exactly to the cap has none to give them - the tooling fails
#: against a stack that is, by its own reckoning, healthy.
PLANT_CONNECTION_RESERVE = 2

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


def _symbol_field(text: str) -> tuple[str, ...]:
    """The symbols in one field of a register call: a backtick list, an
    `enlist `x`, or an empty `symbol$()`."""
    text = text.strip().rstrip(";").strip()
    if text.startswith("enlist"):
        text = text[len("enlist") :].strip()
    if "symbol$()" in text:
        return ()
    return _symbol_list(text)


def _register_fields(body: str) -> dict[str, str]:
    """The `key!(value; value; ...)` of one register call, as {key: value}.

    Read by NAME rather than by position: a job that declares a timer has two
    more values than one that does not, and the whole point of this function
    is to be indifferent to that.
    """
    if "!(" not in body:
        return {}
    keys_text, values_text = body.split("!(", 1)
    keys = [key for key in keys_text.strip().strip("`").split("`") if key]
    values = [value.strip() for value in values_text.split(";")]
    return dict(zip(keys, values, strict=False))


def _declared_stream_edges(repo_root: Path) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """procname -> (subscribes, publishes), read from the job files.

    Every job under src/etl/streaming/ is read, so a job whose file exists but
    whose registry entry was forgotten is absent here and reported as a
    mismatch rather than silently agreeing.
    """
    edges: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    directory = repo_root / _STREAM_DIR
    if not directory.is_dir():
        return edges
    for path in sorted(directory.glob("*.q")):
        source = _strip_q_comments(path.read_text())
        for match in _REGISTER_RE.finditer(source):
            fields = _register_fields(match.group(2))
            procname = _symbol_field(fields.get("procname", ""))
            if not procname:
                continue
            edges[procname[0]] = (
                _symbol_field(fields.get("subscribes", "")),
                _symbol_field(fields.get("publishes", "")),
            )
        for match in _NORMALIZER_RE.finditer(source):
            fields = _normalizer_fields(match.group(2))
            procname = _symbol_field(fields.get("procname", ""))
            if not procname:
                continue
            sources = fields.get("sources", "")
            edges[procname[0]] = (
                _symbol_list(sources.split("!", 1)[0]),
                (match.group(1),),
            )
    return edges


def _declared_workers(repo_root: Path) -> set[str]:
    """Every bounded worker that registers itself under src/etl/workers/."""
    directory = repo_root / _WORKER_DIR
    if not directory.is_dir():
        return set()
    found: set[str] = set()
    for path in sorted(directory.glob("*.q")):
        found.update(_WORKER_RE.findall(_strip_q_comments(path.read_text())))
    return found


def _normalizer_fields(body: str) -> dict[str, str]:
    """The `key!(value; ...)` of one .qnorm.define call, as {key: value}.

    Unlike _register_fields, a value here may itself contain a `!` - the
    `sources` dictionary - and its own `;` separators only inside a symbol
    list, so the split is on the top-level `;` between values.
    """
    if "!(" not in body:
        return {}
    keys_text, values_text = body.split("!(", 1)
    keys = [key for key in keys_text.strip().strip("`").split("`") if key]
    values = [value.strip() for value in values_text.split(";")]
    return dict(zip(keys, values, strict=False))


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

    `pipelines` is passed in rather than imported, because importing it from
    `pipelines.py` - which imports this module - would be the same cycle
    `env.py` was extracted to break. The caller that has the registry
    supplies it.

    Returns a list of human-readable mismatches - empty means the registry
    and the code agree, so the generated diagrams describe what actually
    runs. Pipelines whose edges are chosen at runtime
    (``subscribes_dynamic``) are skipped. A publish through ``.qpipe.publish``
    is read at its call site, where the table is still a literal.
    """
    problems: list[str] = []

    # Uniqueness first, because every derived structure below and in this
    # module keys on procname and a duplicate would not error - it would
    # collapse. PIPELINE_BY_NAME and PIPELINE_OFFSETS are both dict
    # comprehensions over PIPELINES, so a repeated name silently drops one
    # pipeline from the registry and hands the survivor the other's port
    # offset. add_extra_process already refuses a duplicate at runtime; the
    # literal below it had no such check, which is the wrong way round.
    seen: dict[str, int] = {}
    for index, pipeline in enumerate(pipelines):
        if pipeline.procname in seen:
            problems.append(
                f"{pipeline.procname}: declared twice in PIPELINES "
                f"(entries {seen[pipeline.procname]} and {index}) - procnames key "
                "PIPELINE_BY_NAME and PIPELINE_OFFSETS, so a duplicate loses a "
                "process rather than reporting one"
            )
        else:
            seen[pipeline.procname] = index

    # A streaming job's edges are in its own file, not in the runner that
    # starts it - the runner is generic and mentions no table at all.
    stream_edges = _declared_stream_edges(scripts_dir.parent)

    for pipeline in pipelines:
        script = scripts_dir / pipeline.script
        if not script.is_file():
            problems.append(f"{pipeline.procname}: script {script} does not exist")
            continue

        if pipeline.script == STREAM_RUNNER:
            if pipeline.procname not in stream_edges:
                problems.append(
                    f"{pipeline.procname}: runs {STREAM_RUNNER} but no job under "
                    f"{_STREAM_DIR} claims that process - .qstream.register's "
                    "procname is how the runner finds out which job it is, so this "
                    "process would refuse to start"
                )
                continue
            subscribes, publishes = stream_edges[pipeline.procname]
            if subscribes != tuple(pipeline.subscribes):
                problems.append(
                    f"{pipeline.procname}: declares subscribes={pipeline.subscribes!r} "
                    f"but its streaming job subscribes to {subscribes!r}"
                )
            if publishes != tuple(pipeline.published_tables):
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
            if tuple(found) != tuple(pipeline.subscribes):
                problems.append(
                    f"{pipeline.procname}: declares subscribes={pipeline.subscribes!r} "
                    f"but {pipeline.script} subscribes to {tuple(found)!r}"
                )

        published = [match.group(1) for match in _PUB_RE.finditer(source)]
        if tuple(published) != tuple(pipeline.published_tables):
            problems.append(
                f"{pipeline.procname}: declares publishes={pipeline.published_tables!r} "
                f"but {pipeline.script} publishes {tuple(published)!r}"
            )

    # The other direction, and it was missing. Everything above asks "does
    # this PROCESS's job exist?". Nothing asked "does this JOB have a
    # process?", so a streaming job could register in q, subscribe to
    # nothing, publish nowhere, and no gate would say a word - which is
    # exactly what fxpositions1 and fxordersfeed1 did until #281.
    #
    # A job is TorQ-free code and a runner decides its transport, so being
    # runnable standalone is no reason to be unstartable by the stack. Every
    # registered job gets a process, or a named reason it does not.
    declared = _declared_stream_edges(scripts_dir.parent)
    have_process = {p.procname for p in pipelines}
    for procname in sorted(set(declared) - have_process - RUNS_WITHOUT_A_PROCESS):
        problems.append(
            f"{procname}: a streaming job claims this process, but no pipeline "
            f"declares it - so process.csv never mentions it and uqf-stack cannot "
            f"start it. Add a Pipeline entry, or add the procname to "
            f"RUNS_WITHOUT_A_PROCESS with the reason it is deliberately unstartable"
        )
    for procname in sorted(RUNS_WITHOUT_A_PROCESS - set(declared)):
        problems.append(
            f"{procname}: listed in RUNS_WITHOUT_A_PROCESS but no streaming job "
            f"claims it - remove the entry rather than leaving a dead exemption"
        )

    # The same rule for the bounded half. A backfill process and the worker
    # it runs are joined at RUNTIME by UQF_BACKFILL_WORKER, so nothing
    # statically connected the two until `worker` was declared - which is
    # how databento_book_backfill and upstream_trades_backfill ended up
    # fully declared with no process able to run them (#283).
    workers = _declared_workers(scripts_dir.parent)
    run_by = {p.worker for p in pipelines if p.kind is PipelineKind.BACKFILL and p.worker}
    for worker in sorted(workers - run_by - WORKERS_WITHOUT_A_PROCESS):
        problems.append(
            f"{worker}: a bounded worker declares itself but no backfill pipeline "
            f"names it, so it can only be run by hand. Add a Pipeline with "
            f"worker={worker!r}, or add it to WORKERS_WITHOUT_A_PROCESS with a reason"
        )
    for worker in sorted(WORKERS_WITHOUT_A_PROCESS - workers):
        problems.append(
            f"{worker}: listed in WORKERS_WITHOUT_A_PROCESS but no worker declares "
            f"it - remove the dead exemption"
        )
    for pipeline in pipelines:
        if pipeline.kind is PipelineKind.BACKFILL and not pipeline.worker:
            problems.append(
                f"{pipeline.procname}: a backfill pipeline must name the worker it "
                f"runs, so the link is declared rather than left to an environment "
                f"variable nobody can grep for"
            )
        elif pipeline.worker and pipeline.worker not in workers:
            problems.append(
                f"{pipeline.procname}: names worker {pipeline.worker!r}, which no "
                f"file under {_WORKER_DIR} declares - the process would start and "
                f"then refuse"
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
    allowance = PLANT_CONNECTION_BUDGET - PLANT_CONNECTION_RESERVE
    if len(plant_clients) > allowance:
        problems.append(
            f"the default start opens {len(plant_clients)} tickerplant connections "
            f"but only {allowance} are available ({PLANT_CONNECTION_BUDGET} on the "
            f"licence, {PLANT_CONNECTION_RESERVE} held back for ad-hoc handles): "
            f"{', '.join(plant_clients)}. The plant resets the extras and they wedge "
            f'in the retry loop while still reporting `up`, so set startwithall="0" '
            f"on the ones that need not run by default - with a note saying why - "
            f"or raise PLANT_CONNECTION_BUDGET if the licence has changed"
        )

    return problems
