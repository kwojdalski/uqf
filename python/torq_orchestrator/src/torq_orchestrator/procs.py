"""process.csv composition and per-process config overrides.

The precedence, decided as question-bank H-01 and asserted by
test_core.test_the_three_process_csv_layers_compose_in_a_stated_order:

    vendored process.csv  ->  PIPELINES  ->  extra_processes.csv  (appended)
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

from torq_orchestrator.env import build_env
from torq_orchestrator.logger import get_logger
from torq_orchestrator.paths import TorqDemoError, TorqDemoPaths
from torq_orchestrator.pipelines import (
    DEFAULT_BASE_PORT,
    PIPELINES,
    PROCESS_CSV_FIELDS,
    _pipeline_rows,
)
from torq_orchestrator.schemas import (
    CRYPTO_BOOK_TABLE_SCHEMA,
    CRYPTO_SIM_FILLS_TABLE_SCHEMA,
    CRYPTO_TRADES_TABLE_SCHEMA,
)

log = get_logger(__name__)

# Vendored rows this tree deliberately starts with the stack, against the
# upstream default. One entry so far:
#
#   monitor1 - TorQ ships it startwithall=0, so out of the box nothing runs
#   `.hb.checkheartbeat` and `.hb.hb` is a table nobody fills. Every process
#   still PUBLISHES its heartbeat regardless; the collector is the missing
#   half. `torq-demo summary`'s Heartbeat column, and the frontend health
#   view behind it, therefore reported "not collected" on a fully healthy
#   stack - a monitoring surface that is only ever populated if an operator
#   knows to start one more process by hand is not monitoring.
#
# This is an overlay, not an edit: the vendored file is never touched (H-01),
# and because process_overrides.csv is still applied afterwards, an operator
# who does want the upstream behaviour can put it back with
# `torq-demo config-set monitor1 startwithall 0`.
VENDORED_STARTWITHALL_OVERLAY = {"monitor1": "1"}

# Proctypes monitor1 must also subscribe to, on top of the ten the vendored
# settings file lists.
#
# Starting monitor1 is only half of collecting heartbeats. It subscribes to
# the proctypes in `.servers.CONNECTIONS`, and the vendored
# appconfig/settings/monitor.q lists TorQ's own types only - so the four
# standing uqf ETLs (cross1, vectorize1, posbook1, markout1, all proctype
# `metrics`) published heartbeats that nothing was listening for. The feeds
# were already covered, since `feed` is on the vendored list.
#
# `backfill` is deliberately NOT here. A backfill is a bounded job that
# registers, runs a window and exits (ETL-16). Its `.hb.hb` row would
# outlive it, and `checkheartbeat` would age that row into `warning` and
# then `error` - reporting a job that SUCCEEDED as a fault, permanently.
# Absence is the expected end state for a bounded worker, so the thing to
# monitor is its run record, not its heartbeat.
MONITOR_EXTRA_CONNECTIONS = ("metrics",)


def _vendored_monitor_connections(paths: TorqDemoPaths) -> list[str]:
    """The proctypes the vendored monitor settings file subscribes to.

    Parsed out rather than restated, so that if upstream adds a proctype to
    its list we extend THEIR list instead of silently pinning a copy of it
    made on the day this was written. The line looks like:

        CONNECTIONS:`discovery`rdb`hdb`...`sortworker

    Returns [] if the file or the line is not found, which makes the
    override below a no-op rather than a truncation - losing nine
    subscriptions would be a far worse failure than not adding one.
    """
    settings = paths.torqapphome / "appconfig" / "settings" / "monitor.q"
    if not settings.is_file():
        return []
    for line in settings.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("CONNECTIONS:`"):
            return [part for part in stripped[len("CONNECTIONS:") :].split("`") if part]
    return []


