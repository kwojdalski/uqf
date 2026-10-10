"""process.csv composition and per-process config overrides.

The precedence, decided as the question bank and asserted by
test_core.test_the_process_csv_layers_compose_in_a_stated_order:

    vendored process.csv  ->  PIPELINES  (appended)
    then process_overrides.csv applied LAST, per procname, field by field

with one field-level overlay on the vendored rows themselves
(VENDORED_STARTWITHALL_OVERLAY, below) sitting at the first step, since it
stands in for editing a file this tree may not edit.

So an override wins over every other source, and the vendored file is never
edited - every run regenerates the merged copy from scratch."""

from __future__ import annotations

import csv
import re
from string import Template

from uqs.logger import get_logger
from uqs.model.pipelines import PROCESS_CSV_FIELDS, _pipeline_rows
from uqs.model.runtime_members import pipeline_procnames
from uqs.paths import UqsError, UqsPaths
from uqs.stack.carriable import refuse_unsafe_override
from uqs.stack.env import build_env
from uqs.stack.monitor_budget import (
    MONITOR_QUIET_EXTRAS,
    monitor_connection_extras,
    monitor_dropped,
)

log = get_logger(__name__)

# Vendored rows this tree deliberately starts with the stack, against the
# upstream default. One entry so far:
#
#   monitor1 - TorQ ships it startwithall=0, so out of the box nothing runs
#   `.hb.checkheartbeat` and `.hb.hb` is a table nobody fills. Every process
#   still PUBLISHES its heartbeat regardless; the collector is the missing
#   half. `uqs summary`'s Heartbeat column, and the frontend health
#   view behind it, therefore reported "not collected" on a fully healthy
#   stack - a monitoring surface that is only ever populated if an operator
#   knows to start one more process by hand is not monitoring.
#
#   feed1 - the starter pack's random demo feed, turned OFF. It publishes
#   `trade` and `quote`, and this tree has its own producer for `quote`
#   (fxfeed1) built from the FX curve rather than from `rand`. Keeping both
#   meant two feeds interleaving rows into one table, and it cost one of the
#   sixteen inbound connections the licence allows a q process - which is the
#   budget that decides whether fxpositions1 and executions1 can reach the
#   plant at all (#285, LICENCE_CONNECTION_LIMIT). Start it by hand for a
#   vendored TorQ demo.
#
# This is an overlay, not an edit: the vendored file is never touched,
# and because process_overrides.csv is still applied afterwards, an operator
# who does want the upstream behaviour can put it back with
# `uqs config set monitor1 startwithall 0`.
VENDORED_STARTWITHALL_OVERLAY = {"monitor1": "1", "feed1": "0"}

#: Extra q a vendored process loads, as (before, after) its vendored `load`
#: column: each path is pathed through an env var torq.sh's envsubst expands.
#:
#: gateway1 - the desk catalog, after.
#: `.qcat` is the desk catalog's authored half - what each table is for, and
#: which are deliberately not browsable. It has to answer on the process the
#: BFF already talks to, and that is the gateway: `Gateway.call` runs a
#: program on the gateway process itself, while `route` reaches the data
#: tiers. The columns and types come from the tiers via `meta`; only the
#: prose lives here.
#:
#: Not the generated database.q, which would have been the obvious carrier:
#: only stp1 is given `-schemafile`, so nothing else would see it.
#:
#: The same overlay reasoning as VENDORED_STARTWITHALL_OVERLAY above - the
#: vendored row says `${KDBCODE}/processes/gateway.q` and this appends to it
#: rather than replacing it, because the `load` column takes a
#: space-separated list and TorQ loads it in order.
#:
#: gateway1 also loads src/etl/core/intervals.q - the coverage ledger's
#: interval arithmetic and nothing else. The frontend's /coverage reads
#: etl_coverage from both tiers, and only the gateway holds both halves, so
#: it asks the gateway to compose them and find the gaps rather than keeping
#: a second implementation of that rule in Python.
#:
#: hdb1 and dqe1 - the metatables (docs/guides/metatables.md). DQE sends
#: `.dqe.uqf_metatable` to hdb1 by value and it runs there, so `.qmeta` must be
#: loaded on hdb1, after its database; dqe1 loads the adapter and `.qmeta`
#: after its own script, and uqs_dqe_config.q BEFORE it, because dqe.q reads
#: `.dqe.configcsv` once as it loads (stack/dqe.py). hdb2 is not a DQE target.
VENDORED_LOAD_OVERLAY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "gateway1": (
        (),
        ("${UQF_ROOT}/src/etl/core/intervals.q", "${UQF_SCRIPTS}/processes/uqs_catalog.q"),
    ),
    "hdb1": ((), ("${UQF_ROOT}/src/metadata/metatables.q",)),
    "dqe1": (
        ("${UQF_SCRIPTS}/processes/uqs_dqe_config.q",),
        ("${UQF_ROOT}/src/metadata/metatables.q", "${UQF_SCRIPTS}/processes/torq_metatables.q"),
    ),
}


