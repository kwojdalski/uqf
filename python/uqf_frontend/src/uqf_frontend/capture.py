"""Usage-log capture (F-13).

``.usage.usage`` rows are flushed to disk and dropped from memory after
``.usage.flushtime`` - **three hours** by the vendored default, not the one
day the requirements state. So any frontend view of error or latency history
longer than that window is not a query against a live process; it is a
capture pipeline that has to run *before* the rows are pruned.

Getting this wrong is unrecoverable in a way most bugs are not: if capture is
added later, the history in between is simply gone. That is why B2 builds it
rather than deferring it with the views that read it.

The design is deliberately dull: a watermark of the newest row captured, a
fetch of everything strictly newer, an append to a sink, then the watermark
advances - and only after the append succeeds, so a failed write is retried
rather than skipped. That ordering is the same publish-before-checkpoint rule
the ETL framework states in E-05.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from uqf_frontend import ops
from uqf_frontend.fleet import Fleet

#: Rows to pull per process per pass. A bound rather than "everything",
#: because an unbounded fetch against a busy process is how a capture job
#: becomes the reason the process is slow.
DEFAULT_BATCH = 50_000


@runtime_checkable
class Sink(Protocol):
    """Where captured rows go. Append-only by contract."""

    def append(self, process: str, rows: list[dict[str, Any]]) -> None: ...


class JsonlSink:
    """Append rows as JSON Lines, one file per process.

    JSONL because it is append-only by nature: a partial write damages one
    line rather than the file, and a reader can skip it.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def append(self, process: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"usage-{process}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")


class MemorySink:
    """A :class:`Sink` for tests."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.fail_on: set[str] = set()

    def append(self, process: str, rows: list[dict[str, Any]]) -> None:
        if process in self.fail_on:
            raise OSError(f"simulated sink failure for {process}")
        self.rows.setdefault(process, []).extend(rows)


@dataclass
class CaptureResult:
    """What one pass did, per process."""

    captured: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.captured.values())


class UsageCapture:
    """Captures each process's usage log into a sink before it is pruned."""

    #: Epoch for a process never captured before. Deliberately far in the
    #: past so the first pass takes whatever is currently in memory.
    EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

    def __init__(self, fleet: Fleet, sink: Sink, *, batch: int = DEFAULT_BATCH) -> None:
        self._fleet = fleet
        self._sink = sink
        self._batch = batch
        self._watermarks: dict[str, dt.datetime] = {}

    def watermark(self, process: str) -> dt.datetime:
        return self._watermarks.get(process, self.EPOCH)

    def capture_once(self) -> CaptureResult:
        """One pass over the fleet. Safe to call repeatedly.

        A process whose sink write fails keeps its old watermark, so the next
        pass retries the same rows rather than losing them. A row exactly at
        the watermark is never re-fetched, because ``USAGE_SINCE`` filters
        strictly greater - so a successful pass is idempotent.
        """
        result = CaptureResult()

        for process in self._fleet.processes:
            proc_result = self._fleet.one(
                process, ops.USAGE_SINCE, self.watermark(process), self._batch
            )
            if not proc_result.ok:
                result.failed[proc_result.process] = proc_result.error or "unreachable"
                continue
            rows = ops._as_rows(proc_result.value)
            if not rows:
                result.captured[proc_result.process] = 0
                continue
            try:
                self._sink.append(proc_result.process, rows)
            except Exception as exc:
                result.failed[proc_result.process] = f"sink write failed: {exc}"
                continue
            # Advance only after a successful write (E-05's ordering).
            newest = max(_aware(r["time"]) for r in rows if r.get("time") is not None)
            self._watermarks[proc_result.process] = newest
            result.captured[proc_result.process] = len(rows)

        return result


def _aware(value: Any) -> dt.datetime:
    """q stores UTC and kola returns naive datetimes, so label rather than
    convert (E-08/R9.1).
    """
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    return UsageCapture.EPOCH
