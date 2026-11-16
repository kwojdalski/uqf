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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack import attach_state
from uqs.stack.env import build_env
from uqs.stack.placeholders import resolve_process_config
from uqs.stack.procs import effective_process_rows

log = get_logger()

HEADER = "TIME | PROCESS | STATUS | PID | PORT"


#: What torq.sh asks `hostname` for to decide a host is this machine
#: (lib/torq/torq.sh's start: `hostname -i`, `hostname -I`, `hostname -A`):
#: an address, every address, every name.
_HOSTNAME_FLAGS = ("-i", "-I", "-A")


def _hostname_words(flag: str) -> set[str]:
    """`hostname <flag>`'s words, or none where it fails - BSD hostname (macOS)
    knows none of these flags, and a missing binary is no reason to fail."""
    try:
        result = subprocess.run(
            ["hostname", flag], capture_output=True, text=True, check=False, timeout=5
        )
    except OSError, subprocess.SubprocessError:
        return set()
    return set(result.stdout.split()) if result.returncode == 0 else set()


def _local_hosts() -> set[str]:
    """Names a process.csv `host` may use for this machine: what torq.sh
    starts locally, so a process it started on this machine's LAN address is
    one this sees (#2579). torq.sh prints no row at all for a process
    configured on another host, and neither does this."""
    names = {"localhost", "127.0.0.1", "::1"}
    try:
        names |= {socket.gethostname(), socket.getfqdn()}
    except OSError:  # pragma: no cover - a machine with no hostname
        pass
    for flag in _HOSTNAME_FLAGS:
        names |= _hostname_words(flag)
    return names


def _process_rows(paths: UqsPaths, base_port: int | None) -> list[dict[str, str]]:
    """process.csv as the start line sees it - vendored rows, uqf's, the
    operator's overrides - resolved, without writing anything."""
    env = build_env(paths, base_port=base_port)
    return [resolve_process_config(row, env) for row in effective_process_rows(paths)]


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