#: The data-access API (TorQ's .dataaccess.getdata) on the gateway and the
#: tiers it routes to. TorQ turns it on for a process given `-dataaccess` and
#: a table-properties file; without it getdata does not exist on the
#: backends, so the gateway's routed calls fail. Every rdb and hdb, not just
#: rdb1 and hdb1, because the gateway may route to any server of a type.
#:
#: The file is the tree's own, under KDBSERVCONFIG, listing the tables the
#: gateway exposes - the query policy for each is querypolicy.csv beside it
#: (scripts/torqcode/gateway/querypolicy.q). Each runtime is started with a
#: copy holding only its own tables (stack/gateway_access.py).
DATAACCESS_PROCTYPES = frozenset({"gateway", "rdb", "hdb"})
DATAACCESS_EXTRAS = "-dataaccess ${TORQDATA}/tableproperties.csv"

#: Access lists (the `U` column, q's `-U`) written by bootstrap
#: (stack/gateway_access.py): the vendored logins plus a role's own users.
#: gateway1 adds scripts/torqconfig/permissions/gateway_users.csv - an
#: ordinary user logs in there, where .pm holds them to getdata, and nowhere
#: else, so going round the gateway is refused at login. sctp1, the chained
#: tickerplant, adds subscriber_users.csv - an outside real-time subscriber
#: (#984) subscribes there as itself, never on stp1 or a data tier.
ACCESS_LIST_OVERLAY = {
    "gateway1": "${TORQDATA}/gateway_accesslist.txt",
    "sctp1": "${TORQDATA}/subscriber_accesslist.txt",
}


def _composed_rows(paths: UqsPaths) -> list[dict[str, str]]:
    """The vendored process.csv rows, plus one row per PIPELINES entry
    appended (with stp1's -schemafile extras repointed and
    VENDORED_STARTWITHALL_OVERLAY applied) - the FILE is never mutated,
    always read fresh from the vendored file. The nine uqf rows used to be
    nine literal dicts here; they are generated by _pipeline_rows() now, so
    a new pipeline is a Pipeline() entry rather than an edit to this
    function.
    """
    vendored_procs = paths.torqapphome / "appconfig" / "process.csv"
    with vendored_procs.open(newline="") as f:
        rows = list(csv.DictReader(f))
    # Without overlays the vendored rows run as shipped - feed1 on, monitor1
    # off, stp1 on the vendored database.q - and without pipelines nothing is
    # appended: the torq runtime has neither.
    declared = paths.runtime_declaration
    if declared.overlays:
        for row in rows:
            if row["procname"] in VENDORED_STARTWITHALL_OVERLAY:
                row["startwithall"] = VENDORED_STARTWITHALL_OVERLAY[row["procname"]]
            if row["procname"] in VENDORED_LOAD_OVERLAY:
                before, after = VENDORED_LOAD_OVERLAY[row["procname"]]
                row["load"] = " ".join(x for x in (*before, row["load"], *after) if x)
            if row["proctype"] in DATAACCESS_PROCTYPES:
                row["extras"] = " ".join(x for x in (row["extras"], DATAACCESS_EXTRAS) if x)
            if row["procname"] in ACCESS_LIST_OVERLAY:
                row["U"] = ACCESS_LIST_OVERLAY[row["procname"]]
        for row in rows:
            # stp1 loads its schema via -schemafile in `extras`; point it at the
            # generated copy (vendored database.q + uqf's own `fx_orderbook` table -
            # see _generated_schema_content()) instead of the vendored file
            # itself, same never-edit-the-vendored-tree approach as process.csv.
            if row["procname"] == "stp1":
                row["extras"] = row["extras"].replace(
                    "${TORQAPPHOME}/database.q", "${TORQDATA}/database.q"
                )
    if declared.pipelines:
        members = pipeline_procnames(declared)
        rows.extend(r for r in _pipeline_rows() if members is None or r["procname"] in members)
    return rows


