"""Usage capture end to end over real kola, against a `.usage.usage` whose
rows have nanosecond tails (#1316).

test_capture.py drives the capture through FakeFleet, which hands back
whatever a test puts in a dict. What it cannot show is the round trip the
watermark makes: a timestamp column leaves q at nanosecond precision, kola
turns it into a Python ``datetime`` with microseconds, and whatever the
capture sends back as ``since`` is what q's ``>`` judges the next sweep by.
That is where the newest row of every idle process was re-captured once per
interval - the watermark came back 789 ns before the row it was taken from.
Here the q side is real: `.usage.usage` as `.z.p` stamps it.
"""

from __future__ import annotations

from typing import Any

import pytest

from uqf_frontend.capture import MemorySink, UsageCapture
from uqf_frontend.config import Process, Settings
from uqf_frontend.fleet import KolaFleet

SERVER_SCRIPT = """
/ Three rows as .z.p stamps them: every one has a sub-microsecond tail.
.usage.usage:([] time:2026.10.14D10:00:00.000000100 2026.10.14D10:00:00.000000900
    2026.10.14D10:00:01.123456789; proctype:3#`rdb; id:til 3);
/ One more row, a single nanosecond after the newest - inside the window a
/ microsecond watermark could never see.
add_one_ns_later:{[unused]
    `.usage.usage insert (1+max .usage.usage`time;`rdb;count .usage.usage);
    count .usage.usage};
"""


@pytest.fixture(scope="module")
def fleet(start_q, tmp_path_factory) -> Any:
    script = tmp_path_factory.mktemp("usage") / "usage.q"
    script.write_text(SERVER_SCRIPT)
    with start_q(str(script)) as port:
        yield KolaFleet(Settings(processes=(Process("rdb1", "localhost", port),)))


def test_two_sweeps_over_an_unchanged_table_append_nothing_the_second_time(fleet):
    sink = MemorySink()
    cap = UsageCapture(fleet, sink)
    assert cap.capture_once().captured == {"rdb1": 3}
    assert cap.capture_once().captured == {"rdb1": 0}, "nothing new on the process"
    assert [r["id"] for r in sink.rows["rdb1"]] == [0, 1, 2], "each row in the sink once"


def test_the_watermark_is_the_newest_row_s_time_at_q_s_precision(fleet):
    cap = UsageCapture(fleet, MemorySink())
    cap.capture_once()
    assert cap.watermark("rdb1") == fleet.one("rdb1", '"j"$max .usage.usage`time').value


def test_a_row_one_nanosecond_after_the_watermark_is_still_captured(fleet):
    """The fix must not trade a duplicate for a hole: rounding the watermark
    UP to the next microsecond would also stop the re-capture, and would
    then miss any row that lands inside that microsecond."""
    sink = MemorySink()
    cap = UsageCapture(fleet, sink)
    cap.capture_once()
    before = cap.watermark("rdb1")
    assert fleet.one("rdb1", "add_one_ns_later", 0).value == 4
    assert cap.capture_once().captured == {"rdb1": 1}
    assert cap.watermark("rdb1") == before + 1
    assert [r["id"] for r in sink.rows["rdb1"]][-1] == 3
