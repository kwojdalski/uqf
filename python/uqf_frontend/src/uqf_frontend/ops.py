"""Operational views: the gateway's own state, and the fleet's query log.

All three sources already exist and need no q-side work (see the access-path
table in docs/frontend-requirements.md). Two of them are read from the
gateway *process itself* rather than routed to a backend tier, which still
respects the gateway-only query boundary - the gateway is the thing being
asked about.

Poll-only throughout, per F-10: none of these has a subscribe mechanism to a
browser, so cadence is the caller's choice. Suggested cadences are attached
to each view rather than hardcoded, since F-10's consequence is that the UI
decides.
"""

from __future__ import annotations

from typing import Any

#: Pending and running queries on the gateway, with the status it derives
#: from a null submittime. Read on the gateway itself.
QUEUE = ".gw.getqueue[]"

#: Registered backend handles and their up/in-use state.
#:
#: Two shape fixes, both found against a live process:
#: ``.gw.servers`` is keyed by serverid, so it is unkeyed for a flat JSON
#: projection; and its ``attributes`` column holds a **dict per server**,
#: which kola cannot serialise ("Not supported nested list - k type 99").
#: Dropping that one column is what makes the rest of the table readable -
#: the alternative is the whole view failing because of a field no ops
#: dashboard displays anyway.
SERVERS = "delete attributes from 0!.gw.servers"

#: Currently connected clients.
CLIENTS = ".gw.clients"

#: This process's own query log, newest first, capped.
#:
#: `.usage.usage` is per-process with no fleet-wide rollup (F-04), so this is
#: fanned out by :class:`uqf_frontend.fleet.Fleet` and merged here.
USAGE = """{[lim]
  r:`time xdesc .usage.usage;
  $[lim>0; lim sublist r; r]}"""

#: Rows newer than a watermark, oldest first - the capture query for F-13.
#:
#: Strictly greater than the watermark so a row already captured is never
#: captured twice, which makes the capture idempotent under retry.
USAGE_SINCE = """{[since;lim]
  r:`time xasc select from .usage.usage where time>since;
  $[lim>0; lim sublist r; r]}"""

#: How long this process keeps usage rows in memory before flushing them.
#:
#: Worth reading rather than assuming: the vendored default is `0D03` - three
#: hours - not the one day the frontend requirements state. A capture pipeline
#: sized for a day would lose most of the log.
FLUSHTIME = "value `.usage.flushtime"

#: What a process can say about itself over IPC.
#:
#: This is what makes fleet health work without shelling out to torq.sh and
#: without inspecting local OS processes - and therefore without caring
#: whether the process is on this machine (F-22's open question). ``.z.i`` is
#: the pid and ``system"p"`` the listening port; ``.proc.procname`` and
#: ``.proc.proctype`` are set by TorQ from its own command line.
#:
#: ``.proc`` is absent on a plain q process, so both reads are trapped and
#: fall back to `unknown rather than failing the probe. Returns a one-row
#: table rather than a dict so it projects the same way as every other view.
#:
#: An *expression*, not a ``{[] ...}`` lambda, for the same reason as
#: :data:`uqf_frontend.queries.PING`: sending a niladic lambda with no
#: arguments makes q return the function itself, which kola cannot
#: deserialise ("Not supported k type 100"). Second time this bit, hence the
#: test asserting no program in either module is a bare niladic lambda.
IDENTITY = (
    '([] pid:enlist .z.i; port:enlist "j"$system"p"; '
    "procname:enlist @[{.proc.procname};::;`unknown]; "
    "proctype:enlist @[{.proc.proctype};::;`unknown])"
)

#: Suggested poll intervals in seconds. Ops state changes fast; coverage and
#: analytics move at their own publish cadence (F-10, and the refresh-cadence
#: note in the requirements).
POLL_SECONDS: dict[str, int] = {
    "queue": 2,
    "connections": 5,
    "usage": 10,
    "coverage": 60,
    "processes": 5,
}


def merge_usage(results: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Flatten per-process usage into one fleet-wide log, newest first.

    Returns the merged rows and a list of the processes that could not be
    reached, so a caller can render nine processes and name the tenth rather
    than showing an empty log as if the fleet were idle.

    Each row is tagged with the process it came from, because `.usage.usage`
    carries `procname` but a row read from a process that mislabels itself
    would otherwise be indistinguishable.
    """
    rows: list[dict[str, Any]] = []
    unreachable: list[dict[str, str]] = []

    for result in results:
        if not result.ok:
            unreachable.append({"process": result.process, "error": result.error or "unknown"})
            continue
        for row in _as_rows(result.value):
            rows.append({"source_process": result.process, **row})

    rows.sort(key=lambda r: r.get("time") or "", reverse=True)
    return rows, unreachable


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dicts"):
        return value.to_dicts()
    if isinstance(value, list):
        return value
    return []
