"""The flink_vwap publisher (python/uqs/src/uqs/external/flink_vwap_streamer.py).

No Flink and no tickerplant: Flink's result rows are plain tuples here, which
is what a pyflink Row unpacks to.
"""

import re
from datetime import datetime
from pathlib import Path

from uqs.external import flink_vwap_streamer

REPO = Path(__file__).resolve().parents[3]

ROWS = [
    ("EURUSD", datetime(2026, 10, 9, 9, 1), 1.1012, 3_000_000.0, 7),
    ("GBPUSD", datetime(2026, 10, 9, 9, 1), 1.2701, 1_500_000.0, 4),
]


def test_each_row_is_published_as_its_own_batch_keyed_by_fields():
    out = list(flink_vwap_streamer.batches(ROWS))
    assert len(out) == 2
    assert out[0] == [dict(zip(flink_vwap_streamer.FIELDS, ROWS[0], strict=True))]


def test_columns_follow_fields_order():
    records = [r for b in flink_vwap_streamer.batches(ROWS) for r in b]
    cols = flink_vwap_streamer.to_columns(records)
    assert list(cols) == flink_vwap_streamer.FIELDS
    assert cols["sym"] == ["EURUSD", "GBPUSD"]
    assert cols["n"] == [7, 4]


def test_fields_match_the_raw_table_without_time():
    """.u.upd places columns by position, so FIELDS must be the plant's order."""
    text = (REPO / "src/etl/plant_tables.q").read_text()
    line = next(x for x in text.splitlines() if x.startswith("flink_vwap_raw:([]"))
    names = re.findall(r"(\w+):`", line)
    assert names == ["time", *flink_vwap_streamer.FIELDS]


def test_the_window_query_selects_fields_in_order():
    sql = flink_vwap_streamer.WINDOW_SQL
    aliases = re.findall(r"AS (\w+)", sql)
    assert aliases == ["sym", "vwap", "volume", "n"]
    assert sql.index("AS sym") < sql.index("window_end,") < sql.index("AS vwap")
