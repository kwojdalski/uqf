"""The q gateway client.

Reuses the kola IPC pattern already proven in this repo by
``uqs.stack.runtime.query()`` rather than introducing a second mechanism,
per FE-16.

``Gateway`` is a Protocol so that tests run with a fake and no q process.
That is the same posture ETL-19 takes on the q side: double the adapters at
the edges, test the logic in the middle directly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from uqf_frontend.config import Settings
from uqf_frontend.errors import (
    GatewayReloading,
    GatewayUnavailable,
    QueryRejected,
    QueryTimedOut,
)

#: Substrings q/TorQ puts in an error when the gateway is mid-EOD-reload.
#: FE-12 requires this be surfaced as a transient state, not a failure.
_RELOADING_MARKERS = ("eod", "reload", "not available")
_TIMEOUT_MARKERS = ("timeout", "timed out")


#: The backend tiers a query may be routed to. `rdb` holds today's session,
#: `hdb` the completed partitions - FE-08 requires the split be explicit
#: rather than hidden, because FE-11 expects hdb to be slower.
TIERS: dict[str, list[str]] = {
    "rdb": ["rdb"],
    "hdb": ["hdb"],
    "both": ["rdb", "hdb"],
}


@runtime_checkable
class Gateway(Protocol):
    """Everything the API layer needs from q. Deliberately tiny."""

    def call(self, program: str, *args: Any) -> Any:
        """Run a server-authored program on the gateway process itself."""
        ...

    def route(self, program: str, args: tuple[Any, ...], tiers: list[str]) -> Any:
        """Route a server-authored program to backend tiers via
        ``.gw.syncexec``, which razes the results.

        The program travels as a **char vector** at the head of a query list
        so the backend's ``value`` *applies* it to *args* instead of parsing
        anything. See queries.py.
        """
        ...


class KolaGateway:
    """A :class:`Gateway` backed by a real kdb+ IPC connection.

    Connects per call rather than holding a long-lived handle. That costs a
    round trip but means a gateway restart, or the EOD reload window, cannot
    leave this process wedged behind a dead handle - which matters more for a
    poll-only frontend (FE-10) where every view reconnects on a timer anyway.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def call(self, program: str, *args: Any) -> Any:
        return self._exec(program, args)

    def route(self, program: str, args: tuple[Any, ...], tiers: list[str]) -> Any:
        # bytes, not str: kola maps str to a q symbol, and a symbol at the
        # head of the query list makes the backend's `value` try to resolve a
        # variable named the whole lambda.
        query = [program.encode(), *args]
        return self._exec(".gw.syncexec", (query, tiers))

    def _exec(self, program: str, args: tuple[Any, ...]) -> Any:
        import kola

        s = self._settings
        try:
            q = kola.Q(s.host, s.port, user=s.user, passwd=s.passwd, timeout=s.timeout)
        except Exception as exc:  # pragma: no cover - construction rarely fails
            raise GatewayUnavailable(f"could not construct a gateway client: {exc}") from exc

        try:
            q.connect()
        except Exception as exc:
            # Name the variable that fixes it. The two failures here are a
            # wrong port and an unset credential, and both arrive as one
            # opaque line from the driver ("Wrong credential.") that says
            # nothing about where the setting lives - so the reader is left
            # guessing at a package they did not write.
            raise GatewayUnavailable(
                f"gateway at {s.host}:{s.port} is not reachable: {exc} "
                f"(set UQF_FRONTEND_GATEWAY_PORT / _USER / _PASSWD; against the "
                f"local demo stack that is the gateway's port and admin/admin)"
            ) from exc

        try:
            return q.sync(program, *args)
        except Exception as exc:
            raise _classify(exc) from exc
        finally:
            try:
                q.disconnect()
            except Exception:  # pragma: no cover - disconnect failure is not actionable
                pass


def _classify(exc: Exception) -> Exception:
    """Map a raw kola/q failure onto the typed error the frontend reacts to.

    Matching on message text is unlovely, but q signals errors as strings and
    the alternative - treating every failure identically - would make FE-12
    impossible to honour.
    """
    message = str(exc).lower()
    if any(m in message for m in _TIMEOUT_MARKERS):
        return QueryTimedOut(f"the gateway's own query timeout fired: {exc}")
    if any(m in message for m in _RELOADING_MARKERS):
        return GatewayReloading(f"the gateway is reloading and is refusing queries: {exc}")
    return QueryRejected(f"the gateway rejected the query: {exc}")


