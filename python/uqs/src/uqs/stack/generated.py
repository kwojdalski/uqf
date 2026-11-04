"""How the generated configuration is written, and who may write it (#2028).

Every start, stop and restart bootstraps: it rewrites process.csv, setenv.sh
and the rest of the generated files under $TORQDATA, then runs torq.sh. Two
things went wrong with that while anything else was reading:

- each file was truncated and then written, so a reader in between - the
  frontend's Processes view, or the torq.sh of a second lifecycle command,
  which awks process.csv several times per process - saw a prefix of it: a
  short fleet, with nothing to say it was short. torq.sh treats a process
  missing from a torn file as `unavailable processname` and exits 0;
- nothing stopped two lifecycle commands - two browser tabs, or the browser
  and a terminal - from interleaving their bootstraps and torq.sh runs.

So each file is replaced whole (``write_text``: a temporary file in the same
directory, then one rename), and a lifecycle command holds the stack's lock
(``lifecycle_lock``) from its bootstrap to the end of its torq.sh. The lock is
an ``flock`` on a file under $TORQDATA, so `uqs` and the frontend serialise
against each other, and the kernel releases it if the holder dies.
"""

from __future__ import annotations

import csv
import fcntl
import io
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from uqs.paths import UqsError, UqsPaths
from uqs.stack.carriable import check_carriable
from uqs.stack.site_fields import process_csv_fields

#: The lock file, beside the generated files it guards.
LOCK_NAME = ".lifecycle.lock"

#: Lock files this thread already holds: a caller that took the lock around a
#: whole action (the frontend's lifecycle route) runs run_torq_sh, which takes
#: it again. flock would refuse the second open file of the same process.
_held = threading.local()


class StackBusy(UqsError):
    """Another lifecycle command holds this stack's lock."""


def write_text(path: Path, text: str) -> None:
    """Replace *path* with *text* in one rename: a reader sees the old file or
    the new one, never a prefix. Keeps the file's permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def lock_path(paths: UqsPaths) -> Path:
    return paths.torqdata / LOCK_NAME


@contextmanager
def lifecycle_lock(paths: UqsPaths, wait: float | None = None) -> Iterator[None]:
    """Hold the stack's lifecycle lock.

    *wait* is seconds to wait for another holder, None to wait as long as it
    takes (a terminal's `uqs start` queues behind the other one), or 0 to
    refuse at once with StackBusy (the frontend's 409). Re-entrant within a
    thread.
    """
    lock = lock_path(paths)
    key = str(lock)
    held: set[str] = getattr(_held, "locks", set())
    if key in held:
        yield
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        if wait is None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            _within(handle, lock, time.monotonic() + wait)
        held.add(key)
        _held.locks = held
        try:
            yield
        finally:
            held.discard(key)
            fcntl.flock(handle, fcntl.LOCK_UN)


def _within(handle, lock: Path, deadline: float) -> None:
    """Take the lock on *handle* by *deadline*, else refuse with StackBusy."""
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise StackBusy(
                    f"another start, stop or restart is running on this stack (it holds "
                    f"{lock}) - wait for it to finish, then try again"
                ) from None
            time.sleep(0.05)


def write_process_csv(paths: UqsPaths, rows: list[dict[str, str]]) -> None:
    """The process.csv torq.sh starts from: `rows`, as effective_process_rows
    composes them.

    Extend (never edit in place) the vendored process.csv with uqf's own extra
    processes (fxfeed1) and any process_overrides.csv fields set via
    set_process_config()/`config set`/uqs_set_config. Each row TorQ leaves to
    its default interpreter - an empty qcmd, or `q` spelled out, as the starter
    pack and the pipeline rows both do - names the configured one: QCMD, or the
    runtime's own (#764). torq.sh prefers a row's qcmd to $QCMD, so a row saying
    `q` ran PATH's q whatever QCMD named, and a site launcher need not fall back
    to $QCMD at all. An operator's override still wins, even one to bare `q`.
    """
    check_carriable(rows)
    f = io.StringIO()
    # torq.sh's own field lookups are a naive awk -F, parse expecting
    # plain \n line endings, like the vendored csv itself - csv module's
    # default \r\n (the "excel" dialect) corrupts the last column's
    # value (a trailing \r glued onto qcmd breaks the qcmd==qcmd header
    # match, which every single process start looks up), producing a
    # bare `print $` awk syntax error for every process - hard-won via
    # `start all` throwing exactly that for every process at once.
    writer = csv.DictWriter(f, fieldnames=process_csv_fields(), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    # Replaced whole: the Processes view and a second command's torq.sh read
    # it while this rewrites it, and a prefix of it is a short fleet (#2028).
    write_text(paths.generated_procs, f.getvalue())
