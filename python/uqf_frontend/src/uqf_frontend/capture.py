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

    def last_watermark(self, process: str) -> int | None:
        """The ``time_ns`` of the newest row already appended for `process`,
        or None - what a new :class:`UsageCapture` resumes from (#2413)."""
        ...


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
        torn = _ends_torn(path)
        with path.open("a", encoding="utf-8") as fh:
            if torn:
                # End the torn line first: last_watermark skipped it so its
                # row is fetched again, and written straight after the
                # partial bytes that row would be lost inside them (#2584).
                fh.write("\n")
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")

    def last_watermark(self, process: str) -> int | None:
        """The last complete line's ``time_ns``: ``USAGE_SINCE`` returns rows
        in time order, so the file's last row is the newest captured. Read
        from the end, so an archive of any size costs one chunk; a torn last
        line - a write cut short - is skipped, and its row fetched again."""
        path = self._dir / f"usage-{process}.jsonl"
        if not path.is_file():
            return None
        with path.open("rb") as fh:
            end = fh.seek(0, 2)
            tail = b""
            pos = end
            while pos > 0:
                step = min(_TAIL_CHUNK, pos)
                pos -= step
                fh.seek(pos)
                tail = fh.read(step) + tail
                lines = tail.split(b"\n")
                # the first piece may be the end of a line that starts earlier
                for line in reversed(lines if pos == 0 else lines[1:]):
                    stamp = _time_ns(line)
                    if stamp is not None:
                        return stamp
                tail = lines[0] if pos > 0 else b""
        return None


def _ends_torn(path: Path) -> bool:
    """Does *path* end part-way through a line - a write cut short?"""
    try:
        with path.open("rb") as fh:
            if fh.seek(0, 2) == 0:
                return False
            fh.seek(-1, 2)
            return fh.read(1) != b"\n"
    except FileNotFoundError:
        return False


#: How much of a JSONL archive's end `last_watermark` reads at a time.
_TAIL_CHUNK = 64 * 1024


def _time_ns(line: bytes) -> int | None:
    """One archived row's ``time_ns``, or None for a torn or foreign line."""
    try:
        stamp = json.loads(line).get("time_ns")
        return None if stamp is None else int(stamp)
    except ValueError, TypeError, AttributeError:
        return None


class MemorySink:
    """A :class:`Sink` for tests."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.fail_on: set[str] = set()

    def append(self, process: str, rows: list[dict[str, Any]]) -> None:
        if process in self.fail_on:
            raise OSError(f"simulated sink failure for {process}")
        self.rows.setdefault(process, []).extend(rows)

    def last_watermark(self, process: str) -> int | None:
        stamps = [int(r["time_ns"]) for r in self.rows.get(process, []) if "time_ns" in r]
        return max(stamps, default=None)


@dataclass
class CaptureResult:
    """What one pass did, per process."""

    captured: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.captured.values())


class UsageCapture:
    """Captures each process's usage log into a sink before it is pruned.

    The watermark is a q LONG - the newest captured row's ``time`` as
    nanoseconds since 2000.01.01, which is what ``"j"$`` makes of a timestamp
    - and it is held, compared and sent as that long from end to end. It is
    never a ``datetime``: kola hands a timestamp column back as a Python
    ``datetime``, which keeps microseconds, and ``.usage.usage.time`` is
    ``.z.p`` with a nanosecond tail. A watermark that went through
    ``datetime`` was up to 999 ns before the row it came from, so that row
    was "newer than the watermark" again on every sweep and was appended to
    the sink once per interval (#1316). ``USAGE_SINCE`` returns the long per
    row as ``time_ns`` so nothing here has to reconstruct it.
    """

    #: Epoch for a process never captured before: 1970.01.01 as a q long
    #: (nanoseconds since 2000.01.01, so negative). Deliberately far in the
    #: past so the first pass takes whatever is currently in memory.
    EPOCH = -946_684_800_000_000_000

    def __init__(self, fleet: Fleet, sink: Sink, *, batch: int = DEFAULT_BATCH) -> None:
        self._fleet = fleet
        self._sink = sink
        self._batch = batch
        self._watermarks: dict[str, int] = {}

    def watermark(self, process: str) -> int:
        """The newest row captured for `process`: this object's own, or - the
        first time it is asked, as after an API restart - the sink's, so a
        restart does not append every row still in memory again (#2413)."""
        if process not in self._watermarks:
            recovered = self._sink.last_watermark(process)
            self._watermarks[process] = self.EPOCH if recovered is None else recovered
        return self._watermarks[process]

    def capture_once(self) -> CaptureResult:
        """One pass over the fleet. Safe to call repeatedly.

        A process whose sink write fails keeps its old watermark, so the next
        pass retries the same rows rather than losing them. A row exactly at
        the watermark is never re-fetched, because ``USAGE_SINCE`` filters
        strictly greater on the same long the watermark was taken from - so a
        successful pass is idempotent, and a pass after a successful pass
        with nothing new on the process captures nothing.
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
            # From time_ns, the long q compared against - never from `time`,
            # which lost its nanoseconds on the way into Python.
            newest = max(
                (int(r["time_ns"]) for r in rows if r.get("time_ns") is not None),
                default=self.watermark(proc_result.process),
            )
            self._watermarks[proc_result.process] = newest
            result.captured[proc_result.process] = len(rows)

        return result


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
