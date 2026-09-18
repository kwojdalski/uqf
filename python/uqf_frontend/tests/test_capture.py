"""Usage capture (FE-13).

The ordering property here is the one that cannot be fixed after the fact: if
a write fails and the watermark advances anyway, those rows are pruned from q
and gone. So the retry behaviour gets explicit tests.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

from uqf_frontend import ops
from uqf_frontend.capture import (
    CaptureResult,
    CaptureScheduler,
    JsonlSink,
    MemorySink,
    UsageCapture,
)
from uqf_frontend.fleet import FakeFleet, ProcessResult


def row(hour: int, status: str = "b") -> dict:
    return {"time": dt.datetime(2026, 9, 15, hour), "status": status, "id": hour}


def test_first_pass_captures_everything_in_memory():
    fleet = FakeFleet({"rdb1": [row(10), row(11)]})
    sink = MemorySink()
    result = UsageCapture(fleet, sink).capture_once()
    assert result.captured == {"rdb1": 2}
    assert len(sink.rows["rdb1"]) == 2


def test_watermark_advances_to_the_newest_row_captured():
    fleet = FakeFleet({"rdb1": [row(10), row(12)]})
    cap = UsageCapture(fleet, MemorySink())
    cap.capture_once()
    assert cap.watermark("rdb1") == dt.datetime(2026, 9, 15, 12, tzinfo=dt.UTC)


def test_the_watermark_is_passed_to_q_so_filtering_happens_there():
    """Filtering in q rather than in Python is what keeps the fetch bounded."""
    fleet = FakeFleet({"rdb1": [row(10)]})
    cap = UsageCapture(fleet, MemorySink())
    cap.capture_once()
    process, program, args = fleet.calls[-1]
    assert program == ops.USAGE_SINCE
    assert args[0] == UsageCapture.EPOCH
    cap.capture_once()
    assert fleet.calls[-1][2][0] == dt.datetime(2026, 9, 15, 10, tzinfo=dt.UTC)


def test_a_second_pass_with_no_new_rows_captures_nothing():
    fleet = FakeFleet({"rdb1": lambda since, lim: [] if since > UsageCapture.EPOCH else [row(10)]})
    cap = UsageCapture(fleet, MemorySink())
    assert cap.capture_once().captured == {"rdb1": 1}
    assert cap.capture_once().captured == {"rdb1": 0}


def test_a_failed_sink_write_does_not_advance_the_watermark():
    """The property that makes capture retry-safe rather than lossy. If the
    watermark advanced on a failed write, those rows would be pruned from q
    before the next pass and lost permanently.
    """
    fleet = FakeFleet({"rdb1": [row(10)]})
    sink = MemorySink()
    sink.fail_on = {"rdb1"}
    cap = UsageCapture(fleet, sink)
    result = cap.capture_once()
    assert "rdb1" in result.failed
    assert cap.watermark("rdb1") == UsageCapture.EPOCH, "watermark must not advance"


def test_the_retry_after_a_failed_write_succeeds():
    fleet = FakeFleet({"rdb1": [row(10)]})
    sink = MemorySink()
    sink.fail_on = {"rdb1"}
    cap = UsageCapture(fleet, sink)
    cap.capture_once()
    sink.fail_on = set()
    assert cap.capture_once().captured == {"rdb1": 1}
    assert len(sink.rows["rdb1"]) == 1, "the row is captured exactly once across both passes"


def test_an_unreachable_process_is_reported_and_others_still_captured():
    fleet = FakeFleet({"rdb1": [row(10)], "hdb1": ConnectionError("refused")})
    result = UsageCapture(fleet, MemorySink()).capture_once()
    assert result.captured == {"rdb1": 1}
    assert "hdb1" in result.failed


def test_watermarks_are_independent_per_process():
    fleet = FakeFleet({"a": [row(10)], "b": [row(20)]})
    cap = UsageCapture(fleet, MemorySink())
    cap.capture_once()
    assert cap.watermark("a") != cap.watermark("b")


def test_batch_size_is_passed_to_q_as_a_bound():
    fleet = FakeFleet({"rdb1": []})
    UsageCapture(fleet, MemorySink(), batch=123).capture_once()
    assert fleet.calls[-1][2][1] == 123


def test_jsonl_sink_appends_one_line_per_row(tmp_path):
    sink = JsonlSink(tmp_path)
    sink.append("rdb1", [row(10), row(11)])
    sink.append("rdb1", [row(12)])
    lines = (tmp_path / "usage-rdb1.jsonl").read_text().strip().split("\n")
    assert len(lines) == 3
    assert json.loads(lines[0])["id"] == 10


def test_jsonl_sink_serialises_timestamps_rather_than_failing():
    """A datetime is not JSON-serialisable by default; the sink must not throw
    on the one column every row has.
    """
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        JsonlSink(Path(d)).append("p", [row(10)])
        content = (Path(d) / "usage-p.jsonl").read_text()
        assert "2026-09-15" in content


def test_jsonl_sink_ignores_an_empty_batch(tmp_path):
    JsonlSink(tmp_path).append("rdb1", [])
    assert not list(tmp_path.glob("*.jsonl"))


def test_capture_result_totals_across_processes():
    fleet = FakeFleet({"a": [row(10)], "b": [row(11), row(12)]})
    assert UsageCapture(fleet, MemorySink()).capture_once().total == 3


# --- the scheduler: B2's gate ------------------------------------------
#
# "Usage rows are captured into durable storage before flushtime prunes
# them, verified by advancing a fake clock past the window." The clock is
# fake in both directions here: the scheduler's sleep is injected, and the
# q side is a FakeFleet that PRUNES, so a sweep that runs too late finds
# nothing - which is exactly how the real failure presents.


class PruningFleet:
    """A fleet whose rows vanish once they are older than the flush window.

    Stands in for `.usage.usage`, which drops rows from memory at
    `.usage.flushtime`. The point is that a capture which runs late does not
    error - it succeeds and captures nothing, and the history is simply
    gone. A test against a fleet that never prunes cannot tell a working
    capture from an absent one.
    """

    def __init__(self, rows: list[dict], flush_window: dt.timedelta) -> None:
        self._rows = rows
        self._window = flush_window
        self.now = dt.datetime(2026, 9, 15, 12, tzinfo=dt.UTC)

    @property
    def processes(self) -> tuple[str, ...]:
        return ("rdb1",)

    def one(self, process: str, program: str, *args: Any) -> ProcessResult:
        since, limit = args
        live = [
            r
            for r in self._rows
            if self.now - r["time"].replace(tzinfo=dt.UTC) <= self._window
            and r["time"].replace(tzinfo=dt.UTC) > since
        ]
        return ProcessResult(process=process, ok=True, value=live[:limit], error=None)

    def per_process(self, program: str, *args: Any) -> list[ProcessResult]:
        return [self.one(name, program, *args) for name in self.processes]

    def probe(self, host: str, port: int, program: str, *args: Any) -> ProcessResult:
        raise NotImplementedError("capture never probes an arbitrary address")


def test_capture_runs_before_the_flush_window_prunes():
    """The gate. One sweep per interval, an interval well inside the window,
    and the rows survive in the sink after the clock has moved past the
    point where q would have dropped them.
    """
    window = dt.timedelta(hours=3)
    rows = [row(9), row(10), row(11)]
    fleet = PruningFleet(rows, window)
    sink = MemorySink()
    scheduler = CaptureScheduler(
        UsageCapture(fleet, sink), interval_seconds=1800, sleep=lambda _: None
    )

    # A sweep at 12:00 takes everything still in memory (09:00 onwards).
    scheduler.run_once()
    captured_early = len(sink.rows["rdb1"])
    assert captured_early == 3

    # Now advance well past the window. q has pruned all three.
    fleet.now = dt.datetime(2026, 9, 15, 18, tzinfo=dt.UTC)
    assert fleet.one("rdb1", ops.USAGE_SINCE, UsageCapture.EPOCH, 10).value == []

    # A later sweep adds nothing, and - the point - loses nothing either.
    scheduler.run_once()
    assert len(sink.rows["rdb1"]) == captured_early, (
        "rows captured before the window closed must still be in the sink"
    )


def test_without_a_scheduler_the_history_is_gone():
    """The negative of the gate, so it is testing something.

    The same fleet and the same clock, with no sweep until after the window:
    the capture succeeds and captures nothing. This is what the frontend did
    before the scheduler existed.
    """
    fleet = PruningFleet([row(9), row(10), row(11)], dt.timedelta(hours=3))
    sink = MemorySink()
    fleet.now = dt.datetime(2026, 9, 15, 18, tzinfo=dt.UTC)
    result = UsageCapture(fleet, sink).capture_once()
    assert result.captured == {"rdb1": 0}
    assert sink.rows == {}


def test_a_sweep_that_throws_does_not_kill_the_loop():
    """A capture that stopped silently is the failure this class exists to
    prevent, so a throwing sweep is reported and the next one still runs.
    """

    class Exploding(UsageCapture):
        def __init__(self) -> None:
            super().__init__(FakeFleet({}), MemorySink())
            self.calls = 0

        def capture_once(self) -> CaptureResult:
            self.calls += 1
            raise RuntimeError("gateway went away")

    capture = Exploding()
    scheduler = CaptureScheduler(capture, interval_seconds=1, sleep=lambda _: None)
    first = scheduler.run_once()
    second = scheduler.run_once()
    assert capture.calls == 2
    assert "gateway went away" in first.failed["*"]
    assert "gateway went away" in second.failed["*"]


def test_the_scheduler_sweeps_on_a_timer_and_stops():
    seen: list[int] = []
    fleet = FakeFleet({"rdb1": [row(10)]})
    scheduler = CaptureScheduler(
        UsageCapture(fleet, MemorySink()),
        interval_seconds=0.01,
        on_result=lambda r: seen.append(r.total),
    )
    scheduler.start()
    deadline = time.monotonic() + 5
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    scheduler.stop()
    assert not scheduler.running
    assert len(seen) >= 2, "the scheduler swept more than once"


def test_a_non_positive_interval_is_refused():
    """An interval of zero is a busy loop against the whole fleet."""
    import pytest

    with pytest.raises(ValueError, match="positive"):
        CaptureScheduler(UsageCapture(FakeFleet({}), MemorySink()), interval_seconds=0)
