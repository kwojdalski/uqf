"""Reads the tickerplant table definitions out of `scripts/processes/uqf_stack_tables.q`.

**The definitions themselves live in q, not here.** This module used to hold
them as Python string literals, which meant q source that no q parser read
until `stp1` started: a typo surfaced as a failed tickerplant rather than a
failed commit, `check_q_traps.py` never scanned them (it globs
``git ls-files '*.q'``), and the contract surface reported four tables when
the system had thirteen.

So this file is now a *reader*. The q file is the single source of truth, and
these constants resolve out of it by table name - which is the same
generate-or-check discipline the rest of the tree follows: the definition
exists once, and anything that needs it derives from that one place.

The constants keep their names so every caller - `pipelines.PIPELINES`,
`procs._generated_schema_content`, and the frontend's catalog drift test -
is unchanged by the move.
"""

from __future__ import annotations

import re
from pathlib import Path

from torq_orchestrator.logger import get_logger

log = get_logger(__name__)

#: The q file that owns these definitions. Resolved from this module rather
#: than a working directory, so the reader works from anywhere.
TABLES_Q = Path(__file__).resolve().parents[4] / "scripts" / "processes" / "uqf_stack_tables.q"

#: `name:([]...)` at the start of a line, to the end of that line. Comments in
#: the q file start with `/` and never begin a definition, so a line-anchored
#: match cannot pick one up.
_DEFINITION = re.compile(r"^([a-z_][a-z0-9_]*):\(\[\].*$", re.MULTILINE)


def _definitions() -> dict[str, str]:
    """{table name: its one-line q definition}, read from the q file.

    Read at import and cached in the constants below rather than on every
    call: these are consulted while generating `database.q`, which
    `bootstrap()` does on *every* uqf-stack command.
    """
    if not TABLES_Q.is_file():  # pragma: no cover - a broken checkout
        raise FileNotFoundError(f"table definitions not found at {TABLES_Q}")
    text = TABLES_Q.read_text()
    found = {match.group(1): match.group(0) for match in _DEFINITION.finditer(text)}
    if not found:  # pragma: no cover - guarded by test_schemas.py
        raise ValueError(
            f"{TABLES_Q} defines no tables - the `name:([]...)` convention this "
            "reader matches on has changed, and every generated database.q "
            "would silently lose uqf's tables"
        )
    return found


def definition(table: str) -> str:
    """One table's q definition, or a refusal naming it.

    Throws rather than returning None: a missing definition that reaches
    `_generated_schema_content` becomes a tickerplant without the table, and
    the first symptom is a feed failing to publish.
    """
    found = _definitions()
    if table not in found:
        raise KeyError(
            f"{table!r} is not defined in {TABLES_Q.name} - it has {', '.join(sorted(found))}"
        )
    return found[table]


_DEFS = _definitions()

#: Levels in the wide book. The columns are written out in the q file rather
#: than generated from this, so it is a fact ABOUT that file rather than the
#: thing that produces it - `test_schemas.py` holds the two in agreement.
WIDE_BOOK_LEVELS = 11

QUOTES_TABLE_SCHEMA = _DEFS["quotes"]
WIDE_BOOK_TABLE_SCHEMA = _DEFS["wide_book"]
MKT_ORDERBOOK_TABLE_SCHEMA = _DEFS["mkt_orderbook"]
DATABENTO_MBP10_TABLE_SCHEMA = _DEFS["databento_mbp10"]
DATABENTO_BOOK_TABLE_SCHEMA = _DEFS["databento_book"]
CRYPTO_BOOK_TABLE_SCHEMA = _DEFS["crypto_book"]
CRYPTO_SIM_FILLS_TABLE_SCHEMA = _DEFS["crypto_sim_fills"]
CRYPTO_TRADES_TABLE_SCHEMA = _DEFS["crypto_trades"]
TRADES_TABLE_SCHEMA = _DEFS["trades"]
POSITION_TABLE_SCHEMA = _DEFS["position"]
EXECUTION_QUALITY_TABLE_SCHEMA = _DEFS["execution_quality"]
EXECUTIONS_TABLE_SCHEMA = _DEFS["executions"]
ORDERS_TABLE_SCHEMA = _DEFS["orders"]
FX_POSITION_TABLE_SCHEMA = _DEFS["fx_position"]
FX_LIMIT_BREACH_TABLE_SCHEMA = _DEFS["fx_limit_breach"]
MARKS_TABLE_SCHEMA = _DEFS["marks"]
MARKET_DATA_TABLE_SCHEMA = _DEFS["market_data"]
SUPERBOOK_TABLE_SCHEMA = _DEFS["superbook"]
ARBITRAGE_TABLE_SCHEMA = _DEFS["arbitrage"]
CROSS_ARBITRAGE_TABLE_SCHEMA = _DEFS["cross_arbitrage"]
CONFIG_CHANGE_TABLE_SCHEMA = _DEFS["config_change"]
