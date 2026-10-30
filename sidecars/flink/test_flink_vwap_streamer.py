"""The flink_vwap publisher (the flink bundle's flink_vwap_streamer.py, #1776).

No Flink and no tickerplant: Flink's result rows are plain tuples here, which
is what a pyflink Row unpacks to.

The flink bundle's own Python tests: scripts/dev/bundle_suite.py installs the
bundle into a throwaway tree and runs them there, against the INSTALLED copies
in src/etl/connectors/flink/ - the feed module the CLI discovered, and the
streamer beside it on sys.path.
"""

import importlib
import re
import sys
from datetime import datetime
from pathlib import Path

from uqs.cli import external as cli_external
from uqs.external.plant_contract import plant_table_fields
from uqs.paths import TABLES_FILE, repo_root

REPO = repo_root()
#: The installed streamer: bundle_suite.py puts the installed folder on sys.path.
flink_vwap_streamer = importlib.import_module("flink_vwap_streamer")

ROWS = [
    ("EURUSD", datetime(2026, 10, 9, 9, 1), 1.1012, 3_000_000.0, 7),
    ("GBPUSD", datetime(2026, 10, 9, 9, 1), 1.2701, 1_500_000.0, 4),
]


def test_each_row_is_published_as_its_own_batch_keyed_by_fields():
    out = list(flink_vwap_streamer.batches(ROWS))
    assert len(out) == 2
    assert out[0] == [dict(zip(flink_vwap_streamer.FIELDS, ROWS[0], strict=True))]


def plant_text() -> str:
    """The tree's plant_tables.q, which holds `flink_vwap_raw` once the bundle is installed."""
    return (REPO / TABLES_FILE).read_text()


def plant_order(text: str | None = None) -> list[str]:
    return plant_table_fields(str(REPO), "flink_vwap_raw", text=text or plant_text())


def test_columns_follow_the_plant_order():
    records = [r for b in flink_vwap_streamer.batches(ROWS) for r in b]
    cols = flink_vwap_streamer.to_columns(records, plant_order())
    assert list(cols) == plant_order()
    assert cols["sym"] == ["EURUSD", "GBPUSD"]
    assert cols["n"] == [7, 4]


def test_the_sql_selects_exactly_the_plants_columns():
    """The SELECT names the same set of columns as the raw table (less `time`);
    their ORDER is the plant's business, not the SQL's."""
    assert sorted(flink_vwap_streamer.FIELDS) == sorted(plant_order())
    # Column names live in the declaration line itself, so the parser and a
    # regex over the raw line must agree on the real file.
    line = next(
        x
        for x in plant_text().splitlines()
        if x.removeprefix(".qetl.plant.").startswith("flink_vwap_raw:([]")
    )
    assert re.findall(r"(\w+):`", line) == ["time", *plant_order()]


def test_a_reordered_plant_reorders_what_is_sent():
    """.u.upd places by position: swap vwap and volume in the plant and the
    streamer must follow, or each value lands in the other's column."""
    swapped = plant_text().replace(
        "vwap:`float$(); volume:`float$()", "volume:`float$(); vwap:`float$()"
    )
    assert swapped != plant_text()
    order = plant_order(swapped)
    assert order == ["sym", "window_end", "volume", "vwap", "n"]
    records = [r for b in flink_vwap_streamer.batches(ROWS) for r in b]
    cols = flink_vwap_streamer.to_columns(records, order)
    assert list(cols) == order
    # Each value stays under its own name; only the position moves.
    assert cols["vwap"] == [1.1012, 1.2701]
    assert cols["volume"] == [3_000_000.0, 1_500_000.0]


def test_the_window_query_selects_fields_in_order():
    sql = flink_vwap_streamer.WINDOW_SQL
    aliases = re.findall(r"AS (\w+)", sql)
    assert aliases == ["sym", "vwap", "volume", "n"]
    assert sql.index("AS sym") < sql.index("window_end,") < sql.index("AS vwap")


def test_the_feed_runs_its_streamer_from_beside_its_installed_copy():
    """The feed is discovered from src/etl/connectors/flink/, never the bundle's
    folder, and `start` runs the streamer that was installed with it."""
    feed = sys.modules[cli_external.FEEDS["flink_vwap"].start.__module__]
    installed = REPO / "src" / "etl" / "connectors" / "flink"
    assert Path(str(feed.__file__)).resolve() == (installed / "flink_vwap_feed.py").resolve()
    assert Path(str(flink_vwap_streamer.__file__)).resolve().parent == installed.resolve()
