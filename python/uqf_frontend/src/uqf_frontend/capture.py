"""Usage-log capture.

``.usage.usage`` rows are flushed to disk and dropped from memory after
``.usage.flushtime`` - **one day** in a standard TorQ stack. So any frontend
view of error or latency history longer than that window is not a query
against a live process; it is a capture pipeline that has to run *before*
the rows are pruned.

The number is worth getting right because it was got wrong here twice, in
both directions. ``code/handlers/logusage.q`` reads
``@[value;`flushtime;0D03]`` - three hours - and that line is what earlier
comments in this package quoted. But it is a FALLBACK for a value already
defined, and ``config/settings/default.q`` defines ``flushtime:1D00``
first, so the fallback never fires. Measured on three running processes:
one day. Query ``.usage.flushtime`` rather than assuming either figure - a
deployment may override it again.

Getting this wrong is unrecoverable in a way most bugs are not: if capture is
added later, the history in between is simply gone. That is why B2 builds it
rather than deferring it with the views that read it.

The design is deliberately dull: a watermark of the newest row captured, a
fetch of everything strictly newer, an append to a sink, then the watermark
advances - and only after the append succeeds, so a failed write is retried
rather than skipped. That ordering is the same publish-before-checkpoint rule
the ETL framework follows for cursors.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from uqf_frontend import ops
from uqf_frontend.config import DEFAULT_CAPTURE_INTERVAL
from uqf_frontend.fleet import Fleet

log = logging.getLogger(__name__)

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
            # Advance only after a successful write (publish, then acknowledge).
            newest = max(_aware(r["time"]) for r in rows if r.get("time") is not None)
            self._watermarks[proc_result.process] = newest
            result.captured[proc_result.process] = len(rows)

        return result


def _aware(value: Any) -> dt.datetime:
    """q stores UTC and kola returns naive datetimes, so label rather than
    convert.
    """
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    return UsageCapture.EPOCH


class CaptureScheduler:
    """Runs a :class:`UsageCapture` on a fixed interval, in a daemon thread.

    WHY THIS EXISTS AT ALL. ``capture_once`` was written, tested and called
    by nothing. Capture is the one requirement whose failure is invisible
    while it is failing - a view of the last day looks exactly like
    a view of everything, right up to the day someone asks about yesterday -
    which is why the requirements call it "the trap" and why the pipeline
    needs a thing that actually runs it rather than a README line saying it
    could be run.

    A THREAD, not an asyncio task: ``capture_once`` is synchronous and
    blocking (kola IPC, one round trip per process), so on the event loop it
    would stall every request for the length of a fleet sweep. It is a
    daemon thread so that it can never hold the process open at shutdown.

    THE CLOCK AND THE SLEEP ARE INJECTABLE, which is what makes the B2 gate
    runnable: a test advances a fake clock past the flush window and asserts
    the rows were captured before they would have been pruned, with no real
    time passing.
    """

    def __init__(
        self,
        capture: UsageCapture,
        *,
        interval_seconds: float = DEFAULT_CAPTURE_INTERVAL,
        sleep: Callable[[float], None] | None = None,
        on_result: Callable[[CaptureResult], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._capture = capture
        self._interval = interval_seconds
        self._sleep = sleep or time.sleep
        self._on_result = on_result
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> CaptureResult:
        """One pass, with failures reported rather than raised.

        A sweep that throws must not kill the loop: the next interval is the
        retry, and a capture that stopped silently is the failure mode this
        class exists to prevent.
        """
        try:
            result = self._capture.capture_once()
        except Exception as exc:  # noqa: BLE001 - a sweep must never kill the loop
            log.warning("usage capture failed: %s", exc)
            result = CaptureResult()
            result.failed["*"] = str(exc)
        if self._on_result is not None:
            self._on_result(result)
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            # Event.wait, not sleep: a stop during the interval is acted on
            # at once rather than after up to a full period.
            if self._stop.wait(self._interval):
                return

    def start(self) -> None:
        """Begin capturing. Captures immediately, then every interval - the
        first sweep is the one that matters most after a restart, since
        whatever accumulated while the API was down is still within the
        window only for a while.
        """
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="uqf-usage-capture", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop and wait for the current sweep to finish."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None
