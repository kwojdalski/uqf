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
from collections.abc import Iterable

from uqs.logger import get_logger
from uqs.model.pipelines import _pipeline_rows
from uqs.model.runtime_members import pipeline_procnames
from uqs.paths import UqsPaths, UqsUnknownProcess
from uqs.stack import housekeeping
from uqs.stack import overrides as override_files
from uqs.stack.carriable import refuse_unsafe_override
from uqs.stack.compression import with_cmp
from uqs.stack.env import build_env
from uqs.stack.monitor_budget import (
    MONITOR_QUIET_EXTRAS,
    monitor_connection_extras,
    monitor_dropped,
)
from uqs.stack.placeholders import resolve_process_config
from uqs.stack.site_fields import declared, refuse_unknown_field

log = get_logger()

# Vendored rows this tree deliberately starts with the stack, against the
# upstream default. One entry so far:
#
#   monitor1 - TorQ ships it startwithall=0, so out of the box nothing runs
#   `.hb.checkheartbeat` and `.hb.hb` is a table nobody fills. Every process
#   still PUBLISHES its heartbeat regardless; the collector is the missing
#   half. `uqs status`'s Heartbeat column, and the frontend health
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
# `uqs config set monitor1 startwithall=0`.
VENDORED_STARTWITHALL_OVERLAY = {"monitor1": "1", "feed1": "0"}

