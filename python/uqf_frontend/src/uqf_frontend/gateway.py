"""The q gateway client.

Reuses the kola IPC pattern already proven twice in this repo - by
``uqf_client.UqfClient`` and by ``torq_orchestrator.core.query()`` - rather
than introducing a second mechanism, per F-16.

``Gateway`` is a Protocol so that tests run with a fake and no q process.
That is the same posture E-19 takes on the q side: double the adapters at
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
#: F-12 requires this be surfaced as a transient state, not a failure.
_RELOADING_MARKERS = ("eod", "reload", "not available")
_TIMEOUT_MARKERS = ("timeout", "timed out")


#: The backend tiers a query may be routed to. `rdb` holds today's session,
#: `hdb` the completed partitions - F-08 requires the split be explicit
#: rather than hidden, because F-11 expects hdb to be slower.
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
    poll-only frontend (F-10) where every view reconnects on a timer anyway.
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
            raise GatewayUnavailable(
                f"gateway at {s.host}:{s.port} is not reachable: {exc}"
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
    the alternative - treating every failure identically - would make F-12
    impossible to honour.
    """
    message = str(exc).lower()
    if any(m in message for m in _TIMEOUT_MARKERS):
        return QueryTimedOut(f"the gateway's own query timeout fired: {exc}")
    if any(m in message for m in _RELOADING_MARKERS):
        return GatewayReloading(f"the gateway is reloading and is refusing queries: {exc}")
    return QueryRejected(f"the gateway rejected the query: {exc}")


class FakeGateway:
    """An in-process :class:`Gateway` for tests.

    Records every call so a test can assert on *what was sent*, which is the
    property that matters for F-14: the program text must be one of this
    package's own constants, and the caller's values must appear only in the
    argument list.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.routed: list[tuple[str, tuple[Any, ...], list[str]]] = []
        self._responses = responses or {}
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
