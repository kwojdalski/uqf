"""What the pipeline registry DERIVES: ports, process.csv rows, offsets.

The registry literal itself is in registry.py and the Pipeline dataclass in
pipeline.py; this is the third of the three, and the one that turns entries
into the numbers and rows the rest of the stack reads. Adding a pipeline
touches registry.py only - the per-process offsets, the process.csv rows and
the generated schema all derive from that tuple, here.

Also holds the dataflow-edge declarations and verify_pipeline_edges, which
greps each pipeline's own q script and fails if a declaration disagrees with
the code. A hand-drawn diagram goes stale silently; a derived one cannot."""

from __future__ import annotations

from pathlib import Path

from torq_orchestrator.logger import get_logger
from torq_orchestrator.pipeline import (  # noqa: F401 - re-exported: core.py imports them from here
    PIPELINE_LIB_SCRIPT,
    STREAM_RUNNER_SCRIPT,
    Pipeline,
)
from torq_orchestrator.registry import (  # noqa: F401 - re-exported, every caller imports them from here
    DEFAULT_BASE_PORT,
    FXFEED_PINNED_OFFSET,
    PIPELINE_BLOCK_START,
    PIPELINES,
)

log = get_logger(__name__)


def _resolved_offsets() -> dict[str, int]:
    """Each pipeline's `{KDBBASEPORT}+N` offset: explicit where pinned,
    otherwise allocated contiguously from PIPELINE_BLOCK_START in list order.
    """
    offsets: dict[str, int] = {}
    nxt = PIPELINE_BLOCK_START
    for pipeline in PIPELINES:
        if pipeline.offset is not None:
            offsets[pipeline.procname] = pipeline.offset
            continue
        offsets[pipeline.procname] = nxt
        nxt += 1
    return offsets


PIPELINE_OFFSETS = _resolved_offsets()
PIPELINE_BY_NAME = {pipeline.procname: pipeline for pipeline in PIPELINES}


def _pipeline_rows() -> list[dict[str, str]]:
    """One process.csv row per PIPELINES entry, in port order."""
    return [
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{PIPELINE_OFFSETS[pipeline.procname]}",
            "proctype": pipeline.proctype,
            "procname": pipeline.procname,
            "U": pipeline.access_list,
            "localtime": pipeline.localtime,
            "g": "0",
            "T": "",
            "w": "",
            "load": pipeline.load_column(),
            "startwithall": pipeline.startwithall,
            "extras": "",
            "qcmd": "q",
        }
        for pipeline in PIPELINES
    ]


# Per-process offset constants, kept as a stable public surface (tests and
# docs reference them by name) but derived from PIPELINES rather than
# hand-maintained.
FXFEED_PORT_OFFSET = PIPELINE_OFFSETS["fxfeed1"]
QUOTES_FEED_PORT_OFFSET = PIPELINE_OFFSETS["quotesfeed1"]
CROSS_ETL_PORT_OFFSET = PIPELINE_OFFSETS["cross1"]
WIDE_BOOK_FEED_PORT_OFFSET = PIPELINE_OFFSETS["widefeed1"]
VECTORIZE_ETL_PORT_OFFSET = PIPELINE_OFFSETS["vectorize1"]
TAP_PORT_OFFSET = PIPELINE_OFFSETS["tap1"]
FX_TRADES_FEED_PORT_OFFSET = PIPELINE_OFFSETS["fxtradesfeed1"]
POSBOOK_PORT_OFFSET = PIPELINE_OFFSETS["posbook1"]
MARKOUT_PORT_OFFSET = PIPELINE_OFFSETS["markout1"]


# Edge verification lives in pipeline_edges.py - see its header for why the
# split is there. Re-exported so `from pipelines import verify_pipeline_edges`
# keeps working for every existing caller.
from torq_orchestrator.pipeline_edges import (  # noqa: E402
    verify_pipeline_edges as _verify_edges,
)


def verify_pipeline_edges(scripts_dir: Path) -> list[str]:
    """Check every pipeline's declared edges against its own q script.

    A thin wrapper that supplies THIS module's registry to the checker in
    `pipeline_edges`, keeping the one-argument signature every existing
    caller uses while the two modules import in only one direction.
    """
    return _verify_edges(scripts_dir, PIPELINES)


PROCESS_CSV_FIELDS = (
    "host",
    "port",
    "proctype",
    "procname",
    "U",
    "localtime",
    "g",
    "T",
    "w",
    "load",
    "startwithall",
    "extras",
    "qcmd",
)
