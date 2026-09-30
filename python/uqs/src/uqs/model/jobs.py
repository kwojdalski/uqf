"""Every ETL job the tree declares, as rows: what `uqs list jobs` shows.

The process table (`uqs list processes`) answers "what runs where"; this
answers "what jobs are there": each streaming job, normalizer and bounded
worker, read from its own q declaration - the same files the process
registry is built from, so the two cannot disagree about which jobs exist.

`state` says whether a job is still a scaffold: its file carries the
SCAFFOLDED marker `uqs job new` writes and nothing clears until it is written.
It is the list `uqs job remove` will act on without --force.
"""

from __future__ import annotations

from pathlib import Path

from uqs.model.declarations import declaration_calls, read_file_text, symbols
from uqs.model.pipeline import PipelineKind
from uqs.paths import STREAM_DIR, WORKER_DIR

#: The marker `uqs job new` writes into every placeholder.
SCAFFOLD_MARKER = "SCAFFOLDED"


def _starts(kind: PipelineKind, start_with_all: bool) -> str:
    if kind is PipelineKind.BACKFILL:
        return "triggered"
    return "with all" if start_with_all else "on demand"


def job_rows(repo_root: Path) -> list[dict[str, str]]:
    """One row per declared job, streaming jobs first, each directory in
    filename order - the order src/etl/init.q loads them in."""
    rows = []
    for directory in (STREAM_DIR, WORKER_DIR):
        for path in sorted((repo_root / directory).glob("*.q")):
            text = path.read_text()
            rel = path.relative_to(repo_root)
            calls = declaration_calls(text)
            for decl, (_, _, fields) in zip(read_file_text(text, rel), calls, strict=True):
                if decl.kind is PipelineKind.BACKFILL:
                    reads = ", ".join(f"source {s}" for s in symbols(fields.get("source", "")))
                    part = symbols(fields.get("partition", ""))
                    writes = ", ".join(symbols(fields.get("dataset", "")))
                    writes += f" [{part[0]}]" if part else ""
                else:
                    reads = ", ".join(decl.subscribe_to)
                    writes = ", ".join(decl.publishes)
                rows.append(
                    {
                        "job": decl.name,
                        "kind": str(decl.kind),
                        "procname": decl.procname,
                        "reads": reads,
                        "writes": writes,
                        "starts": _starts(decl.kind, decl.start_with_all),
                        "state": "scaffolded" if SCAFFOLD_MARKER in text else "written",
                        "file": str(rel),
                    }
                )
    return rows
