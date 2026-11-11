"""Hold a Databento live subscription and push its records at a tickerplant.

Run by `databento_feed.start_databento_feed`, not imported by the CLI: it
is a process, and its dependencies (the `databento` client) are optional -
the rest of the orchestrator must import without them.

The decoding is deliberately separate from everything that touches a socket,
so it is testable with no API key, no network and no tickerplant. That is the
whole of the logic here; `main` is plumbing.

WHAT A REAL RECORD CARRIES, AND WHAT THE PLANT TABLE WANTS (#2190)

  price, *_px   an int in fixed-point units of 1e-9 (271200000000 is 271.20).
                Read through the record's own `pretty_*` converters, which
                divide by `FIXED_PRICE_SCALE` and turn `UNDEF_PRICE` into NaN.
                Cast by hand, the int would land 1e9 times too large and
                nothing would raise.
  ts_event      an int, nanoseconds since 1970. Sent as a polars ns datetime,
                which kola writes as a q timestamp (q counts from 2000, so a
                bare long would be thirty years off).
  side, action  `databento_dbn.Side` / `Action` enums, whose values are the
                one-letter codes the plant already stores (`A`/`B`/`N`,
                `A`/`C`/`M`/`R`/`T`/`F`/`N`) - the same letters the ODBC
                source reads out of a DBN extract.
  symbol        ABSENT: MBP-10 carries an `instrument_id`, and the name comes
                from a `SymbolMappingMsg` earlier in the same stream - what the
                live client's own `symbology_map` is built from. A record whose
                id has no mapping is refused with a reason, never published
                with a null sym.

And the row goes to the plant (`ACCEPT_Q`, which wraps `.u.upd`) as a LIST of
typed columns, not a dict: the segmented tickerplant prepends its `time` with
`(enlist ...),x`, which a dictionary does not accept (kafka_streamer.publish_message, #1492).
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any

import polars as pl

from uqs.external.kafka_streamer import ACCEPT_Q
from uqs.paths import UqsError

#: Databento always sends ten levels. A thin book (an opening auction, an
#: illiquid name) fills the empty ones with `UNDEF_PRICE` and size 0, and
#: those become nulls, not zeros: zero is a price.
LEVELS = 10

#: The four per-level quantities, as the contract's column prefixes, and the
#: `BidAskPair` attribute each one reads.
LEVEL_PREFIXES = ("bid_px_", "bid_sz_", "ask_px_", "ask_sz_")

#: Column order comes from the q source declaration, read at runtime rather
#: than restated here. `.u.upd` positions by index, so a field added in q
#: and forgotten here would transpose two same-typed columns silently -
#: prices into sizes - and nothing would raise.
CONTRACT_Q = "src/etl/sources/databento_mbp10.q"

#: q's `ts_event`/symbol/float/long, as the polars dtypes kola writes them in:
#: an exact type per column, because `insert` refuses a float where a long
#: belongs, and a Python list of mixed ints and Nones has no single q type.
TIMESTAMP, SYMBOL, FLOAT, LONG = pl.Datetime("ns"), pl.Categorical, pl.Float64, pl.Int64


def field_dtype(field: str) -> Any:
    """The dtype a contract field is published in.

    By name, like the level columns themselves; a test holds this against
    the plant table's own column types, so a q change cannot drift past it.
    """
    if field == "ts_event":
        return TIMESTAMP
    if field in ("symbol", "action", "side"):
        return SYMBOL
    if field == "price" or field.startswith(("bid_px_", "ask_px_")):
        return FLOAT
    if field in ("size", "sequence") or field.startswith(("bid_sz_", "ask_sz_")):
        return LONG
    raise KeyError(f"databento_mbp10 has a field the streamer does not type: {field}")


def contract_fields(repo_root: str, q_file: str = CONTRACT_Q) -> list[str]:
    """The source contract's field order, parsed out of its q declaration.

    Reading the q rather than duplicating the list is the same discipline
    `model/schemas.py` follows for the table definitions: one place to change, and
    a mismatch is impossible rather than merely unlikely. `q_file` is where
    the databento bundle's source sits once installed; a test points it at
    the bundle's own folder.
    """
    text = open(os.path.join(repo_root, q_file), encoding="utf-8").read()
    # columns:`ts_event`symbol`action`side`price`size`sequence,level_fields
    head = next(
        line for line in text.splitlines() if re.match(r"(?:\.[A-Za-z_][\w.]*\.)?columns:", line)
    ).split(":", 1)[1]
    scalars = [f for f in head.split(",")[0].split("`") if f]
    levels = [f"{p}{i:02d}" for i in range(LEVELS) for p in LEVEL_PREFIXES]
    return scalars + levels


class Refused(Exception):
    """A record that cannot become a row, and why. Never published."""


def _px(value: float) -> float | None:
    """A `pretty_*` price, with the client's NaN for `UNDEF_PRICE` as a null."""
    return None if math.isnan(value) else value


def _level(level: Any, prefix: str) -> float | int | None:
    """One level's quantity. A level whose price is undefined is an empty slot
    in the book: its size is null too, not the 0 Databento pads it with."""
    side = prefix[:3]
    px = _px(getattr(level, f"pretty_{side}_px"))
    if prefix.endswith("px_"):
        return px
    return None if px is None else getattr(level, f"{side}_sz")