def _defaulted(paths: UqsPaths, default_qcmd: str | None) -> list[dict[str, str]]:
    """The composed rows, each default interpreter - a missing or empty qcmd,
    or TorQ's own `q` spelled out - set to *default_qcmd* when one is given.
    Before the overrides, so an operator's, even to bare `q`, still wins."""
    rows = _composed_rows(paths)
    if default_qcmd is None:
        return rows
    return [
        {**r, "qcmd": default_qcmd if (r.get("qcmd") or "q") == "q" else r["qcmd"]} for r in rows
    ]


def effective_process_rows(
    paths: UqsPaths, default_qcmd: str | None = None
) -> list[dict[str, str]]:
    """process.csv as torq.sh starts from it - THE one place it is composed.

    The vendored rows with their overlays, the pipelines appended, then the
    operator's process_overrides.csv applied field by field, and only THEN
    monitor1's connection budget, decided against that effective fleet.

    It used to be decided before the overrides, inside the composition, and
    five call sites re-merged the overrides themselves (#624): so `uqs config
    set X startwithall 1` never reached the plan that decides which heartbeat
    subscriptions monitor1 drops under the licence cap. An override of
    monitor1's own `extras` still wins outright, as it always did.
    """
    overrides = _checked_overrides(paths)
    rows = [
        {**row, **overrides.get(row["procname"], {})} for row in _defaulted(paths, default_qcmd)
    ]
    if not paths.runtime_declaration.overlays:
        # monitor1 subscribes to the vendored list, as the starter pack ships
        # it: the budget below exists for this tree's extra subscriptions.
        return rows
    for row in rows:
        if row["procname"] == "monitor1" and "extras" not in overrides.get("monitor1", {}):
            extras = monitor_connection_extras(paths, rows)
            row["extras"] = " ".join(x for x in (row["extras"], extras, MONITOR_QUIET_EXTRAS) if x)
    return rows


def monitor_dropped_proctypes(paths: UqsPaths) -> list[str]:
    """The proctypes monitor1 gives up under the connection budget, for this
    fleet as torq.sh will start it - [] when nothing is dropped, when monitor1
    does not start with the stack, or when an override of monitor1's own
    `extras` replaces the plan (it wins outright, as in effective_process_rows).
    """
    overrides = _read_overrides(paths)
    if "extras" in overrides.get("monitor1", {}):
        return []
    rows = [{**row, **overrides.get(row["procname"], {})} for row in _composed_rows(paths)]
    if not any(r["procname"] == "monitor1" and r.get("startwithall") == "1" for r in rows):
        return []
    return monitor_dropped(paths, rows)


def _read_overrides(paths: UqsPaths) -> dict[str, dict[str, str]]:
    """{procname: {field: value}} from process_overrides.csv, or {} if it
    doesn't exist yet (nothing has been set())."""
    if not paths.overrides_path.is_file():
        return {}
    overrides: dict[str, dict[str, str]] = {}
    with paths.overrides_path.open(newline="") as f:
        for row in csv.DictReader(f):
            overrides.setdefault(row["procname"], {})[row["field"]] = row["value"]
    return overrides


def _checked_overrides(paths: UqsPaths) -> dict[str, dict[str, str]]:
    """The overrides on disk, each refused by name if torq.sh's eval would run
    it - one written by hand, or before set_process_config refused it (#1041)."""
    overrides = _read_overrides(paths)
    for procname, fields in overrides.items():
        for field, value in fields.items():
            refuse_unsafe_override(procname, field, value or "")
    return overrides


def _write_overrides(paths: UqsPaths, overrides: dict[str, dict[str, str]]) -> None:
    paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    with paths.overrides_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["procname", "field", "value"], lineterminator="\n")
        writer.writeheader()
        for procname, fields in overrides.items():
            for field, value in fields.items():
                writer.writerow({"procname": procname, "field": field, "value": value})


def list_process_names(paths: UqsPaths) -> list[str]:
    return [row["procname"] for row in effective_process_rows(paths)]


