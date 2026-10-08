"""What a streaming job's batches did: `uqs summary`'s Batches column (#832).

A batch handler that throws is trapped by TorQ, so the process stays up and
its heartbeat stays green while it drops every batch. Status (a pid) and
Heartbeat (TorQ's) cannot see that; this column can. Each running job writes
`stream_health_<job>.txt` in the status directory on every uptime beat -
src/etl/core/stream_health.q, whose keys this reads as literals - saying
whether a batch failed since the beat before.

A file is believed only for the process that wrote it: its `process` names
the row and its `pid` must be that row's, so a file left by an earlier run
of the job never marks the current one.
"""

from __future__ import annotations

import json

from uqs.paths import UqsPaths
from uqs.stack import runs

#: The files' names: stream_health_<job>.txt.
PREFIX = "stream_health_"


def read(paths: UqsPaths) -> dict[str, dict]:
    """Every job's latest record, by the process that wrote it."""
    found: dict[str, dict] = {}
    for path in sorted(runs.status_dir(paths).glob(f"{PREFIX}*.txt")):
        try:
            record = json.loads(path.read_text())
        except OSError, ValueError:
            continue
        if isinstance(record, dict) and record.get("process"):
            found[str(record["process"])] = record
    return found


def cell(record: dict | None, pid: str) -> str:
    """The Batches cell for a running process: `failing` with how many of its
    batches have failed, `ok`, or `-` for a process that runs no job (or
    whose record is another run's). Short, so `--columns status` still fits
    eighty columns: the note under the table says when, and why."""
    if record is None or str(int(record.get("pid", -1))) != pid:
        return "-"
    if not record.get("failing"):
        return "ok"
    return f"failing ({int(record['failed'])})"


def attach_batches_column(rows: list[dict[str, str]], paths: UqsPaths) -> list[dict]:
    """Fill each row's Batches cell, in place; return the failing records."""
    records = read(paths)
    failing = []
    for row in rows:
        record = records.get(row["Process"]) if row["Status"] == "up" else None
        row["Batches"] = cell(record, row.get("PID", ""))
        if row["Batches"].startswith("failing") and record is not None:
            failing.append(record)
    return failing
