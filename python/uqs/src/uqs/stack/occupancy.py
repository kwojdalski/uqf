"""Whether another stack already holds the ports a start needs (#762).

Two runtimes - or one runtime started twice on different `--port`s - can be
on one machine at once. When their port spans overlap, a start used to fail
process by process with bind errors that named no cause. This finds the
other stack first and refuses by name.

Every TorQ process's start line carries `-stackid <KDBBASEPORT>`,
`-procname` and `-procfile <data dir>/process.csv` (lib/torq/torq.sh's
startline). The procfile says which stack the process belongs to - each
runtime has its own data directory - and its own row says which port it
listens on. So this reads `ps` and the other stack's process.csv, and
needs no connection to anything.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.runtimes import DEFAULT_RUNTIME, RUNTIMES
from uqs.stack import alive

log = get_logger(__name__)

_BASE_PLACEHOLDER = re.compile(r"\{KDBBASEPORT\}(?:\+(\d+))?")


@dataclass(frozen=True)
class Holder:
    """A process of another stack, listening on a port this one needs."""

    procname: str
    pid: int
    port: int
    stackid: int
    data_dir: Path


def _switch(words: list[str], name: str) -> str | None:
    try:
        return words[words.index(f"-{name}") + 1]
    except ValueError, IndexError:
        return None


def _port_in(procfile: Path, procname: str, stackid: int) -> int | None:
    """`procname`'s port from its own stack's process.csv, resolved."""
    try:
        with procfile.open(newline="") as handle:
            rows = {row.get("procname"): row for row in csv.DictReader(handle)}
    except OSError:
        return None
    raw = (rows.get(procname) or {}).get("port", "")
    match = _BASE_PLACEHOLDER.fullmatch(raw.strip())
    if match:
        return stackid + int(match.group(1) or 0)
    return int(raw) if raw.strip().isdigit() else None


def holders(
    paths: UqsPaths, base_port: int | None, procs: str = "all", timeout: float | None = 10
) -> list[Holder]:
    """Processes of any OTHER stack on a port that `procs` of this one would
    use (`all`: every process this runtime declares)."""
    base = paths.runtime_declaration.resolve_base_port(base_port)
    named = None if procs.strip() in ("", "all") else set(procs.split())
    ours = {
        int(row["port"]): row["procname"]
        for row in alive._process_rows(paths, base)
        if str(row.get("port", "")).isdigit() and (named is None or row["procname"] in named)
    }
    found = []
    for pid, command in alive._command_lines(timeout):
        words = command.split()
        stackid, procname, procfile = (
            _switch(words, n) for n in ("stackid", "procname", "procfile")
        )
        if not (stackid and stackid.isdigit() and procname and procfile):
            continue
        data_dir = Path(procfile).parent
        if data_dir == paths.torqdata and int(stackid) == base:
            continue  # this stack's own process
        port = _port_in(Path(procfile), procname, int(stackid))
        if port in ours:
            found.append(Holder(procname, pid, port, int(stackid), data_dir))
    return found


def describe(paths: UqsPaths, holder: Holder) -> tuple[str, str]:
    """(what the other stack is, how to stop it)."""
    for runtime in RUNTIMES.values():
        if holder.data_dir == paths.repo_root / "output" / runtime.data_dir:
            flag = "" if runtime.name == DEFAULT_RUNTIME else f" --runtime {runtime.name}"
            port = "" if holder.stackid == runtime.base_port else f" --port {holder.stackid}"
            return (
                f"the {runtime.name} runtime's stack (base port {holder.stackid})",
                f"Stop it with `uqs{flag} stop{port}`",
            )
    # Another checkout's stack - a worktree beside this one, typically: its
    # data directory is <checkout>/output/<data_dir>.
    return (
        f"a stack whose data is in {holder.data_dir} (base port {holder.stackid})",
        f"Stop it from the checkout it belongs to ({holder.data_dir.parent.parent})",
    )


def refuse_if_taken(paths: UqsPaths, base_port: int | None, procs: str = "all") -> None:
    """Raise, naming the other stack, when it holds a port this start needs.

    Advisory when it cannot look: a `ps` that fails or hangs is logged and
    the start goes ahead, which is how starts behaved before this check.
    """
    try:
        taken = holders(paths, base_port, procs)
    except UqsError as exc:
        log.warning("could not check for another stack on these ports: {}", exc)
        return
    if not taken:
        return
    what, stop = describe(paths, taken[0])
    shown = ", ".join(f"{h.procname} :{h.port} (pid {h.pid})" for h in taken[:5])
    more = f" and {len(taken) - 5} more" if len(taken) > 5 else ""
    raise UqsError(
        f"{what} is already using ports this start needs: {shown}{more}. "
        f"{stop}, or start this one elsewhere with --port."
    )
