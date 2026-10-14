"""Read the declared process set from TorQ's ``process.csv``.

Fleet health covers every process in ``process.csv``, so the declared
set comes from that file rather than from this package's own configuration -
otherwise the fleet view can only report on processes someone remembered to
list twice.

The file is *generated* by ``uqs.stack.runtime.bootstrap()`` from
vendored inputs plus uqf's own additions, and its port column carries
unresolved placeholders: ``{KDBBASEPORT}``, ``{KDBBASEPORT}+N``, or any other
``{NAME}`` with an offset. They are resolved by ``uqs.portexpr`` - the one
reader of that grammar, shared with the orchestrator's own resolver and the
port-occupancy check. It is a leaf (it imports nothing else from ``uqs``), so
this fleet-health path, which every poll takes, still does not load the
orchestrator. A variable this package cannot know the value of resolves to
None, reported as undetermined, rather than to the base port.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from uqs import portexpr


@dataclass(frozen=True)
class DeclaredProcess:
    """One row of process.csv, with its port resolved."""

    procname: str
    proctype: str
    host: str
    port: int | None
    #: Whether `torq.sh start all` brings it up. A process with
    #: startwithall=0 being down is expected, not a fault - tap1 is the
    #: example in this repo - so the health view must not report it as one.
    start_with_all: bool

    @property
    def group(self) -> str:
        """Group membership. proctype is TorQ's own grouping axis."""
        return self.proctype


def resolve_port(raw: str, base_port: int) -> int | None:
    """Resolve a process.csv port cell, or None if it cannot be resolved.

    Returns None rather than raising: one unresolvable row must not make the
    whole fleet view unavailable. The caller reports it as undetermined.
    """
    return portexpr.resolve(raw, {"KDBBASEPORT": str(base_port)})


def read(path: Path, base_port: int) -> list[DeclaredProcess]:
    """Parse process.csv. Rows without a procname are skipped as malformed."""
    if not path.is_file():
        raise FileNotFoundError(f"no process.csv at {path}")

    out: list[DeclaredProcess] = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            procname = (row.get("procname") or "").strip()
            if not procname:
                continue
            out.append(
                DeclaredProcess(
                    procname=procname,
                    proctype=(row.get("proctype") or "").strip() or "unknown",
                    host=(row.get("host") or "localhost").strip() or "localhost",
                    port=resolve_port(row.get("port") or "", base_port),
                    start_with_all=(row.get("startwithall") or "").strip() == "1",
                )
            )
    return out
