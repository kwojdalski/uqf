"""What the pipeline registry DERIVES: ports, process.csv rows, offsets.

The registry is built in model/registry.py, from the q declarations, and the
Pipeline dataclass is in model/pipeline.py; this is the third of the three, and
the one that turns entries into the numbers and rows the rest of the stack
reads. Adding a pipeline touches its q file only - the per-process offsets, the
process.csv rows and the generated schema all derive from there.

Also holds the dataflow-edge declarations and verify_pipeline_edges, which
greps each pipeline's own q script and fails if a declaration disagrees with
the code. A hand-drawn diagram goes stale silently; a derived one cannot."""

from __future__ import annotations

from pathlib import Path

from uqf_stack.logger import get_logger
from uqf_stack.model.pipeline import (  # noqa: F401 - re-exported: core.py imports them from here
    PIPELINE_LIB_SCRIPT,
    STREAM_RUNNER_SCRIPT,
    Pipeline,
)
from uqf_stack.model.registry import (  # noqa: F401 - re-exported, every caller imports them from here
    DEFAULT_BASE_PORT,
    PIPELINES,
    allocate_offsets,
    read_port_lock,
)

log = get_logger(__name__)


def _resolved_offsets() -> dict[str, int]:
    """Each pipeline's `{KDBBASEPORT}+N` offset, as model/registry.py read it
    from the port lock (or allocated it, for a process not yet in the lock).
    """
    return {p.procname: p.offset for p in PIPELINES if p.offset is not None}


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


# Edge verification lives in model/pipeline_edges.py - see its header for why the
# split is there. Re-exported so `from pipelines import verify_pipeline_edges`
# keeps working for every existing caller.
from uqf_stack.model.pipeline_edges import (  # noqa: E402
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