#: The catalog a FakeGateway answers with unless a test stages its own.
#:
#: Hand-written rather than read from the q tree: a test double that loaded
#: the real declarations would fail for reasons that have nothing to do with
#: the test, and this package no longer depends on that tree at all - which
#: was the point of moving the catalog to the stack. The shapes here only
#: have to be REALISTIC, and the properties tests actually lean on are that
#: `trades` and `quotes` exist, that `quotes` has vector columns (a blank
#: `meta` type, which becomes QType.LIST and is unfilterable), and that
#: `etl_coverage` carries a guid `run_id`.
#:
#: tests/q/test_catalog.q is what holds the REAL catalog honest.
FAKE_CATALOG: list[dict[str, str]] = [
    {"table": "trades", "description": "Client fills"},
    {"table": "quotes", "description": "FX top-of-book and depth as per-row level vectors"},
    {"table": "position", "description": "Running position book marked to the prevailing mid"},
    {"table": "etl_coverage", "description": "Append-only completeness ledger"},
]

#: `meta`, as a data tier would report it for FAKE_CATALOG's tables.
#:
#: `quotes.bid_prices` is "F", not a blank, and that is deliberate: q reports
#: a nested column with an UPPERCASE character once the table has rows, and a
#: blank only while it is still empty. A fixture carrying the blank tests the
#: state no live stack is ever in, and hid a bug that made the whole catalog
#: unbuildable against a populated quotes. `ask_sizes` keeps the blank so both
#: spellings stay covered.
FAKE_SCHEMA: list[dict[str, str]] = [
    {"table": "trades", "column": "time", "kind": "p"},
    {"table": "trades", "column": "sym", "kind": "s"},
    {"table": "trades", "column": "side", "kind": "j"},
    {"table": "trades", "column": "trade_price", "kind": "f"},
    {"table": "trades", "column": "size", "kind": "f"},
    {"table": "trades", "column": "pip_factor", "kind": "f"},
    {"table": "quotes", "column": "time", "kind": "p"},
    {"table": "quotes", "column": "sym", "kind": "s"},
    {"table": "quotes", "column": "bid_prices", "kind": "F"},
    {"table": "quotes", "column": "bid_sizes", "kind": "F"},
    {"table": "quotes", "column": "ask_prices", "kind": "F"},
    {"table": "quotes", "column": "ask_sizes", "kind": " "},
    {"table": "position", "column": "time", "kind": "p"},
    {"table": "position", "column": "sym", "kind": "s"},
    {"table": "position", "column": "qty", "kind": "f"},
    {"table": "etl_coverage", "column": "dataset", "kind": "s"},
    {"table": "etl_coverage", "column": "partition", "kind": "s"},
    {"table": "etl_coverage", "column": "source_version", "kind": "s"},
    {"table": "etl_coverage", "column": "range_from", "kind": "p"},
    {"table": "etl_coverage", "column": "range_to", "kind": "p"},
    {"table": "etl_coverage", "column": "rows_published", "kind": "j"},
    {"table": "etl_coverage", "column": "recorded_at", "kind": "p"},
    {"table": "etl_coverage", "column": "superseded_at", "kind": "p"},
    {"table": "etl_coverage", "column": "run_id", "kind": "g"},
]


class FakeGateway:
    """An in-process :class:`Gateway` for tests.

    Records every call so a test can assert on *what was sent*, which is the
    property that matters for FE-14: the program text must be one of this
    package's own constants, and the caller's values must appear only in the
    argument list.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        from uqf_frontend import queries

        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.routed: list[tuple[str, tuple[Any, ...], list[str]]] = []
        # The catalog answers are defaults, not overrides: a test that stages
        # its own - an empty catalog, a table that exists but is undescribed -
        # still gets exactly what it asked for.
        self._responses = {
            queries.CATALOG: FAKE_CATALOG,
            queries.SCHEMA: FAKE_SCHEMA,
            **(responses or {}),
        }
        self.raises: Exception | None = None

    def call(self, program: str, *args: Any) -> Any:
        self.calls.append((program, args))
        if self.raises is not None:
            raise self.raises
        return self._responses.get(program)

    def route(self, program: str, args: tuple[Any, ...], tiers: list[str]) -> Any:
        self.routed.append((program, args, tiers))
        if self.raises is not None:
            raise self.raises
        return self._responses.get(program)

    @property
    def last_program(self) -> str:
        return self.routed[-1][0] if self.routed else self.calls[-1][0]

    @property
    def last_args(self) -> tuple[Any, ...]:
        return self.routed[-1][1] if self.routed else self.calls[-1][1]

    @property
    def last_tiers(self) -> list[str]:
        return self.routed[-1][2]
