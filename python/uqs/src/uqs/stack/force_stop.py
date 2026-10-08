"""`uqs stop --force`: kill a stack's q processes outright (#875).

`uqs stop` is torq.sh's stop: `kill -15` to the pid LISTENING on each
process's configured port. That misses three cases this does not:

  - a process stuck before it opened its port - hung while loading, say -
    has nothing listening, so nothing is signalled and it runs on;
  - a duplicate instance, a second copy started by hand: only the port holder
    is signalled;
  - a process that ignores SIGTERM.

So the processes are found the way `uqs summary` finds them - by command line,
`-stackid <base port> -proctype <type> -procname <name>` (stack/alive.py) -
and EVERY matching pid gets SIGKILL. Nothing else is touched: another stack's
processes carry another -stackid, and a stray q carries none.

The last resort, and it says so: no clean shutdown, no .z.exit handler runs,
and a tickerplant killed mid-write may leave its log's last message torn.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from uqs.paths import UqsError, UqsPaths
from uqs.stack import alive


@dataclass
class Killed:
    """One pid that was signalled, and whether it is gone."""

    name: str
    pid: int
    gone: bool


def kill(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int | None = None,
    *,
    wait: float = 5.0,
    lister: Callable[..., dict[str, list[int]]] = alive.instances,
    send: Callable[[int, int], None] = os.kill,
    alive_pid: Callable[[int], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Killed]:
    """SIGKILL every running instance of `procs` ("all", or space-separated
    names), wait up to `wait` seconds for each to exit, and say which did."""
    found = lister(paths, base_port)
    wanted = list(found) if procs.strip() == "all" else procs.split()
    unknown = [n for n in wanted if n not in found]
    if unknown:
        raise UqsError(f"not a local process of this stack: {', '.join(unknown)}")
    is_alive = alive_pid or _pid_alive
    targets = [(name, pid) for name in wanted for pid in found[name]]
    for _, pid in targets:
        try:
            send(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # exited between the listing and the signal - which is the goal
    end = clock() + wait
    while any(is_alive(pid) for _, pid in targets) and clock() < end:
        sleep(0.1)
    return [Killed(name, pid, not is_alive(pid)) for name, pid in targets]


def _pid_alive(pid: int) -> bool:
    """Whether `pid` still runs. A zombie does not: it has exited and only
    waits for its parent to read its status, so signal 0 still finds it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")
