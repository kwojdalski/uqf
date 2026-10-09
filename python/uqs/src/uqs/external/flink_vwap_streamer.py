"""flink_vwap_streamer.py - run an Apache Flink job and push the windows it
closes at the tickerplant. Started by `uqs feed start flink_vwap`
(flink_vwap_feed.py).

Flink does the stream processing; q only receives its result. The job is SQL:
a tumbling one-minute window per sym over a trade stream, giving VWAP, volume
and trade count. `execute().collect()` runs it on a local Flink mini-cluster
inside this process and hands back each row as its window closes, so there is
no Flink cluster to stand up and no sink connector to write.

The source is Flink's own `datagen` connector, so the example runs with no
broker. For real trades, replace SOURCE_DDL's WITH clause with a Kafka (or
any other) connector; the window query does not change.

`apache-flink` (and the Java it needs) is deliberately NOT a dependency of
uqs, as `confluent-kafka` is not for kafka_streamer.py: the rest of the
orchestrator must import without it. Only `main` imports it.

## Delivery

A collected Flink result is at-least-once: a restarted job, or one replaying
a Kafka source from its last checkpoint, emits windows it emitted before.
Every row carries (sym; window_end), unique per window, and the q job
`flink_vwap` drops any window at or below the latest it has published for that
sym. The SQL is append-only - a window is emitted once it is final - so a
repeat is never a correction.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Iterator
from typing import Any

#: The raw table `flink_vwap_raw`'s columns in plant order. `time` is not sent:
#: .u.upd stamps it.
FIELDS = ["sym", "window_end", "vwap", "volume", "n"]

#: Synthetic trades for three pairs. `ts` is the processing-time clock, made an
#: event-time attribute so TUMBLE can window on it.
SOURCE_DDL = """
CREATE TABLE trades (
    pair INT,
    px DOUBLE,
    qty DOUBLE,
    ts AS LOCALTIMESTAMP,
    WATERMARK FOR ts AS ts - INTERVAL '1' SECOND
) WITH (
    'connector' = 'datagen',
    'rows-per-second' = '20',
    'fields.pair.min' = '0', 'fields.pair.max' = '2',
    'fields.px.min' = '1.05', 'fields.px.max' = '1.15',
    'fields.qty.min' = '100000', 'fields.qty.max' = '5000000'
)
"""

#: One row per sym per closed window, in FIELDS order.
WINDOW_SQL = """
SELECT
    CASE pair WHEN 0 THEN 'EURUSD' WHEN 1 THEN 'GBPUSD' ELSE 'USDJPY' END AS sym,
    window_end,
    SUM(px * qty) / SUM(qty) AS vwap,
    SUM(qty) AS volume,
    COUNT(*) AS n
FROM TABLE(TUMBLE(TABLE trades, DESCRIPTOR(ts), INTERVAL '1' MINUTES))
GROUP BY pair, window_start, window_end
"""


def batches(rows: Iterable[Any]) -> Iterator[list[dict[str, Any]]]:
    """Flink's result rows, each as a one-record list keyed by FIELDS.

    One row per publish: the collect iterator blocks until the next window
    closes, so buffering would hold a finished window back for a minute, and
    at a few rows a minute there is nothing to save by batching.
    """
    for row in rows:
        yield [dict(zip(FIELDS, tuple(row), strict=True))]


def to_columns(records: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Records as .u.upd wants them: one list per column, in FIELDS order -
    .u.upd places columns by position, not by name."""
    return {field: [record[field] for record in records] for field in FIELDS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--credential", required=True)
    parser.add_argument("--table", default="flink_vwap_raw")
    args = parser.parse_args(argv)
    try:
        import kola
        from pyflink.table import (  # ty: ignore[unresolved-import]
            EnvironmentSettings,
            TableEnvironment,
        )
    except ImportError as exc:  # pragma: no cover - depends on the extra
        print(
            f"missing dependency: {exc}. Install it (Flink also needs Java 11+):\n"
            "    uv pip install apache-flink kola",
            file=sys.stderr,
        )
        return 1
    user, _, password = args.credential.partition(":")
    q = kola.Q(args.host, args.port, user=user, passwd=password)
    q.connect()

    env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
    env.execute_sql(SOURCE_DDL)
    with env.sql_query(WINDOW_SQL).execute().collect() as rows:
        for batch in batches(rows):
            # `sync`: the call returns once the plant has the rows.
            q.sync(".u.upd", args.table, to_columns(batch))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
