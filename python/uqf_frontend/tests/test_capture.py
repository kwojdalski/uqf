"""Usage capture (FE-13).

The ordering property here is the one that cannot be fixed after the fact: if
a write fails and the watermark advances anyway, those rows are pruned from q
and gone. So the retry behaviour gets explicit tests.
"""

from __future__ import annotations

import datetime as dt
import json

from uqf_frontend import ops
from uqf_frontend.capture import JsonlSink, MemorySink, UsageCapture
from uqf_frontend.fleet import FakeFleet


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
