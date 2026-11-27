"""Is the process that wrote a file gone? One rule for every reader (#1362).

q records the writer of a status file, a lock owner and a heartbeat as a pid
and a host (`.z.i`, `.z.h`). A reader can prove that writer dead only on the
same host - another host's pid says nothing here - and must compare host
names without case: q's `.z.h` is lower-case where `socket.gethostname()` may
not be, and comparing them with case made every local writer look like
another host's, alive forever (#726).

A hostname is not a machine either: two containers or cloned VMs can share
one, and a status directory, with separate pid numbering. A writer that
records its pid space (boot id and pid namespace, `local_pid_space`) beside
its host is this machine's only when that matches too; one that records none
- an older version, or macOS, where there is no /proc - is judged by its host
alone (#2986). A record in another pid space written before this one began
(its pid 1's start) is this host before a reboot or this container before its
restart, and gone; one written since is another machine's, never gone here.

A live pid is not proof either: after a container restart the worker is pid 1
again, and so was the dead writer. Where the record carries a time its writer
was alive, a live process that started after that time is a newcomer with a
recycled number, and the writer is gone - the reader's form of q's
`.qetl.job.bounded.state.held_by_predecessor` (#2497, #2652).
python/uqs/tests/test_liveness_q_parity.py puts one table of cases to both.

Used by `uqs job backfill --wait` (stack/backfill_wait.py), the attachment
backfill owner records (attach/backfill_owner.py, stack/backfill_status.py)
and uqf_frontend's status view. A leaf: it imports nothing
from `uqs`, because uqf_frontend's read path imports it (the same rule as
timefmt, #1326). uqf_airflow_provider does not depend on uqs, so its sensor
carries a copy of these functions that
python/uqs/tests/test_process_gone_rule.py holds equal to this one.
"""

from __future__ import annotations

import os
import socket
import subprocess
from datetime import UTC, datetime

#: Where Linux names this boot, and this process's pid namespace.
BOOT_ID = "/proc/sys/kernel/random/boot_id"
PID_NAMESPACE = "/proc/self/ns/pid"


def local_pid_space() -> str:
    """The space this machine's pids are numbered in: its boot id and pid namespace
    (`<boot id>/pid:[<inode>]`), as q's `.qetl.job.bounded.state.pid_space` records
    it - or "" where there is no /proc to read them from (macOS), and the host alone
    says which machine (#2986)."""
    try:
        with open(BOOT_ID) as fh:
            boot = fh.read().strip()
        space = os.readlink(PID_NAMESPACE)
    except OSError:
        return ""
    return f"{boot}/{space}" if boot and space else ""


def same_pid_space(pid_space: object) -> bool:
    """Was a record written in this machine's pid space - or does either side have
    none (a version that wrote none, or macOS), so the host alone decides (#2986)?"""
    recorded, here = str(pid_space or ""), local_pid_space()
    return not recorded or not here or recorded == here


def on_this_host(host: str, pid_space: object = None) -> bool:
    """Was `host` (as q's `.z.h` recorded it) this machine, as it is now? Two
    containers or cloned VMs can share a hostname but not a pid space, so where the
    writer recorded one and this machine has one, they must match too (#2986)."""
    return host.lower() == socket.gethostname().lower() and same_pid_space(pid_space)


def pid_alive(pid: int) -> bool:
    """Is `pid` running on this machine? A process owned by another user
    raises PermissionError, and that one IS running - the reason
    `.qetl.job.bounded.state.pid_alive` uses `ps -p` rather than `kill -0`."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recorded_instant(value: object) -> datetime | None:
    """A time a writer recorded, as an aware UTC datetime, or None when there
    is none to read. q's `.j.j` writes `.z.p` - UTC - with no zone and nine
    fractional digits; a Python writer's `isoformat()` carries `+00:00`."""
    try:
        when = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when


def process_started(pid: int) -> datetime | None:
    """When the process now numbered `pid` started, or None when that cannot
    be read - no `ps`, a `ps` without `lstart`, or no such process. `ps`
    rather than /proc so it reads the same on macOS. `lstart` is local time,
    truncated to the second: never later than the real start, so a reader
    comparing it can only err towards "the same process"."""
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
            timeout=5,
            check=False,
        ).stdout
        local = datetime.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y")
    except OSError, ValueError, subprocess.SubprocessError:
        return None
    return local.astimezone(UTC)


def predates_pid_space(alive_at: object) -> bool:
    """A record on this host but in another pid space: was it written before this
    pid space began - the start of its pid 1? Then it is this machine before a reboot,
    or this container before its restart, and its writer is gone (#2497). Written
    since, or either time unreadable: another machine sharing the hostname, whose
    writer cannot be proven dead here (#2986). q's `predates_pid_space`."""
    recorded = recorded_instant(alive_at)
    began = process_started(1) if recorded is not None else None
    return recorded is not None and began is not None and recorded < began


def process_gone(host: str, pid: int, alive_at: object = None, pid_space: object = None) -> bool:
    """Is the process `pid` on `host` provably dead? Only on this host: a
    pid recorded elsewhere is never gone, so a reader keeps waiting.

    `alive_at` is a time the writer recorded while it was running - a lock
    owner's `started`, a status file's `updated_at`. A live `pid` that
    started AFTER it is not the writer, only a process given its number
    since: a restarted container makes that the usual case, its worker pid 1
    again (#2497). q's `held_by_predecessor` is the same test from the inside,
    where the live process is the one asking. Without `alive_at`, or when
    either time cannot be read, a live pid is the writer.

    A record in another pid space on this host is judged by `predates_pid_space`:
    its pid numbers another space's process, so only its time can say (#2986)."""
    if host.lower() != socket.gethostname().lower():
        return False
    if not same_pid_space(pid_space):
        return predates_pid_space(alive_at)
    if not pid_alive(pid):
        return True
    recorded = recorded_instant(alive_at)
    started = process_started(pid) if recorded is not None else None
    return recorded is not None and started is not None and started > recorded
