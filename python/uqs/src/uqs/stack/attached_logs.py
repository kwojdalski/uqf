"""`uqs logs --attach NAME`: the logs an attached backfill or receiver writes (#1725).

An attached run (uqs.attach.backfill_run, backfill_remote) is a q process of its own, not a
TorQ process: it registers nothing, and logs to

    <status dir>/attached/<attachment>-<destination id>/logs/<worker>-<stamp>.log

in `.qetl.log`'s four fields, `time|level|id|message`, among lines that are not log lines
at all - a q error, the closing `RESULT {...}`. This finds the run a command line means
and reads it into the records uqs.stack.logs prints, so it reads like any other log.
An attachment has one state directory per destination (a checked one, a forced one), and
the runs of all of them are looked through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from uqs.paths import UqsError, UqsPaths
from uqs.stack import logs, runs
from uqs.stack.backfill_status import STATE_SUBDIR

#: <attachment>-<12 hex digits>: a destination's state directory (uqs.attach.backfill.identity_of)
_STATE = re.compile(r"^(?P<attachment>.+)-(?P<identity>[0-9a-f]{12})$")
#: <worker>-<%Y%m%dT%H%M%S%f>.log: a run's log (uqs.attach.backfill_run, backfill_remote)
_RUN = re.compile(r"^(?P<worker>.+)-(?P<stamp>\d{8}T\d{12})\.log$")
#: `.qetl.log`'s line outside TorQ: time|level|id|message (src/etl/core/log.q)
_FIELDS = ("time", "loglevel", "id", "message")
#: What a receiver's run is named after: `<dataset>.receiver`.
RECEIVER_SUFFIX = ".receiver"


@dataclass(frozen=True)
class RunLog:
    path: Path
    worker: str
    stamp: str
    #: the destination's identity, from the state directory's name
    destination: str


def run_logs(paths: UqsPaths, attachment: str) -> list[RunLog]:
    """Every run log of `attachment`, across its destinations, oldest first."""
    root = runs.status_dir(paths) / STATE_SUBDIR
    found = []
    for state in sorted(root.iterdir()) if root.is_dir() else []:
        named = _STATE.match(state.name)
        if not named or named["attachment"] != attachment:
            continue
        for log in (state / "logs").glob("*.log"):
            if run := _RUN.match(log.name):
                found.append(RunLog(log, run["worker"], run["stamp"], named["identity"]))
    return sorted(found, key=lambda r: (r.stamp, r.worker))


def select(
    paths: UqsPaths, attachment: str, worker: str | None = None, run: str | None = None
) -> RunLog:
    """The run a command line means: the latest of `worker` (the only worker, when there is
    one), or with `run` the one whose log is named so - its file name, or a unique prefix of
    its stamp. Refused, naming what there is, when nothing or more than one thing matches."""
    every = run_logs(paths, attachment)
    if not every:
        raise UqsError(
            f"no attached run has logged for attachment {attachment} under "
            f"{runs.status_dir(paths) / STATE_SUBDIR} - has `uqs job backfill --attach "
            f"{attachment}` run on this machine?"
        )
    workers = sorted({r.worker for r in every})
    if worker is not None:
        mine = [r for r in every if r.worker == worker]
        if not mine:
            raise UqsError(
                f"no run of {worker} for attachment {attachment}: it has logs of "
                f"{', '.join(workers)}"
            )
    elif len(workers) > 1 and run is None:
        raise UqsError(
            f"attachment {attachment} has logs of {', '.join(workers)}: say which with --worker"
        )
    else:
        mine = every
    if run is None:
        return mine[-1]
    chosen = [r for r in mine if r.path.name == run or r.stamp.startswith(run)]
    if len(chosen) != 1:
        recent = ", ".join(r.path.name for r in mine[-5:])
        raise UqsError(
            f"--run {run} names {'no' if not chosen else 'more than one'} run of "
            f"{worker or attachment}: the latest are {recent}"
        )
    return chosen[0]


def parser(log: RunLog):
    """A line of `log` -> the record uqs.stack.logs prints, never None for a non-blank line.

    A four-field line is a log line, shown as the worker's with the id that logged it (the
    worker, attached_backfill, qetl.io.remote...). Anything else - a q error, the RESULT
    line, a refusal printed before logging began - is kept whole, at INFO, and marked
    `unleveled` so --level never hides it: it is often the one line that says what happened.
    """
    kind = "receiver" if log.worker.endswith(RECEIVER_SUFFIX) else "backfill"

    def parse(line: str) -> dict[str, str] | None:
        text = line.rstrip("\n")
        if not text.strip():
            return None
        parts = text.split("|", len(_FIELDS) - 1)
        if len(parts) == len(_FIELDS) and "D" in parts[0]:
            rec = dict(zip(_FIELDS, parts, strict=True))
        else:
            rec = {"time": "", "loglevel": "INFO", "id": "", "message": text, "unleveled": "1"}
        return rec | {"procname": log.worker, "proctype": rec["id"] or kind}

    return parse


def _history(chosen: RunLog, lines: int) -> list[dict[str, str]]:
    """The last `lines` lines of a run's log as records, in the file's own order and
    unfiltered: emit applies --level, and never to a line with none."""
    parse = parser(chosen)
    tail = chosen.path.read_text(errors="replace").splitlines()[-lines:] if lines else []
    return [rec for line in tail if (rec := parse(line)) is not None]


def recent(
    paths: UqsPaths,
    attachment: str,
    *,
    worker: str | None = None,
    run: str | None = None,
    lines: int = 20,
    min_level: str | None = None,
) -> list[dict[str, str]]:
    """The lines `show` prints without `follow`, as records - `uqs logs --attach --json`."""
    logs.min_level_name(min_level)
    return [
        rec
        for rec in _history(select(paths, attachment, worker, run), lines)
        if rec.get("unleveled")
        or logs._passes_level(logs._LOGURU_LEVEL.get(rec["loglevel"], "INFO"), min_level)
    ]


def show(
    paths: UqsPaths,
    attachment: str,
    *,
    worker: str | None = None,
    run: str | None = None,
    follow: bool = False,
    lines: int = 20,
    min_level: str | None = None,
) -> None:
    """`uqs logs --attach NAME`: the run `select` names, printed as a process's log is -
    its last *lines* lines, then with `follow` every line appended until Ctrl-C."""
    logs.min_level_name(min_level)
    chosen = select(paths, attachment, worker, run)
    parse = parser(chosen)
    files = [chosen.path]
    history = _history(chosen, lines)
    if follow:
        logs._follow(files, min_level, history=lambda: history, parse=parse)
        return
    sink = logs.log_sink()
    for rec in history:
        logs.emit(sink, rec, min_level)
