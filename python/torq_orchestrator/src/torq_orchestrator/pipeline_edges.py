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

    for pipeline in pipelines:
        script = scripts_dir / pipeline.script
        if not script.is_file():
            problems.append(f"{pipeline.procname}: script {script} does not exist")
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