class RecordDecoder:
    """Real `databento_dbn` records in, contract rows out.

    Holds the stream's instrument_id -> symbol map. It learns from each
    `SymbolMappingMsg` it is shown, and falls back to `symbology_map` - the live
    client's own, which the client fills from the same messages - so a mapping
    either one saw is enough.
    """

    def __init__(self, fields: list[str], symbology_map: Mapping[int, Any] | None = None):
        import databento_dbn

        self.dbn = databento_dbn
        self.fields = fields
        self.symbols: dict[int, str] = {}
        self.client_map: Mapping[int, Any] = symbology_map if symbology_map is not None else {}

    def symbol(self, instrument_id: int) -> str:
        found = self.symbols.get(instrument_id)
        if found is None and instrument_id in self.client_map:
            found = str(self.client_map[instrument_id])
        if not found:
            raise Refused(
                f"instrument_id {instrument_id} has no symbol: no SymbolMappingMsg for it "
                "has arrived, so the row is refused rather than published with a null sym"
            )
        return found

    def row(self, rec: Any) -> dict[str, Any] | None:
        """The contract row for `rec`, or None for a record that is not a book
        event (a symbol mapping is learned here). Raises Refused, with the
        reason, for a record that cannot be published."""
        dbn = self.dbn
        if isinstance(rec, dbn.SymbolMappingMsg):
            self.symbols[rec.instrument_id] = str(rec.stype_out_symbol)
            return None
        if isinstance(rec, dbn.ErrorMsg):
            raise Refused(f"gateway error: {rec.err}")
        if not isinstance(rec, dbn.MBP10Msg):
            return None
        levels = rec.levels
        out: dict[str, Any] = {}
        for f in self.fields:
            if f.startswith(LEVEL_PREFIXES):
                out[f] = _level(levels[int(f[-2:])], f[:7])
            elif f == "ts_event":
                out[f] = None if rec.ts_event == dbn.UNDEF_TIMESTAMP else rec.ts_event
            elif f == "symbol":
                out[f] = self.symbol(rec.instrument_id)
            elif f in ("action", "side"):
                out[f] = getattr(rec, f).value
            elif f == "price":
                out[f] = _px(rec.pretty_price)
            elif f in ("size", "sequence"):
                out[f] = getattr(rec, f)
            else:
                raise KeyError(f"databento_mbp10 has a field the streamer does not fill: {f}")
        return out


def columns(rows: list[dict[str, Any]], fields: list[str]) -> list[pl.Series]:
    """`rows` as `.u.upd` takes them: a list of typed columns, contract order.

    A list per column even for one row (`torq_pipeline.q`, invariant 3), and
    no `time`: the tickerplant stamps its own and refuses a row one column too
    wide (invariant 1). `ts_event` goes as epoch nanoseconds cast to an ns
    datetime, which kola rebases onto q's 2000 epoch exactly.
    """
    out = []
    for f in fields:
        dtype = field_dtype(f)
        values = [r[f] for r in rows]
        if dtype == TIMESTAMP:
            out.append(pl.Series(f, values, dtype=pl.Int64).cast(TIMESTAMP))
        else:
            out.append(pl.Series(f, values, dtype=dtype))
    return out


def publish(
    q: Any,
    table: str,
    decoder: RecordDecoder,
    rec: Any,
    log: Callable[[str], None],
) -> bool:
    """Send one record to the tickerplant; True when the plant stored a row.

    Sent through `ACCEPT_Q`, raising unless the plant STORED it: a bare
    `.u.upd` answers success for a row the tickerplant sent to its error log
    (#2698), so a refused row would be reported as published and lost.

    A refused record is logged with its reason and skipped: the feed stays up
    for the instruments it can name, and the log says which it cannot.
    """
    try:
        row = decoder.row(rec)
    except Refused as exc:
        log(f"databento record refused: {exc}")
        return False
    if row is None:
        return False
    answer = q.sync(ACCEPT_Q, table, columns([row], decoder.fields))
    if isinstance(answer, str):
        raise UqsError(f"the tickerplant refused the {table} row - NOT stored: {answer}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--symbols", required=True, help="comma separated")
    parser.add_argument("--table", required=True)
    parser.add_argument("--repo-root", default=os.getcwd())
    args = parser.parse_args(argv)

    try:
        import databento  # ty: ignore[unresolved-import]
        import kola
    except ImportError as exc:  # pragma: no cover - depends on the extra
        print(
            f"missing dependency: {exc}. Install the live-feed extra:\n"
            "    uv pip install databento",
            file=sys.stderr,
        )
        return 1

    fields = contract_fields(args.repo_root)
    # stp1's user:password from the environment its launcher gave it, never
    # an argument, where ps would show it (#1853).
    from uqs.external.connection import credential_from_env

    user, password = credential_from_env()
    # passwd, not password: kola.Q's own keyword, the same one
    # the frontend gateway passes.
    q = kola.Q(args.host, args.port, user=user, passwd=password)
    q.connect()

    client = databento.Live(key=os.environ["DATABENTO_API_KEY"])
    client.subscribe(
        dataset=args.dataset,
        schema="mbp-10",
        symbols=args.symbols.split(","),
    )

    decoder = RecordDecoder(fields, client.symbology_map)

    def log(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    # One record at a time rather than a buffered batch: a tickerplant is
    # the thing that batches, and holding rows here to make bigger writes
    # would add latency to hide work the tickerplant already does well.
    for record in client:
        publish(q, args.table, decoder, record, log)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
