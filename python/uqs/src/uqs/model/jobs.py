"""Every ETL job the tree declares, as rows: what `uqs job list` shows.

The process table (`uqs process list`) answers "what runs where"; this
answers "what jobs are there": each streaming job, normalizer and bounded
worker, read from its own q declaration - the same files the process
registry is built from, so the two cannot disagree about which jobs exist -
and each reaction, which has no process of its own and is listed with the
processes that publish what it watches.

`state` says whether a job is still a scaffold: its file carries the
SCAFFOLDED marker `uqs job new` writes and nothing clears until it is written.
It is the list `uqs job remove` will act on without --override even-if-edited.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from uqs.cells import Shown
from uqs.model.declarations import (
    declaration_calls,
    declared_outputs,
    reaction_calls,
    read_file_text,
    symbols,
)
from uqs.model.kinds import LISTED_KIND, REACTION
from uqs.model.pipeline import PipelineKind
from uqs.model.producers import BOUNDED, processes_by_table
from uqs.paths import REACTION_DIR, STREAM_DIR, WORKER_DIR

#: The marker `uqs job new` writes into every placeholder.
SCAFFOLD_MARKER = "SCAFFOLDED"


def _starts(kind: PipelineKind, start_with_all: bool) -> str:
    if kind is PipelineKind.BACKFILL:
        return "triggered"
    return "with all" if start_with_all else "on demand"


def bounded_producers(repo_root: Path) -> dict[str, list[str]]:
    """{dataset: the processes whose bounded workers fill it}.

    The datasets a reaction can watch - only a bounded worker's published
    window fires one - and the processes it will then run inside. A projection
    of the one producer map (model/producers.py, #1862), so each worker's
    process is its declaration's, q's `<worker>1` default included, and is
    not worked out a second time here.
    """
    return {t: list(ps) for t, ps in processes_by_table(repo_root, frozenset({BOUNDED})).items()}


def job_rows(repo_root: Path) -> list[dict[str, Any]]:
    """One row per declared job, streaming jobs first, then bounded workers,
    then reactions, each directory in filename order - the order src/etl/init.q
    loads them in. `reads` and `writes` are lists of names (uqs.cells: a
    worker's table says "source x" and "dataset [partition]")."""
    rows = []
    for directory in (STREAM_DIR, WORKER_DIR):
        for path in sorted((repo_root / directory).glob("*.q")):
            text = path.read_text()
            rel = path.relative_to(repo_root)
            calls = declaration_calls(text)
            for decl, (verb, _, fields) in zip(read_file_text(text, rel), calls, strict=True):
                if decl.kind is PipelineKind.BACKFILL:
                    sources = symbols(fields.get("source", ""))
                    reads = Shown(sources, ", ".join(f"source {s}" for s in sources))
                    part = symbols(fields.get("partition", ""))
                    datasets = declared_outputs(fields, decl.name)
                    shown = ", ".join(datasets) + (f" [{part[0]}]" if part else "")
                    writes = Shown(datasets, shown)
                else:
                    reads = list(decl.subscribe_to)
                    writes = list(decl.publishes)
                rows.append(
                    {
                        "job": decl.name,
                        "kind": LISTED_KIND[verb.lstrip(".")],
                        "subscribes": bool(decl.subscribe_to),
                        "procname": decl.procname,
                        "reads": reads,
                        "writes": writes,
                        "starts": _starts(decl.kind, decl.start_with_all),
                        "state": "scaffolded" if SCAFFOLD_MARKER in text else "written",
                        "file": str(rel),
                    }
                )
    producers = bounded_producers(repo_root)
    for path in sorted((repo_root / REACTION_DIR).glob("*.q")):
        text = path.read_text()
        for r in reaction_calls(text):
            rows.append(
                {
                    "job": r.name,
                    "kind": REACTION,
                    "subscribes": False,
                    # No process of its own: it runs in whichever publishes.
                    "procname": ", ".join(producers.get(r.dataset, [])) or Shown(None, "-"),
                    "reads": [r.dataset],
                    "writes": list(r.writes),
                    "starts": "triggered",
                    "state": "scaffolded" if SCAFFOLD_MARKER in text else "written",
                    "file": str(path.relative_to(repo_root)),
                }
            )
    return rows