#: Extra q a vendored process loads, as (before, after) its vendored `load`
#: column: each path is pathed through an env var torq.sh's envsubst expands.
#:
#: gateway1 - the desk catalog, after. `.qcat` is the desk catalog's authored half - what
#: each table is for, and which are deliberately not browsable. It has to answer on the
#: process the BFF already talks to, and that is the gateway: `Gateway.call` runs a program
#: on the gateway process itself, while `route` reaches the data tiers. The columns and
#: types come from the tiers via `meta`; only the prose lives here.
#:
#: Not the generated database.q, which would have been the obvious carrier:
#: only stp1 is given `-schemafile`, so nothing else would see it.
#:
#: The same overlay reasoning as VENDORED_STARTWITHALL_OVERLAY above - the
#: vendored row says `${KDBCODE}/processes/gateway.q` and this appends to it
#: rather than replacing it, because the `load` column takes a
#: space-separated list and TorQ loads it in order.
#:
#: gateway1 also loads the coverage ledger's reader and its arithmetic -
#: status.q and backfill_state.q (where the ledger file is, and its durable
#: read), intervals.q and materialisation.q (.qetl.coverage). The ledger is a
#: file the bounded workers persist, held by no RDB or HDB, so the frontend's
#: /coverage reads it here (.qetl.coverage.claims, #1081) and composes and
#: gaps it here rather than keeping a second implementation in Python.
#:
#: hdb1 and dqe1 - the metatables (docs/guides/metatables.md). DQE sends
#: `.dqe.uqf_metatable` to hdb1 by value and it runs there, so `.qf.util` must be
#: loaded on hdb1, after its database; dqe1 loads the adapter and `.qf.util`
#: after its own script, and uqs_dqe_config.q BEFORE it, because dqe.q reads
#: `.dqe.configcsv` once as it loads (stack/dqe.py). hdb2 is not a DQE target.
#:
#: hdb1 also loads mapped_books.q and mapped_depth.q (a markout's as-of join runs where the
#: partitions are mapped); wdb1, sort1, sortworker1/2 load uqs_row_ids.q (#2288).
#:
#: reporter1, housekeeping1 - uqs_reporter_config.q / housekeeping.LOAD_BEFORE (#2150) BEFORE
#: reporter.q / housekeeping.q, each reading `.rp.inputcsv` / `.hk.inputcsv` once as it loads,
#: after every settings layer (stack/reporter.py #1209, stack/housekeeping.py #2155).
ROW_IDS_LOAD = "${UQF_SCRIPTS}/processes/uqs_row_ids.q"
VENDORED_LOAD_OVERLAY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "gateway1": (
        (),
        (
            "${UQF_ROOT}/src/etl/core/status.q",
            "${UQF_ROOT}/src/etl/core/backfill_state.q",
            "${UQF_ROOT}/src/etl/core/intervals.q",
            "${UQF_ROOT}/src/etl/core/materialisation.q",
            "${UQF_SCRIPTS}/processes/uqs_catalog.q",
        ),
    ),
    "hdb1": (
        (),
        (
            "${UQF_ROOT}/src/metadata/metatables.q",
            "${UQF_ROOT}/src/etl/core/mapped_books.q",
            "${UQF_ROOT}/src/etl/core/mapped_depth.q",
        ),
    ),
    "dqe1": (
        ("${UQF_SCRIPTS}/processes/uqs_dqe_config.q",),
        ("${UQF_ROOT}/src/metadata/metatables.q", "${UQF_SCRIPTS}/processes/torq_metatables.q"),
    ),
    "reporter1": (("${UQF_SCRIPTS}/processes/uqs_reporter_config.q",), ()),
    **{p: ((), (ROW_IDS_LOAD,)) for p in ("wdb1", "sort1", "sortworker1", "sortworker2")},
    "housekeeping1": with_cmp((housekeeping.LOAD_BEFORE, ())),
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
#: On PeachQ an hdb maps no partitioned database (no .Q.pv/.Q.pd), so TorQ's
#: `meta` there sees no table, .dataaccess.init finds every listed one
#: "Missing table from HDB in schema" and the hdb exits 3 while loading -
#: which `uqs start` then reports (#2180). It could serve none of them
#: anyway, so a PeachQ runtime's hdb starts without the data-access API (#2281).
PEACHQ_NO_DATAACCESS = frozenset({"hdb"})
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
            if row["proctype"] in DATAACCESS_PROCTYPES and not (
                declared.interpreter == "peachq" and row["proctype"] in PEACHQ_NO_DATAACCESS
            ):
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
    paths: UqsPaths, default_qcmd: str | None = None, selected: Iterable[str] | None = None
) -> list[dict[str, str]]:
    """process.csv as torq.sh starts from it - THE one place it is composed.

    The vendored rows with their overlays, the pipelines appended, then the
    operator's process_overrides.csv applied field by field, and only THEN
    monitor1's connection budget, decided against that effective fleet - or
    against `selected`, the set a start brings up (stack/deployment.py, #1318).

    It used to be decided before the overrides, inside the composition, and
    five call sites re-merged the overrides themselves (#624): so `uqs config
    set X startwithall 1` never reached the plan that decides which heartbeat
    subscriptions monitor1 drops under the licence cap. An override of
    monitor1's own `extras` still wins outright, as it always did.

    A declared site column (stack/site_fields.py) is on every row, empty
    unless an override sets it, so a site launcher always finds it (#1191).
    """
    overrides = _checked_overrides(paths)
    site = dict.fromkeys(declared(), "")
    rows = [
        {**row, **site, **overrides.get(row["procname"], {})}
        for row in _defaulted(paths, default_qcmd)
    ]
    if not paths.runtime_declaration.overlays:
        # monitor1 subscribes to the vendored list, as the starter pack ships
        # it: the budget below exists for this tree's extra subscriptions.
        return rows
    for row in rows:
        if row["procname"] == "monitor1" and "extras" not in overrides.get("monitor1", {}):
            extras = monitor_connection_extras(paths, rows, selected)
            row["extras"] = " ".join(x for x in (row["extras"], extras, MONITOR_QUIET_EXTRAS) if x)
    return rows


