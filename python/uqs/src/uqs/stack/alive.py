"""Which processes are up: `torq.sh summary`'s answer, in two subprocesses.

torq.sh answers it one process at a time, and for each one forks some
twenty tools - awk over process.csv three times, envsubst, `hostname -i`,
pgrep twice, then netstat, grep and awk for the port - so across the ~46
processes this tree declares it is close to a thousand forks, serially,
before a single row prints. Every `uqs start` asked twice more (the
unfed-input and connection-cap warnings), and each ask also re-ran the
bootstrap, which starts q to fill the HDB. That was most of the half-minute
an ordinary command took.

This asks the operating system once: one `ps` for every command line, one
`lsof` for the listening ports of the pids that matched. It needs no
bootstrap either - it only reads, and a status check that writes the data
directory is the wrong way round.

THE SAME MATCH torq.sh makes, so the two cannot disagree about what is up:
its `findproc` is `pgrep -f "-stackid <base> -proctype <type> -procname
<name> "`, trailing space included, and a process with several matches
reports the newest pid. The output is torq.sh's `TIME | PROCESS | STATUS |
PID | PORT` table too, so `listing.summary_rows` reads either.
"""

from __future__ import annotations

import socket
import subprocess
import time
from collections import defaultdict

from uqs.logger import get_logger
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError, UqsPaths
from uqs.stack.env import build_env
from uqs.stack.procs import _base_process_rows, _read_overrides, resolve_process_config

log = get_logger(__name__)

HEADER = "TIME | PROCESS | STATUS | PID | PORT"


def _local_hosts() -> set[str]:
    """Names a process.csv `host` may use for this machine. torq.sh prints no
    row at all for a process configured on another host, and neither does
    this."""
    names = {"localhost", "127.0.0.1", "::1"}
    try:
        names |= {socket.gethostname(), socket.getfqdn()}
    except OSError:  # pragma: no cover - a machine with no hostname
        pass
    return names


def _process_rows(paths: UqsPaths, base_port: int) -> list[dict[str, str]]:
    """process.csv as the start line sees it - vendored rows, uqf's, the
    operator's overrides - resolved, without writing anything."""
    env = build_env(paths, base_port=base_port)
    overrides = _read_overrides(paths)
    rows = []
    for row in _base_process_rows(paths):
        eff = {**row, **overrides.get(row["procname"], {})}
        rows.append(resolve_process_config(eff, env))
    return rows


def _command_lines(timeout: float | None) -> list[tuple[int, str]]:
    """(pid, full command line) for every process on the machine, in pid order."""
    # -ww: no truncation, or a long start line loses its -procname.
    result = subprocess.run(
        ["ps", "-eww", "-o", "pid=,args="],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise UqsError(f"ps failed ({result.returncode}): {result.stderr.strip()[:200]}")
    out = []
    for line in result.stdout.splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit():
            out.append((int(pid), args))
    return sorted(out)


def _listening_ports(pids: list[int], timeout: float | None) -> dict[int, list[str]]:
    """pid -> the TCP ports it listens on, from one lsof over just those pids.

    Empty when lsof is missing or refuses: the port then comes from
    process.csv and is marked `configured`, which is what a missing port
    already meant.
    """
    if not pids:
        return {}
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN", "-Fpn"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("lsof unavailable, ports come from process.csv: {}", exc)
        return {}
    ports: dict[int, list[str]] = defaultdict(list)
    pid = 0
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid:
            port = line.rsplit(":", 1)[-1]
            if port.isdigit() and port not in ports[pid]:
                ports[pid].append(port)
    return ports


def status_table(
    paths: UqsPaths, base_port: int = DEFAULT_BASE_PORT, timeout: float | None = None
) -> str:
    """torq.sh summary's table: every local process, up with pid and port or down."""
    try:
        commands = _command_lines(timeout)
    except subprocess.TimeoutExpired as exc:
        raise UqsError(f"listing processes did not finish within {timeout:g}s") from exc
    local = _local_hosts()
    found: list[tuple[str, int | None]] = []
    for row in _process_rows(paths, base_port):
        if row.get("host", "localhost") not in local:
            continue
        needle = f"-stackid {base_port} -proctype {row['proctype']} -procname {row['procname']} "
        pids = [pid for pid, args in commands if needle in args + " "]
        found.append((row["procname"], pids[-1] if pids else None))
    ports = _listening_ports([pid for _, pid in found if pid is not None], timeout)
    now = time.strftime("%H:%M:%S")
    lines = [HEADER]
    for name, pid in found:
        if pid is None:
            lines.append(f"{now} | {name} | down |")
        else:
            lines.append(f"{now} | {name} | up | {pid} | {' '.join(ports.get(pid, []))}")
    return "\n".join(lines) + "\n"


def running(paths: UqsPaths, base_port: int = DEFAULT_BASE_PORT) -> set[str]:
    """The procnames that are up."""
    return {
        line.split("|")[1].strip()
        for line in status_table(paths, base_port).splitlines()[1:]
        if line.split("|")[2].strip() == "up"
    }