def assert_known_procnames(paths: UqsPaths, procs: str) -> None:
    """Refuse a lifecycle selector naming a process that does not exist.

    `start`/`stop`/`restart`/`start --print` used to hand `procs` straight to the
    vendored torq.sh on the grounds that it owns its own handling of an
    unknown name. It does, but badly, and the cost is paid before you see it:
    `uqs start xyz` first printed a licence-cap warning whose arithmetic
    counted the nonexistent process, then the vendored script's own
    `hostname: illegal option` noise, then `xyz failed - unavailable
    processname` - and **exited 0**, so nothing scripting this could tell the
    typo from a successful start.

    Checking here costs one read of the process table and turns all of that
    into one line before any work happens. `all` is passed through untouched:
    it is torq.sh's own selector for the startwithall rows, not a process.

    Deliberately NOT a resolver - it returns nothing and rewrites nothing,
    because `all` has to reach torq.sh as the literal word. `resolve_procnames`
    in stack/logs.py is the sibling that DOES expand, because the log commands
    need a concrete file list.
    """
    if procs.strip() == "all":
        return
    known = list_process_names(paths)
    unknown = [name for name in procs.split() if name not in known]
    if unknown:
        raise UqsError(
            f"unknown process(es) {unknown} - known processes are {sorted(known)}. "
            "Nothing was started or stopped."
        )


def list_process_choices(paths: UqsPaths) -> list[dict[str, str]]:
    """Every process a lifecycle selector may name, with what a picker shows.

    procname, proctype and startwithall, from the same effective rows torq.sh
    starts from - vendored process.csv and the pipelines -
    with process_overrides.csv applied, so a startwithall a user set through
    config set is the one reported. Nothing is resolved beyond that: a picker
    needs to know what CAN be started and which are started by "all", not
    what port each would take.
    """
    out: list[dict[str, str]] = []
    for merged in effective_process_rows(paths):
        out.append(
            {
                "procname": merged["procname"],
                "proctype": merged["proctype"],
                "startwithall": merged.get("startwithall", ""),
            }
        )
    return out


# process.csv's two placeholder styles: `${VAR}` / `$VAR` (shell parameter
# expansion, resolved via envsubst at runtime - handled here by
# string.Template, which uses the same syntax) inside e.g. `load`/`U`/
# `extras`; and `{VAR}` / `{VAR}+N` / `{VAR}-N` (no `$`, only ever in
# `port` - resolved at runtime by torq.sh stripping the braces and letting
# bash arithmetic-context evaluate the bare variable name plus offset).
_BRACE_ARITH_RE = re.compile(r"^\{(\w+)\}([+-]\d+)?$")


def _resolve_value(value: str, env: dict[str, str]) -> str:
    m = _BRACE_ARITH_RE.match(value)
    if m:
        var, offset = m.groups()
        if var in env and env[var].lstrip("-").isdigit():
            return str(int(env[var]) + int(offset or 0))
        return value  # unknown/non-numeric var - leave the placeholder as-is
    return Template(value).safe_substitute(env)


def resolve_process_config(row: dict[str, str], env: dict[str, str]) -> dict[str, str]:
    return {field: _resolve_value(value, env) for field, value in row.items()}


def get_process_config(
    paths: UqsPaths,
    procname: str,
    base_port: int | None = None,
    resolve: bool = True,
) -> dict[str, str]:
    """The effective process.csv row for *procname* - vendored/fxfeed1 values
    with any set_process_config() overrides applied on top. With
    resolve=True (the default), also evaluates ${VAR}-style and
    {VAR}(+N)-style placeholders (KDBBASEPORT, KDBHDB, UQF_SCRIPTS, ...)
    against build_env(paths, base_port) - the same values torq.sh itself
    would substitute at process-start time.
    """
    rows = {row["procname"]: row for row in effective_process_rows(paths)}
    if procname not in rows:
        raise UqsError(f"unknown process {procname!r} - {sorted(rows)}")
    row = dict(rows[procname])
    if resolve:
        row = resolve_process_config(row, build_env(paths, base_port=base_port))
    return row


def set_process_config(paths: UqsPaths, procname: str, field: str, value: str) -> None:
    """Persist a process.csv field override for *procname*, applied by every
    later bootstrap() (i.e. every start/stop/summary/... call) until
    changed again. Read-modify-write against process_overrides.csv - the
    only file this touches; the vendored process.csv is never edited.
    """
    if field not in PROCESS_CSV_FIELDS:
        raise UqsError(f"unknown process.csv field {field!r} - {sorted(PROCESS_CSV_FIELDS)}")
    if procname not in list_process_names(paths):
        raise UqsError(f"unknown process {procname!r} - {sorted(list_process_names(paths))}")
    refuse_unsafe_override(procname, field, value)

    overrides = _read_overrides(paths)
    overrides.setdefault(procname, {})[field] = value
    _write_overrides(paths, overrides)
    log.info("set {}.{} = {}", procname, field, value)
