"""`uqs list backfills`: what each attached backfill did, from the files it leaves (#1454).

An attached backfill (uqs.attach.backfill_run) runs as a process of its own, logs to
`<status dir>/attached/<attachment>-<destination id>/logs/` and holds an owner record in
the attachment's folder while it lives. Reading those is enough to say which attachment
and destination a run was for, which worker, whether it is still running, how far it got,
and - the distinction this exists to keep - whether its rows are only WRITTEN or also
QUERYABLE, with what to do when they are not. Nothing is asked of any process, and nothing is
written; `stack` may not import `attach`, so the owner record's location is read through
attach_state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uqs.liveness import on_this_host, pid_alive
from uqs.paths import UqsPaths
from uqs.stack import attach_state, runs

#: Where, under the status directory, attached runs keep their state.
STATE_SUBDIR = "attached"
#: The folder of an attachment where a running backfill leaves its owner record.
OWNER_SUBDIR = "backfills"
RESULT_PREFIX = "RESULT "


def result_line(log: Path) -> dict[str, Any]:
    """The runner's closing `RESULT {...}` line in `log`, or {} when it wrote none."""
    try:
        lines = [
            x for x in log.read_text(errors="replace").splitlines() if x.startswith(RESULT_PREFIX)
        ]
    except OSError:
        return {}
    try:
        return json.loads(lines[-1][len(RESULT_PREFIX) :]) if lines else {}
    except ValueError:
        return {}


def _owners(attachment: str) -> list[dict[str, Any]]:
    """The live owner records of `attachment`'s backfills."""
    try:
        folder = attach_state.directory(attachment) / OWNER_SUBDIR
    except Exception:  # noqa: BLE001 - a name that is not an attachment's has no records
        return []
    found = []
    for path in sorted(folder.glob("*.owner.json")) if folder.is_dir() else []:
        try:
            record = json.loads(path.read_text())
            alive = pid_alive(int(record["pid"])) if on_this_host(str(record["host"])) else True
        except OSError, ValueError, KeyError, TypeError:
            continue
        if alive:
            found.append(record)
    return found


def _latest_log(state: Path) -> Path | None:
    logs = sorted((state / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def _row(state: Path) -> dict[str, str]:
    attachment, _, identity = state.name.rpartition("-")
    log = _latest_log(state)
    result = result_line(log) if log else {}
    owners = [o for o in _owners(attachment) if str(o.get("destination")) == identity]
    row = {
        "attachment": attachment,
        "destination": identity,
        "worker": str(result.get("worker") or (owners[0].get("worker") if owners else "")),
        "state": "running" if owners else str(result.get("state", "no outcome")),
        "outcome": "" if owners else str(result.get("outcome", "")),
        "queryable": "" if owners or not result else ("yes" if result.get("queryable") else "no"),
        "log": str(log or ""),
        "detail": "",
    }
    if owners:
        row["detail"] = f"pid {owners[0].get('pid')} since {owners[0].get('started')}"
    elif result.get("outcome") == "written_not_queryable":
        row["detail"] = (
            "written and covered, NOT queryable - re-run the same command: nothing is "
            "rewritten, the readers are asked again. " + "; ".join(result.get("failed_readers", []))
        )
    elif result.get("error"):
        row["detail"] = str(result["error"])
    elif result.get("outcome") in ("failed", "partial"):
        row["detail"] = "see the log; windows already covered are skipped on a re-run"
    elif not result:
        row["detail"] = (
            "no outcome recorded - the run died or was killed; re-run the same command, "
            "covered windows are skipped"
        )
    return row


def rows(paths: UqsPaths, base_port: int | None = None) -> list[dict[str, str]]:
    """One row per attached destination that has run (`uqs list backfills`): in name order.
    `base_port` is the listing's signature; the state is on disk and has none."""
    base = runs.status_dir(paths) / STATE_SUBDIR
    if not base.is_dir():
        return []
    return [_row(d) for d in sorted(base.iterdir()) if d.is_dir() and "-" in d.name]