def monitor_dropped_proctypes(paths: UqsPaths, selected: Iterable[str] | None = None) -> list[str]:
    """The proctypes monitor1 gives up under the connection budget, for the
    fleet `selected` brings up (as effective_process_rows, #1318) - [] when
    nothing is dropped, when monitor1 is not in that fleet, or when an override
    of monitor1's own `extras` replaces the plan (it wins outright, as there).
    """
    overrides = _read_overrides(paths)
    if "extras" in overrides.get("monitor1", {}):
        return []
    rows = [{**row, **overrides.get(row["procname"], {})} for row in _composed_rows(paths)]
    return monitor_dropped(paths, rows, selected)


def _read_overrides(paths: UqsPaths) -> dict[str, dict[str, str]]:
    """Effective bundle rows followed by this host's process overrides."""
    return override_files.effective(paths)


def _checked_overrides(paths: UqsPaths) -> dict[str, dict[str, str]]:
    """The overrides on disk, each refused by name if torq.sh's eval would run
    it - one written by hand, or before set_process_config refused it (#1041) -
    or names a column process.csv lacks, such as an undeclared site one (#1191)."""
    overrides = _read_overrides(paths)
    for procname, fields in overrides.items():
        for field, value in fields.items():
            refuse_unknown_field(field, f"{paths.overrides_path} sets {procname}.{field}")
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


def unknown_processes(names: list[str] | str, then: str = "") -> UqsUnknownProcess:
    """The one refusal for a process name this runtime does not declare (#1885):
    the names asked for and where the real ones are - it was phrased four ways,
    two dumping all 65 names as a Python list. `then` says what was not done."""
    names = [names] if isinstance(names, str) else names
    word = "process" if len(names) == 1 else "processes"
    then = f". {then}" if then else ""
    return UqsUnknownProcess(
        f"no {word} {', '.join(names)} in this runtime - `uqs process list` names them{then}"
    )


def assert_known_procnames(paths: UqsPaths, procs: str) -> None:
    """Refuse a lifecycle selector naming a process that does not exist.

    `start`/`stop`/`restart`/`start --dry-run` used to hand `procs` straight to the
    vendored torq.sh, which handles an unknown name badly: `uqs start xyz`
    printed a licence-cap warning counting the nonexistent process, torq.sh's
    noise, then `xyz failed - unavailable processname` - and **exited 0**, so
    nothing scripting this could tell the typo from a successful start. Checking
    here costs one read of the process table. `all` passes through untouched:
    torq.sh's own selector for the startwithall rows, not a process.

    Deliberately NOT a resolver: `all` has to reach torq.sh as the literal word.
    `resolve_procnames` in stack/logs.py is the sibling that DOES expand.
    """
    if procs.strip() == "all":
        return
    known = list_process_names(paths)
    unknown = [name for name in procs.split() if name not in known]
    if unknown:
        raise unknown_processes(unknown, "Nothing was started or stopped.")


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
        raise unknown_processes(procname)
    row = dict(rows[procname])
    if resolve:
        row = resolve_process_config(row, build_env(paths, base_port=base_port))
    return row


def set_process_config(
    paths: UqsPaths, procname: str, field: str, value: str, dry_run: bool = False
) -> None:
    """Persist a process.csv field override for *procname*, applied by every
    later bootstrap() until changed again, keyed by its row's own procname
    (override_files.row_key). Read-modify-write against process_overrides.csv
    - the only file this touches. With `dry_run`, nothing is written.
    """
    # The process first: a name that is not there is answered as such (404 on
    # the frontend's route, #2224), whatever the field and value say.
    names = list_process_names(paths)
    if procname not in names:
        raise unknown_processes(procname)
    refuse_unknown_field(field, "unknown process.csv field")
    refuse_unsafe_override(procname, field, value)
    override_files.refuse_taken_name(names, procname, field, value)
    if dry_run:
        return
    overrides = override_files.read_file(paths.overrides_path)
    key = override_files.row_key(_read_overrides(paths), procname)
    overrides.setdefault(key, {})[field] = value
    _write_overrides(paths, overrides)
    log.info("set {}.{} = {}", procname, field, value)
