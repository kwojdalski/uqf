"""Reading the real RDB, and nothing else about the stack.

The verifier asks the RDB over kdb+ IPC (kola, as `uqs query` does) and
compares what it finds: a recorder inside a q process would only prove the
process agrees with itself.
"""

from __future__ import annotations

from typing import Any

#: crypto_trade's column types, as q's `meta` reports them: the bundle's tables.q.
CRYPTO_TRADE_TYPES = {
    "time": "p",
    "event_time": "p",
    "broker_time": "p",
    "sym": "s",
    "trade_id": "s",
    "price": "f",
    "qty": "f",
    "side": "s",
    "topic": "s",
    "partition": "j",
    "offset": "j",
}


def q(expr: str, port: int, timeout: int = 15) -> Any:
    """Run a q expression on the RDB at `port`; raises with q's own error text."""
    from uqs.stack.runtime import query

    return query(expr, port, timeout=timeout)


def count(table: str, port: int) -> int:
    return int(q(f"count {table}", port))


def types(table: str, port: int) -> dict[str, str]:
    """column -> q type char, from the process's own `meta`."""
    frame = q(f"select c, t from meta {table}", port)
    return dict(zip(frame["c"].to_list(), frame["t"].to_list(), strict=True))


def rows(table: str, port: int) -> list[dict[str, Any]]:
    """Every row of `table` as a dict, ordered by its Kafka coordinates when it has them."""
    cols = q(f"cols {table}", port)
    order = "`topic`partition`offset xasc " if {"topic", "partition", "offset"} <= set(cols) else ""
    return q(f"select from {order}{table}", port).to_dicts()