def _monitor_connection_extras(paths: TorqDemoPaths) -> str:
    """`.servers.CONNECTIONS` as a command-line override for monitor1.

    `.proc.override[]` runs after every config layer, including the vendored
    appconfig, so a command-line value wins without that file being edited
    (H-01). It REPLACES rather than appends, which is why the vendored list
    is read back above and passed through in full.
    """
    connections = _vendored_monitor_connections(paths)
    if not connections:
        return ""
    for proctype in MONITOR_EXTRA_CONNECTIONS:
        if proctype not in connections:
            connections.append(proctype)
    return "-.servers.CONNECTIONS " + " ".join(connections)


# ---------------------------------------------------------------------------
# process.csv rows + config overrides (get/set)
# ---------------------------------------------------------------------------


def _base_process_rows(paths: TorqDemoPaths) -> list[dict[str, str]]:
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
    for row in rows:
        # stp1 loads its schema via -schemafile in `extras`; point it at the
        # generated copy (vendored database.q + uqf's own `quotes` table -
        # see _generated_schema_content()) instead of the vendored file
        # itself, same never-edit-the-vendored-tree approach as process.csv.
        if row["procname"] == "stp1":
            row["extras"] = row["extras"].replace(
                "${TORQAPPHOME}/database.q", "${TORQDATA}/database.q"
            )
        if row["procname"] in VENDORED_STARTWITHALL_OVERLAY:
            row["startwithall"] = VENDORED_STARTWITHALL_OVERLAY[row["procname"]]
        if row["procname"] == "monitor1":
            extras = _monitor_connection_extras(paths)
            if extras:
                row["extras"] = " ".join(x for x in (row["extras"], extras) if x)
    rows.extend(_pipeline_rows())
    rows.extend(_read_extra_processes(paths))
    return rows


def _read_extra_processes(paths: TorqDemoPaths) -> list[dict[str, str]]:
    """Rows appended via add_extra_process() (the `new-process` wizard, or
    anything else) - process_overrides.csv's sibling for whole new
    processes rather than field tweaks on existing ones. Tracked in git
    like process_overrides.csv (these are meaningful, named demo
    processes someone chose to add, not scratch state); missing file ->
    no extra rows.
    """
    if not paths.extra_processes_path.is_file():
        return []
    with paths.extra_processes_path.open(newline="") as f:
        return list(csv.DictReader(f))


def next_free_port_offset(paths: TorqDemoPaths) -> int:
    """The smallest `{KDBBASEPORT}+N` offset not already used by any
    process.csv row - one past the highest one currently taken. Used by
    the `new-process` wizard so a new process never collides with an
    existing one, whatever offsets the vendored csv/fxfeed1/quotesfeed1/
    cross1/earlier wizard runs have already claimed.
    """
    taken = [0]  # {KDBBASEPORT} alone (bare stp1) counts as offset 0
    for row in _base_process_rows(paths):
        m = _BRACE_ARITH_RE.match(row["port"])
        if m and m.group(2):
            taken.append(int(m.group(2)))
    return max(taken) + 1


def add_extra_process(paths: TorqDemoPaths, row: dict[str, str]) -> None:
    """Append one new process.csv row to extra_processes.csv - the
    never-edit-the-generated-file counterpart to set_process_config()'s
    field overrides, for a whole new process rather than a tweak to an
    existing one.
    """
    if row["procname"] in list_process_names(paths):
        raise TorqDemoError(f"process {row['procname']!r} already exists")

    paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    write_header = not paths.extra_processes_path.is_file()
    with paths.extra_processes_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROCESS_CSV_FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in PROCESS_CSV_FIELDS})
    log.info("added process {} ({})", row["procname"], row["proctype"])


def add_extra_table_schema(paths: TorqDemoPaths, table_def: str) -> None:
    """Append one q table definition line (e.g. 'mytable:([]time:...;
    sym:...)') to extra_schema.q - _generated_schema_content()'s
    extension point for the `new-process` wizard, the same
    generate-never-edit-vendored approach as everything else here.
    """
    paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    with paths.extra_schema_path.open("a") as f:
        f.write(table_def.rstrip("\n") + "\n")


