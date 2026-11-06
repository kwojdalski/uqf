"""Did what a start launched stay up? (#2180)

torq.sh's `start` returns once each process is FORKED, not once it is up, so
a process that dies while loading - a missing load-plan entry, a port another
process already holds ("failed to set port") - left `uqs start` exiting 0.
The only sign was an ERROR line in a log nobody had opened yet.

So after the launcher returns, the start waits a short grace period and asks
the same two questions `uqs status` asks (stack/alive.py): is there a process
whose command line is this stack's `-stackid -proctype -procname`, and does
the process listening on its port belong to it. Polled, so a process that has
already died ends the wait early; a healthy start waits the grace period and
no longer.

Not a failure: a one-shot that exits once started (infra.ONE_SHOT, as
`uqs deploy check` skips it), and a process configured on another host,
which no local check can see.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger
from uqs.model import infra
from uqs.paths import UqsError, UqsPaths
from uqs.stack import alive

log = get_logger()

#: Seconds a start waits before it calls what it launched up (`--grace`).
DEFAULT_GRACE = 3.0
#: How often the wait looks for a process that has already died.
POLL_S = 0.5
#: How much of a failed process's log the refusal shows.
TAIL_LINES = 10


@dataclass(frozen=True)
class Failure:
    """One started process that is not up, and why."""

    procname: str
    why: str
    log: Path | None = None
    tail: tuple[str, ...] = ()


def snapshot(paths: UqsPaths, base_port: int | None) -> dict[str, list[int]]:
    """Every local process's pids before the launch, so a restart can tell a
    new process from the one it meant to replace. Empty when `ps` fails."""
    try:
        return alive.instances(paths, base_port, timeout=10)
    except UqsError as exc:
        log.warning("could not list processes before the start: {}", exc)
        return {}


def _ports(paths: UqsPaths, base_port: int | None) -> dict[str, int]:
    base = paths.runtime_declaration.resolve_base_port(base_port)
    return {
        row["procname"]: int(row["port"])
        for row in alive._process_rows(paths, base)
        if str(row.get("port", "")).isdigit()
    }


def _why(
    pids: list[int], before: list[int], holders: list[int], port: int | None, restarted: bool
) -> str:
    """'' when the process is up and is the one launched, else why not."""
    foreign = [pid for pid in holders if pid not in pids]
    held = f"port {port} is held by pid {foreign[0]}" if foreign else ""
    if not pids:
        return "exited during startup" + (f": {held}" if held else "")
    if restarted and set(pids) <= set(before):
        return f"no new process: pid {pids[-1]} is the one from before the restart"
    if foreign and not set(holders) & set(pids):
        return f"{held}, not by its own pid {pids[-1]}"
    return ""


def failures(
    paths: UqsPaths,
    procnames: list[str],
    before: dict[str, list[int]],
    base_port: int | None,
    *,
    restarted: bool = False,
    since: float = 0.0,
) -> list[Failure]:
    """Which of `procnames` are not up as this stack's own process - one look.
    `since` is when the launch began, so a stale log is not shown as this
    start's."""
    found = alive.instances(paths, base_port, timeout=10)
    checked = [n for n in procnames if n in found and n not in infra.ONE_SHOT]
    ports = _ports(paths, base_port)
    held = alive.listeners([ports[n] for n in checked if n in ports], timeout=10)
    out = []
    for name in checked:
        port = ports.get(name)
        why = _why(found[name], before.get(name, []), held.get(port, []), port, restarted)
        if why:
            out.append(Failure(name, why, *log_tail(paths, name, since)))
    return out


def confirm(
    paths: UqsPaths,
    started: Callable[[], list[str]],
    before: dict[str, list[int]],
    base_port: int | None,
    grace: float = DEFAULT_GRACE,
    *,
    restarted: bool = False,
    since: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Failure]:
    """After `grace` seconds, which started processes are not up. Returns as
    soon as one has died; a start that cannot look (`ps` fails) passes.
    `started` names what the start covered, asked once the launch is done;
    `since` is the wall-clock time the launch began."""
    end = clock() + grace
    try:
        procnames = started()
    except UqsError as exc:
        log.warning("could not tell what the start covered: {}", exc)
        return []
    while True:
        try:
            found = failures(paths, procnames, before, base_port, restarted=restarted, since=since)
        except UqsError as exc:
            log.warning("could not check what the start launched: {}", exc)
            return []
        if any(f.why.startswith("exited") for f in found) or clock() >= end:
            return found
        sleep(min(POLL_S, max(0.0, end - clock())))


def log_tail(
    paths: UqsPaths, procname: str, since: float = 0.0
) -> tuple[Path | None, tuple[str, ...]]:
    """A process's log written since `since` (wall clock) and its last lines:
    TorQ's err_ log first - where `.lg.e` writes the error that killed it -
    then its out_ log, then torq.sh's nohup file, where a process that dies
    before TorQ redirects its output leaves its last words."""
    for path in (
        paths.torq_log(procname, "err"),
        paths.torq_log(procname, "out"),
        paths.log_dir / f"torq{procname}.txt",
    ):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size and stat.st_mtime >= since:
            with path.open(errors="replace") as handle:
                return path, tuple(handle.read().splitlines()[-TAIL_LINES:])
    return None, ()


def describe(failed: list[Failure], grace: float) -> str:
    """The refusal: each failed process, why, and the tail of its log."""
    parts = [f"{len(failed)} started process(es) not up after {grace:g}s:"]
    for f in failed:
        parts.append(f"  {f.procname}: {f.why}")
        if f.log is not None:
            parts.append(f"    last lines of {f.log}:")
            parts.extend(f"      {line}" for line in f.tail)
    parts.append("See `uqs status`, and `uqs logs <proc>` for the whole log.")
    return "\n".join(parts)
