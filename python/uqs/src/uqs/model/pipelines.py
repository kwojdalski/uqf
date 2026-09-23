"""What the pipeline registry DERIVES: ports, process.csv rows, offsets.

The registry is built in model/registry.py, from the q declarations, and the
Pipeline dataclass is in model/pipeline.py; this is the third of the three, and
the one that turns entries into the numbers and rows the rest of the stack
reads. Adding a pipeline touches its q file only - the per-process offsets, the
process.csv rows and the generated schema all derive from there."""

from __future__ import annotations

from uqs.logger import get_logger
from uqs.model.registry import (
    PIPELINES,
)

log = get_logger(__name__)


def _resolved_offsets() -> dict[str, int]:
    """Each pipeline's `{KDBBASEPORT}+N` offset, as model/registry.py read it
    from the port lock (or allocated it, for a process not yet in the lock).
    """
    return {p.procname: p.offset for p in PIPELINES if p.offset is not None}


PIPELINE_OFFSETS = _resolved_offsets()


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