def _generated_schema_content(paths: TorqDemoPaths) -> str:
    """The vendored database.q's tables, plus uqf's own `quotes`/`wide_book`/
    `mkt_orderbook`/`crypto_book` tables and any add_extra_table_schema()
    additions (extra_schema.q) appended - never edited in place, always
    read fresh from the vendored file. stp1's process.csv row (see
    _base_process_rows) is pointed at the generated copy this produces
    rather than the vendored file.
    """
    vendored = (paths.torqapphome / "database.q").read_text()
    extra = paths.extra_schema_path.read_text() if paths.extra_schema_path.is_file() else ""
    # Pipeline-owned tables come from the PIPELINES registry, so a new
    # pipeline that publishes a table gets its definition here automatically.
    # The crypto tables are not pipelines - they are written by the external
    # cryptorust recorders (see start_crypto_recorder/start_crypto_fills_recorder),
    # not by any scripts/torq_*.q process - so they stay listed explicitly.
    # Definition order among independent table declarations is immaterial to
    # q, which is why grouping them this way is safe.
    definitions = [p.schema for p in PIPELINES if p.schema is not None] + [
        CRYPTO_BOOK_TABLE_SCHEMA,
        CRYPTO_SIM_FILLS_TABLE_SCHEMA,
        CRYPTO_TRADES_TABLE_SCHEMA,
    ]
    return vendored.rstrip("\n") + "\n" + "".join(d + "\n" for d in definitions) + extra


def _read_overrides(paths: TorqDemoPaths) -> dict[str, dict[str, str]]:
    """{procname: {field: value}} from process_overrides.csv, or {} if it
    doesn't exist yet (nothing has been set())."""
    if not paths.overrides_path.is_file():
        return {}
    overrides: dict[str, dict[str, str]] = {}
    with paths.overrides_path.open(newline="") as f:
        for row in csv.DictReader(f):
            overrides.setdefault(row["procname"], {})[row["field"]] = row["value"]
    return overrides


def _write_overrides(paths: TorqDemoPaths, overrides: dict[str, dict[str, str]]) -> None:
    paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    with paths.overrides_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["procname", "field", "value"], lineterminator="\n")
        writer.writeheader()
        for procname, fields in overrides.items():
            for field, value in fields.items():
                writer.writerow({"procname": procname, "field": field, "value": value})


def list_process_names(paths: TorqDemoPaths) -> list[str]:
    return [row["procname"] for row in _base_process_rows(paths)]


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
    paths: TorqDemoPaths,
    procname: str,
    base_port: int = DEFAULT_BASE_PORT,
    resolve: bool = True,
) -> dict[str, str]:
    """The effective process.csv row for *procname* - vendored/fxfeed1 values
    with any set_process_config() overrides applied on top. With
    resolve=True (the default), also evaluates ${VAR}-style and
    {VAR}(+N)-style placeholders (KDBBASEPORT, KDBHDB, UQFSCRIPTS, ...)
    against build_env(paths, base_port) - the same values torq.sh itself
    would substitute at process-start time.
    """
    rows = {row["procname"]: row for row in _base_process_rows(paths)}
    if procname not in rows:
        raise TorqDemoError(f"unknown process {procname!r} - {sorted(rows)}")
    row = dict(rows[procname])
    row.update(_read_overrides(paths).get(procname, {}))
    if resolve:
        row = resolve_process_config(row, build_env(paths, base_port=base_port))
    return row


def set_process_config(paths: TorqDemoPaths, procname: str, field: str, value: str) -> None:
    """Persist a process.csv field override for *procname*, applied by every
    later bootstrap() (i.e. every start/stop/summary/... call) until
    changed again. Read-modify-write against process_overrides.csv - the
    only file this touches; the vendored process.csv is never edited.
    """
    if field not in PROCESS_CSV_FIELDS:
        raise TorqDemoError(f"unknown process.csv field {field!r} - {PROCESS_CSV_FIELDS}")
    if procname not in list_process_names(paths):
        raise TorqDemoError(f"unknown process {procname!r} - {list_process_names(paths)}")

    overrides = _read_overrides(paths)
    overrides.setdefault(procname, {})[field] = value
    _write_overrides(paths, overrides)
    log.info("set {}.{} = {}", procname, field, value)