def _lsof_listening(selector: list[str], timeout: float | None) -> dict[int, list[int]] | None:
    """pid -> the TCP ports it listens on, from one lsof over `selector`
    (`-p` pids, or `-iTCP:<ports>`). None when lsof is missing or hangs."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-a", *selector, "-sTCP:LISTEN", "-Fpn"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("lsof unavailable: {}", exc)
        return None
    ports: dict[int, list[int]] = defaultdict(list)
    pid = 0
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid:
            port = line.rsplit(":", 1)[-1]
            if port.isdigit() and int(port) not in ports[pid]:
                ports[pid].append(int(port))
    return ports


def _listening_ports(pids: list[int], timeout: float | None) -> dict[int, list[str]]:
    """pid -> the TCP ports it listens on, from one lsof over just those pids.

    Empty when lsof is missing or refuses: the port then comes from
    process.csv and is marked `configured`, which is what a missing port
    already meant.
    """
    if not pids:
        return {}
    found = _lsof_listening(["-p", ",".join(map(str, pids)), "-iTCP"], timeout) or {}
    return {pid: [str(port) for port in ports] for pid, ports in found.items()}


def listeners(ports: Iterable[int], timeout: float | None = None) -> dict[int, list[int]]:
    """port -> every pid listening on it, for just `ports`, from one lsof -
    whoever the process is (#2180). Empty when lsof is missing, so a caller
    that cannot look goes ahead as it did before it looked."""
    wanted = sorted({port for port in ports if port > 0})
    if not wanted:
        return {}
    found = _lsof_listening([f"-iTCP:{','.join(map(str, wanted))}"], timeout) or {}
    held: dict[int, list[int]] = defaultdict(list)
    for pid, on in sorted(found.items()):
        for port in on:
            if port in wanted:
                held[port].append(pid)
    return dict(held)


def _matches(
    paths: UqsPaths, base_port: int | None, timeout: float | None
) -> tuple[int, dict[str, list[tuple[int, str]]]]:
    """The resolved base port, and every local process this stack declares with
    the (pid, command line) of each process that matches it, oldest first."""
    try:
        commands = _command_lines(timeout)
    except subprocess.TimeoutExpired as exc:
        raise UqsError(f"listing processes did not finish within {timeout:g}s") from exc
    local = _local_hosts()
    base_port = paths.runtime_declaration.resolve_base_port(base_port)  # torq.sh's -stackid
    found: dict[str, list[tuple[int, str]]] = {}
    for row in _process_rows(paths, base_port):
        if row.get("host", "localhost") not in local:
            continue
        needle = f"-stackid {base_port} -proctype {row['proctype']} -procname {row['procname']} "
        found[row["procname"]] = [(pid, args) for pid, args in commands if needle in args + " "]
    return base_port, found


def instances(
    paths: UqsPaths, base_port: int | None = None, timeout: float | None = None
) -> dict[str, list[int]]:
    """Every local process this stack declares, with EVERY pid whose command
    line matches it, oldest first - a duplicate started by hand included, and
    one started from any process file. status_table reports the newest, as
    torq.sh does; `uqs stop` stops only those `stoppable` calls its own."""
    return {
        name: [pid for pid, _ in hits]
        for name, hits in _matches(paths, base_port, timeout)[1].items()
    }


@dataclass(frozen=True)
class Elsewhere:
    """A process that matches a stack's process by name and stack id but was
    started from a process file that is not this stack's to stop."""

    pid: int
    #: its `-procfile`, None when its start line has none
    procfile: Path | None


def stoppable(
    paths: UqsPaths, base_port: int | None = None, timeout: float | None = None
) -> tuple[dict[str, list[int]], dict[str, list[Elsewhere]]]:
    """`instances`, split into the pids a stop may signal and the ones it may not.

    Its own are those started from THIS tree's process.csv (`-procfile`, which
    every start line carries) or, for an attachment's publisher, from the site
    process file that attachment's native registration wrote its row into
    (attach_state.registered_process_files, #2757) - the site's torq.sh starts
    it from there. Anything else that matches -stackid/-procname - another
    checkout's or a test's stack on the same base port, or a site process file
    no attachment recorded - is never signalled (#2689); it is returned so
    the stop can say it is still up, and how it is stopped."""
    base_port, found = _matches(paths, base_port, timeout)
    mine = paths.generated_procs.resolve()
    registered = attach_state.registered_process_files(base_port)
    ours: dict[str, list[int]] = {}
    others: dict[str, list[Elsewhere]] = {}
    for name, hits in found.items():
        allowed = {mine} | registered.get(name, set())
        ours[name] = [pid for pid, args in hits if _procfile(args) in allowed]
        others[name] = [
            Elsewhere(pid, _procfile(args)) for pid, args in hits if _procfile(args) not in allowed
        ]
    return ours, others


def _procfile(args: str) -> Path | None:
    """The process.csv a start line names with `-procfile`, resolved; None without one."""
    words = args.split()
    for flag, value in zip(words, words[1:], strict=False):
        if flag == "-procfile":
            return Path(value).resolve()
    return None


def status_table(
    paths: UqsPaths, base_port: int | None = None, timeout: float | None = None
) -> str:
    """torq.sh summary's table: every local process, up with pid and port or down."""
    found = [
        (name, pids[-1] if pids else None)
        for name, pids in instances(paths, base_port, timeout).items()
    ]
    ports = _listening_ports([pid for _, pid in found if pid is not None], timeout)
    now = time.strftime("%H:%M:%S")
    lines = [HEADER]
    for name, pid in found:
        if pid is None:
            lines.append(f"{now} | {name} | down |")
        else:
            lines.append(f"{now} | {name} | up | {pid} | {' '.join(ports.get(pid, []))}")
    return "\n".join(lines) + "\n"


def running(paths: UqsPaths, base_port: int | None = None) -> set[str]:
    """The procnames that are up."""
    return {
        line.split("|")[1].strip()
        for line in status_table(paths, base_port).splitlines()[1:]
        if line.split("|")[2].strip() == "up"
    }
