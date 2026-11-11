"""The record of every waived guardrail of an attached backfill (#1829, part of #1824).

A waiver (`--skip-eod-fence --reason TEXT`, #1828) is an operator accepting a risk for one
invocation: it is never saved into the attachment's configuration, so the next backfill
without the flag is fenced again. What is kept is the record of each use - appended, one JSON
object a line, to OVERRIDES_LOG in the destination's state directory,

    <status dir>/attached/<attachment>-<identity>/backfill_overrides.jsonl

beside the run's `logs/`, coverage and run ledger (`UQF_STATUS_DIR` moves the status dir).
The shape is start_overrides.jsonl's (`at`, `override`, `reason`, `command`), with what an
attachment acceptance (attach/overrides.py) also keeps: who (`operator`, `host`), and the
OBSERVED FACTS it was accepted against (`observed`, from each accepted finding). Each entry
also says whether the waiver was `requested` and whether it was `needed` - a finding of it
fired at the admission the run started under - and names the destination and the run (`run`,
the log's name; `log`, its path).

An `override` entry is written and fsynced in backfill_run.launch BEFORE the runner is
started: if it cannot be written, nothing starts. It is never rewritten. What became of the
run is appended separately as an `outcome` entry with the same `run` - `not-started` when the
process could not be started, the runner's result when `--wait` followed it - so a run that
died or stopped part way keeps its override entry, and one with no outcome entry is one
nobody followed (`uqs logs --attachment NAME --worker W` reads its log). A dry run is recorded
too, `mode: dry-run`: it admits under the waiver and runs the lifecycle; `--mode plan` records
nothing, it changes nothing. No credential is ever recorded: the readers' password goes to
the runner by environment, and the command is rebuilt from the run's own arguments, not argv.

`uqs job attachment show NAME` lists the entries of its current destination.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths

log = get_logger()

#: Every waiver used by an attached backfill, one JSON object a line, in the state directory.
OVERRIDES_LOG = "backfill_overrides.jsonl"


def path_for(state_dir: Path) -> Path:
    return state_dir / OVERRIDES_LOG


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def command_of(worker: str, attachment: str, plan: dict[str, Any], mode: str, version: str) -> str:
    """The command, rebuilt from the run's own arguments - never argv, which a caller could
    have given anything - with each waiver as `--override NAME`; the reason is its own field."""
    waivers = "".join(f" --override {o['override']}" for o in plan.get("overrides", []))
    return (
        f"uqs job backfill {worker} --attachment {attachment} --mode {mode} "
        f"--from {plan['range_from']} --to {plan['range_to']} --source-version {version}{waivers}"
    )


def entries(
    plan: dict[str, Any], *, run: str, mode: str, command: str, log_path: Path
) -> list[dict[str, Any]]:
    """One `override` entry per waiver the admission `plan` applied."""
    co = plan["coordination"]
    who, host, at = getpass.getuser(), socket.gethostname(), _now()
    return [
        {
            "at": at,
            "event": "override",
            "run": run,
            "mode": mode,
            "override": o["override"],
            "reason": o["reason"],
            "requested": o["requested"],
            "needed": o["needed"],
            "effect": o["effect"],
            "operator": who,
            "host": host,
            "command": command,
            "job": plan["job"],
            "destination": plan["destination"],
            "range_from": plan["range_from"],
            "range_to": plan["range_to"],
            "bounds": {
                "last_writable_date": co["last_writable_date"],
                "writes_stop_at": co["writes_stop_at"],
                "eod_fence_bypassed": co["eod_fence_bypassed"],
            },
            # what it was accepted against: each finding stays `accepted`, never passing
            "accepted": o["accepted"],
            "observed": [{"component": f["component"], **f["facts"]} for f in o["accepted"]],
            "log": str(log_path),
        }
        for o in plan.get("overrides", [])
    ]


def _append(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for line in lines:
            fh.write(json.dumps(line, sort_keys=True, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def record(state_dir: Path, lines: list[dict[str, Any]]) -> Path | None:
    """Append the override entries, durably; None when there were none. A failure is a
    refusal of the run: the caller has started nothing yet."""
    if not lines:
        return None
    path = path_for(state_dir)
    try:
        _append(path, lines)
    except OSError as exc:
        raise UqsError(
            f"could not record the override in {path} ({exc}): an override that is not recorded "
            "is not used - nothing was started"
        ) from None
    return path


def outcome(path: Path | None, run: str, **facts: Any) -> None:
    """Append what became of `run`; the override entry before it is never touched. Best
    effort: the run has happened, and a failure to append only loses this line."""
    if path is None:
        return
    try:
        _append(path, [{"at": _now(), "event": "outcome", "run": run, **facts}])
    except OSError as exc:
        log.warning("could not append the outcome of {} to {}: {}", run, path, exc)


def read(path: Path) -> list[dict[str, Any]]:
    """Every entry, oldest first; a line that is not one is skipped, never fatal. So is a line
    written before #2122 (`worker`, `range: {from, to}`, `destination.stack_id`): it is not
    read, and stays in the file as it was written (#2723)."""
    if not path.is_file():
        return []
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if _written_before_2122(entry):
            log.warning(
                "{}:{} was written by a uqs before #2122 and is not read; the line stays in "
                "the file, and `uqs job attachment show` lists the others",
                path, n,
            )  # fmt: skip
            continue
        out.append(entry)
    return out


def _written_before_2122(entry: dict[str, Any]) -> bool:
    dest = entry.get("destination")
    return (
        "worker" in entry
        or isinstance(entry.get("range"), dict)
        or (isinstance(dest, dict) and "stack_id" in dest)
    )


def of_attachment(paths: UqsPaths, attachment: str) -> tuple[Path | None, list[dict[str, Any]]]:
    """(the log, its entries) for `attachment`'s current destination; (None, []) when it has
    no destination (no pinned HDB)."""
    from uqs.attach.backfill import state_dir_of

    try:
        path = path_for(state_dir_of(paths, attachment))
    except UqsError:
        return None, []
    return path, read(path)


def lines_of(entries_: list[dict[str, Any]]) -> list[str]:
    """Each entry in a line, as `attachment show` prints them: an override is an accepted
    risk, never a check that passed."""
    out = []
    for e in entries_:
        if e.get("event") == "override":
            needed = "needed" if e.get("needed") else "requested, not needed"
            accepted = ", ".join(
                f"{f.get('component')} {f.get('code')}" for f in e.get("accepted", [])
            )
            out.append(
                f"{e.get('at')}  {e.get('run')}  OVERRIDE {e.get('override')} ({needed}; "
                f"{e.get('mode')}) by {e.get('operator')}@{e.get('host')}; reason: "
                f"{e.get('reason')}" + (f"; accepted: {accepted}" if accepted else "")
            )
        else:
            what = e.get("outcome") or e.get("state") or "unknown"
            out.append(f"{e.get('at')}  {e.get('run')}  outcome {what}")
    return out
