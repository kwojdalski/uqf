"""Hold a Databento live subscription and push its records at a tickerplant.

Run by `databento_feed.start_databento_feed`, not imported by the CLI: it
is a process, and its dependencies (the `databento` client) are optional -
the rest of the orchestrator must import without them.

`rows_from_records` is deliberately separate from everything that touches a
socket, so the column order, the missing-level padding and the
one-list-per-column rule are testable with no API key, no network and no
tickerplant. That is the whole of the logic here; `main` is plumbing.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

#: Databento sends ten levels; a record with fewer (a thin book, an opening
#: auction) still has to produce all forty columns or `.u.upd` rejects the
#: row on length. Missing levels are nulls, not zeros: zero is a price.
LEVELS = 10

#: Column order comes from the q source declaration, read at runtime rather
#: than restated here. `.u.upd` positions by index, so a field added in q
#: and forgotten here would transpose two same-typed columns silently -
#: prices into sizes - and nothing would raise.
CONTRACT_Q = "src/etl/sources/databento_mbp10.q"


def contract_fields(repo_root: str) -> list[str]:
    """The source contract's field order, parsed out of its q declaration.

    Reading the q rather than duplicating the list is the same discipline
    `schemas.py` follows for the table definitions: one place to change, and
    a mismatch is impossible rather than merely unlikely.
    """
    text = open(os.path.join(repo_root, CONTRACT_Q), encoding="utf-8").read()
    # fields:`ts_event`symbol`action`side`price`size`sequence,level_fields
    head = next(line for line in text.splitlines() if line.startswith("fields:")).split(":", 1)[1]
    scalars = [f for f in head.split(",")[0].split("`") if f]
    prefixes = ("bid_px_", "bid_sz_", "ask_px_", "ask_sz_")
    levels = [f"{p}{i:02d}" for i in range(LEVELS) for p in prefixes]
    return scalars + levels


def rows_from_records(records: list[Any], fields: list[str]) -> dict[str, list]:
    """Databento records as one list per column, in the contract's order.

    Returns a dict rather than a DataFrame because that is what kola sends
    and what `.u.upd` wants: a list per column, never a bare atom, even for
    one record (`torq_pipeline.q`, invariant 3).

    `time` is absent on purpose - the tickerplant stamps its own on receipt
    and refuses a row that is one column too wide (invariant 1). The venue's
    clock travels as `ts_event`.
    """
    out: dict[str, list] = {f: [] for f in fields}
    for rec in records:
        levels = list(getattr(rec, "levels", []) or [])
        for f in fields:
            if f.startswith(("bid_px_", "bid_sz_", "ask_px_", "ask_sz_")):
                idx = int(f[-2:])
                level = levels[idx] if idx < len(levels) else None
                if level is None:
                    out[f].append(None)
                    continue
                attr = {
                    "bid_px_": "bid_px",
                    "bid_sz_": "bid_sz",
                    "ask_px_": "ask_px",
                    "ask_sz_": "ask_sz",
                }[f[:7]]
                out[f].append(getattr(level, attr, None))
            elif f == "symbol":
                out[f].append(getattr(rec, "symbol", None) or getattr(rec, "raw_symbol", None))
            else:
                out[f].append(getattr(rec, f, None))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--credential", required=True, help="user:password for stp1")
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
            "    uv pip install databento kola",
            file=sys.stderr,
        )
        return 1

    fields = contract_fields(args.repo_root)
    user, _, password = args.credential.partition(":")
    # passwd, not password: kola.Q's own keyword, the same one
    # uqf_client and the frontend gateway pass.
    q = kola.Q(args.host, args.port, user=user, passwd=password)
    q.connect()

    client = databento.Live(key=os.environ["DATABENTO_API_KEY"])
    client.subscribe(
        dataset=args.dataset,
        schema="mbp-10",
        symbols=args.symbols.split(","),
    )

    # One record at a time rather than a buffered batch: a tickerplant is
    # the thing that batches, and holding rows here to make bigger writes
    # would add latency to hide work the tickerplant already does well.
    for record in client:
        cols = rows_from_records([record], fields)
        if not cols[fields[0]]:
            continue
        q.sync(".u.upd", args.table, cols)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
