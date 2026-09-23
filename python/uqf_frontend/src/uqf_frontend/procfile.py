"""Read the declared process set from TorQ's ``process.csv``.

FE-01 asks for health "for every process in ``process.csv``", so the declared
set comes from that file rather than from this package's own configuration -
otherwise the fleet view can only report on processes someone remembered to
list twice.

The file is *generated* by ``uqf_stack.stack.runtime.bootstrap()`` from
vendored inputs plus uqf's own additions (ETL-17), and its port column carries
unresolved placeholders: ``{KDBBASEPORT}`` or ``{KDBBASEPORT}+N``. Resolving
them here duplicates a few lines of the orchestrator rather than importing it,
deliberately: the placeholder grammar is two forms wide, and this is the path
every fleet-health poll takes.

This module's header used to state a stronger rule - that the package must not
depend on ``uqf_stack`` at all - and that is no longer true.
``control.py`` depends on it, because starting a process and writing a
process.csv override are its work and reimplementing them would make a second
writer to one file. The narrow reason still holds and is why this module keeps
its own resolver: a dependency is not worth taking for two lines of regex on a
read path. The broad rule was overtaken by the write surface.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

#: ``{NAME}`` optionally followed by ``+N`` or ``-N``. The same grammar
#: uqf_stack.stack.procs' own _BRACE_ARITH_RE accepts.
_BRACE_ARITH = re.compile(r"^\{(\w+)\}([+-]\d+)?$")


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
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    m = _BRACE_ARITH.match(raw)
    if m is None:
        return None
    offset = int(m.group(2)) if m.group(2) else 0
    return base_port + offset


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
