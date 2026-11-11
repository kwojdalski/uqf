"""`uqs stop`: a stack's processes, found by command line and stopped by the
one stop policy (uqs/process_stop.py, #2689).

torq.sh's stop is `kill -15` to whatever LISTENS on each process's configured
port, and returns. That missed a process stuck before it opened its port
(nothing listening, so `kill -15` with no pid at all), a duplicate started by
hand (only the port holder was signalled), and one that ignores SIGTERM - a
busy q under KDB-X does - which it then reported as stopped; `restart` started
the next one beside it. So the processes are found the way `uqs status` finds
them - by command line, `-stackid <base port> -proctype <type> -procname
<name>` (stack/alive.py), started from this tree's own process.csv - and
EVERY matching pid gets SIGTERM, a grace to exit, and SIGKILL only with
`kill`. Another stack's processes carry another -stackid, another checkout's
on the same base port another -procfile, and a stray q carries neither:
nothing else is touched.

`all` is torq.sh's: every startwithall=1 row. A stop needs no bootstrap - it
writes nothing - but it holds the stack's lifecycle lock, as torq.sh's did, so
it cannot interleave with a start.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass

from uqs.paths import UqsError, UqsPaths
from uqs.process_stop import DEFAULT_GRACE_SECONDS, Stopped, stop_pids
from uqs.stack import alive, generated
from uqs.stack import procs as stack_procs


@dataclass(frozen=True)
class ProcessStopped:
    """One pid of a named process, and what the stop did to it."""

    name: str
    result: Stopped

    def line(self) -> str:
        return f"{self.name}: {self.result.verdict} pid {self.result.pid}"


def _wanted(paths: UqsPaths, procs: str, found: dict[str, list[int]]) -> list[str]:
    """The names `procs` selects: `all` is every startwithall=1 row this
    machine runs, anything else the names themselves - each a local process."""
    if procs.strip() == "all":
        starting = {
            r["procname"]
            for r in stack_procs.effective_process_rows(paths)
            if r.get("startwithall") == "1"
        }
        return [name for name in found if name in starting]
    wanted = procs.split()
    unknown = [n for n in wanted if n not in found]
    if unknown:
        raise UqsError(f"not a local process of this stack: {', '.join(unknown)}")
    return wanted


def stop_stack(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int | None = None,
    *,
    kill: bool = False,
    grace: float = DEFAULT_GRACE_SECONDS,
    lock_wait: float | None = None,
    timeout: float | None = None,
) -> tuple[list[str], list[ProcessStopped]]:
    """Stop every running instance of `procs`: the names that were not
    running, and what became of each pid that was."""
    stack_procs.assert_known_procnames(paths, procs)  # below every front end (#887)
    with generated.lifecycle_lock(paths, wait=lock_wait):
        found = alive.instances(paths, base_port, timeout, own_procfile=True)
        wanted = _wanted(paths, procs, found)
        targets = [(name, pid) for name in wanted for pid in found[name]]
        results = stop_pids([pid for _, pid in targets], kill=kill, grace=grace)
    by_pid = {r.pid: r for r in results}
    idle = [name for name in wanted if not found[name]]
    return idle, [ProcessStopped(name, by_pid[pid]) for name, pid in targets]


def stop(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int | None = None,
    *,
    kill: bool = False,
    grace: float = DEFAULT_GRACE_SECONDS,
    capture: bool = False,
    lock_wait: float | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """stop_stack, reported as the torq.sh run it replaces was: a line per
    process, exit 0 when every pid is gone and 1 when any survives. Printed
    unless `capture`, which returns the lines as stdout. `timeout` bounds the
    process listing, as it bounds `uqs status`'s."""
    idle, stopped = stop_stack(
        paths, procs, base_port, kill=kill, grace=grace, lock_wait=lock_wait, timeout=timeout
    )
    now = time.strftime("%H:%M:%S")
    lines = [f"{now} | {name} is not currently running" for name in idle]
    lines += [f"{now} | {s.line()}" for s in stopped]
    survivors = [s for s in stopped if not s.result.gone]
    if survivors:
        names = " ".join(dict.fromkeys(s.name for s in survivors))
        names += f" --base-port {base_port}" if base_port is not None else ""
        after = (
            "SIGTERM and SIGKILL" if kill else f"SIGTERM - `uqs stop {names} --kill` SIGKILLs them"
        )
        lines.append(f"{len(survivors)} still running {grace:g}s after {after}")
    text = "".join(f"{line}\n" for line in lines)
    if not capture:
        sys.stdout.write(text)
        sys.stdout.flush()
    code = 1 if survivors else 0
    return subprocess.CompletedProcess(["stop", procs], code, text if capture else None, "")
