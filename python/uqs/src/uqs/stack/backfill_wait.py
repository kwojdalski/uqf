"""`uqs backfill --wait`: following a launched backfill to its outcome.

Apart from stack/backfill.py, which launches it, because waiting is the
half that reads status files and the process listing rather than starting
anything (#637, #768).
"""

from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from uqs.paths import UqsPaths
from uqs.stack import alive, runs


def _ns_stamp(value: str) -> datetime:
    """A q timestamp from JSON (nanoseconds, no offset, UTC) as a datetime."""
    stamp, _, fraction = value.partition(".")
    return datetime.fromisoformat(f"{stamp}.{(fraction or '0')[:6].ljust(6, '0')}").replace(
        tzinfo=UTC
    )


#: How long after launch a process with no status for this run is given
#: before its absence means it died: torq.sh returns as soon as it has forked,
#: and the worker writes its first status only once the tree has loaded.
STARTUP_GRACE = timedelta(seconds=30)


def wait_for_outcome(
    paths: UqsPaths,
    procname: str,
    source_version: str,
    range_from: datetime,
    range_to: datetime,
    launched_at: datetime,
    *,
    base_port: int | None = None,
    poll_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    is_up: Callable[[], bool] | None = None,
) -> tuple[str, int, str]:
    """Follow the process's status file to its outcome: (state, exit code, error).

    `uqs backfill` returns once torq.sh has STARTED the process, so its own
    exit code says only that. This waits for what the worker reports - the
    same file the Airflow sensor reads, with the sensor's rules: a file for
    another run (version, range, or written before this launch) is not this
    one's; `idle`/`completed` exit 0, `failed` 1; and a `starting`/`running`
    file whose process is gone on this host is `abandoned`, 1, because it
    will never be written again. The error is the file's own - why a `failed`
    run failed, e.g. which reactions it left owed (#632) - and "" otherwise.

    A process that dies before it writes any status for this run - the tree
    fails to load, the HDB root is missing, the claim is refused - is
    `abandoned` too (#768): once STARTUP_GRACE has passed with no status for
    this run, the process listing is asked whether it is still up, and if it
    is not, the wait ends rather than polling an absent file forever.
    """
    path = runs.status_dir(paths) / f"airflow_status_{procname}.txt"
    if is_up is None:

        def is_up() -> bool:
            return procname in alive.running(paths, base_port)

    def this_run() -> dict | None:
        try:
            status = json.loads(path.read_text())
        except OSError, ValueError:
            return None
        ours = _is_this_run(status, source_version, range_from, range_to, launched_at)
        return status if ours else None

    while True:
        status = this_run()
        if status is None and now() - launched_at >= STARTUP_GRACE and not is_up():
            # It may have written its outcome and exited since the read above.
            status = this_run()
            if status is None:
                return (
                    "abandoned",
                    1,
                    "its process exited without recording an outcome for this run - "
                    f"see `uqs logs {procname}`",
                )
        if status is not None:
            state = str(status["state"])
            if state in ("idle", "completed"):
                return state, 0, ""
            if state == "failed":
                return state, 1, str(status.get("error") or "")
            if str(status.get("host", "")).lower() == socket.gethostname().lower() and not (
                pid_alive(int(status["pid"]))
            ):
                return "abandoned", 1, "its process is gone and it never recorded an outcome"
        sleep(poll_seconds)


def _is_this_run(
    status: dict,
    source_version: str,
    range_from: datetime,
    range_to: datetime,
    launched_at: datetime,
) -> bool:
    try:
        return (
            status["source_version"] == source_version
            and _ns_stamp(status["range_from"]) == range_from.astimezone(UTC)
            and _ns_stamp(status["range_to"]) == range_to.astimezone(UTC)
            and _ns_stamp(status["updated_at"]) >= launched_at.astimezone(UTC)
        )
    except KeyError, ValueError:
        return False


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
