#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb>=1.5,<2"]
# ///
"""Write a DuckDB database of mock FX deals, for the duckdb_deals backfill.

The source that ``src/etl/sources/duckdb_deals.q`` reads over ODBC: one
table, ``deals``, in the shape of the demo_deals source - ``deal_id``,
``deal_time``, ``sym``, ``side``, ``notional``, ``rate`` - so the same
dataset can be read over IPC (demo_deals) or ODBC (this). Its first five rows
ARE demo_deals' fixture, unchanged; the rest are generated.

MOCK DATA, and deterministic. Every run with the same arguments writes the same
rows, because the generator is seeded: a test or a smoke run that reads this
file can assert exact counts. Prices walk from each pair's fixture level in
small steps, so they stay plausible; nothing else about them is meant to be.

A PEP 723 script rather than a workspace dependency, for the reason
``dump_databento_duckdb.py`` gives: DuckDB is needed only to build this file.

    uv run scripts/dev/make_fx_deals_duckdb.py
    uv run scripts/dev/make_fx_deals_duckdb.py --days 30 --per-day 1000

``deal_time`` is stored as ``TIMESTAMP_NS``, UTC. Re-running replaces the file.
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# PEP 723 dependency, deliberately not in the workspace venv ty resolves against.
import duckdb  # ty: ignore[unresolved-import]

#: demo_deals' fixture (src/etl/sources/demo_deals.q), row for row.
FIXTURE_START = datetime(2026, 9, 11, 9, tzinfo=UTC)
FIXTURE = [
    (1, FIXTURE_START + timedelta(days=0), "EURUSD", "buy", 1_000_000.0, 1.0842),
    (2, FIXTURE_START + timedelta(days=1), "GBPUSD", "sell", 2_500_000.0, 1.2631),
    (3, FIXTURE_START + timedelta(days=2), "EURUSD", "buy", 750_000.0, 1.0847),
    (4, FIXTURE_START + timedelta(days=3), "USDJPY", "sell", 3_000_000.0, 149.82),
    (5, FIXTURE_START + timedelta(days=4), "EURUSD", "buy", 1_250_000.0, 1.0851),
]

#: pair -> (starting rate, one step of the walk)
PAIRS = {
    "EURUSD": (1.0842, 0.0002),
    "GBPUSD": (1.2631, 0.0002),
    "USDJPY": (149.82, 0.02),
    "AUDUSD": (0.6612, 0.0002),
}

NANOS_PER_DAY = 86_400 * 10**9


def repo_root() -> Path:
    """The main checkout's root, even from a git worktree (see dump_databento_duckdb.py)."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent,
    ).stdout.strip()
    return Path(common).parent


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root() / "output" / "duckdb" / "fx_deals.duckdb",
        help="DuckDB database file to write",
    )
    parser.add_argument("--start", default="2026-09-11", help="first day of generated deals, UTC")
    parser.add_argument("--days", type=int, default=5, help="days of generated deals")
    parser.add_argument("--per-day", type=int, default=200, help="generated deals per day")
    parser.add_argument("--seed", type=int, default=20260911, help="generator seed")
    return parser.parse_args(argv)


def generated(start: datetime, days: int, per_day: int, seed: int, first_id: int) -> list[tuple]:
    """Deals after the fixture's: per day, sorted by time, ids continuing on."""
    rng = random.Random(seed)
    level = {pair: rate for pair, (rate, _) in PAIRS.items()}
    rows = []
    deal_id = first_id
    for day in range(days):
        midnight = start + timedelta(days=day)
        offsets = sorted(rng.randrange(NANOS_PER_DAY) for _ in range(per_day))
        for offset in offsets:
            pair = rng.choice(list(PAIRS))
            step = PAIRS[pair][1]
            level[pair] += step * rng.choice((-1, 0, 1))
            rows.append(
                (
                    deal_id,
                    midnight + timedelta(microseconds=offset // 1000),
                    offset % 1000,
                    pair,
                    rng.choice(("buy", "sell")),
                    250_000.0 * rng.randint(1, 20),
                    round(level[pair], 5),
                )
            )
            deal_id += 1
    return rows


def write(out: Path, rows: list[tuple]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with duckdb.connect(str(out)) as con:
        con.execute(
            "CREATE TABLE deals (deal_id BIGINT PRIMARY KEY, deal_time TIMESTAMP_NS,"
            " sym VARCHAR, side VARCHAR, notional DOUBLE, rate DOUBLE)"
        )
        # Python datetimes stop at microseconds, so the nanosecond remainder is
        # added in SQL rather than lost on the way in.
        con.executemany(
            "INSERT INTO deals VALUES (?, make_timestamp_ns(epoch_ns(?::TIMESTAMP) + ?),"
            " ?, ?, ?, ?)",
            [(i, t.replace(tzinfo=None), ns, s, side, n, r) for i, t, ns, s, side, n, r in rows],
        )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    fixture = [(i, t, 0, s, side, n, r) for i, t, s, side, n, r in FIXTURE]
    rows = fixture + generated(start, args.days, args.per_day, args.seed, len(FIXTURE) + 1)
    write(args.out, rows)
    print(f"wrote {len(rows)} deals to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
