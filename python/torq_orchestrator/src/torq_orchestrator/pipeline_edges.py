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
#: (scripts/torq_stream.q), which is generic, so reading THAT file back tells
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
_STREAM_DIR = Path("src") / "etl" / "streaming"

#: The one process script every streaming job runs under. Spelled here rather
#: than imported from `pipelines`, which imports this module.
STREAM_RUNNER = "torq_stream.q"


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
    return edges


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
    return problems
