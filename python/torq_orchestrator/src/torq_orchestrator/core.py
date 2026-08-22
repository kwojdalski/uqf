"""Shared business logic for running the vendored TorQ Finance Starter Pack
demo (see docs/torq-demo.md) - bootstrapping the writable data directory,
generating process.csv/setenv.sh, driving lib/torq/torq.sh, and reading/
writing per-process config overrides.

Pure logic, no CLI/MCP framework concerns - ../../torq_demo.py (Typer) and
../../torq_demo_mcp.py (FastMCP) both import from here so the two front
ends can't drift out of sync. Same env-var bridging approach throughout
(TorQ's own installtorqapp.sh convention: point TORQHOME/TORQAPPHOME/etc
at wherever the framework and app live, generate an overlay process.csv/
setenv.sh rather than editing either vendored tree).
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from torq_orchestrator.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE_PORT = 6050
FXFEED_PORT_OFFSET = 19  # the one offset the vendored process.csv leaves free
QUOTES_FEED_PORT_OFFSET = 24  # next free offset after the vendored dqc/dqe block (+20..+23)
CROSS_ETL_PORT_OFFSET = 25  # next free offset after quotesfeed1
WIDE_BOOK_FEED_PORT_OFFSET = 26  # next free offset after cross1
VECTORIZE_ETL_PORT_OFFSET = 27  # next free offset after widefeed1
TAP_PORT_OFFSET = 28  # next free offset after vectorize1

# Appended (never edited in place) to a *copy* of the vendored database.q -
# see _generated_schema_content(). Matches src/forwards.q's require_quotes_cols
# shape (`ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes) except `ts` is
# named `time` here (Rule S1/S3: the tickerplant's upd/.u.upd machinery
# requires the first column literally named `time`) - torq_quotes_feed.q's
# consumers rename time->ts at query time instead.
QUOTES_TABLE_SCHEMA = (
    "quotes:([]time:`timestamp$(); sym:`g#`symbol$(); "
    "bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())"
)

# Appended alongside QUOTES_TABLE_SCHEMA - a deliberately "incorrectly-
# ingested wide order book table" (src/book.q's own header comment), one
# scalar column per depth level rather than a vector column, for
# torq_wide_book_feed.q/vectorize1 (torq_vectorize_etl.q) to demonstrate
# uqf's own .qbook.book_from_wide_levels/derive_level_groups fixing it back
# into forwards.q's vector-column shape. Built rather than hand-listing 22
# column defs; bids0../asks0.. naming matches derive_level_groups'
# prefix+contiguous-digit-suffix convention exactly.
WIDE_BOOK_LEVELS = 11
WIDE_BOOK_TABLE_SCHEMA = "wide_book:([]time:`timestamp$(); sym:`g#`symbol$(); " + (
    ";".join(
        f"{prefix}{level}:`float$()"
        for prefix in ("bids", "asks")
        for level in range(WIDE_BOOK_LEVELS)
    )
    + ")"
)

# vectorize1's (torq_vectorize_etl.q) output: wide_book's bids*/asks*
# folded into bid_prices/ask_prices vector columns via uqf's own
# .qbook.book_from_wide_levels, then republished onto the tickerplant - a
# normal database table like quotes/wide_book (flows through
# rdb1/wdb1/hdb), not private state on vectorize1's own process.
MKT_ORDERBOOK_TABLE_SCHEMA = (
    "mkt_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); "
    "bid_prices:(); ask_prices:())"
)

# Destination for the external, non-TorQ cryptorust (Rust) publisher - see
# ~/github_projects/cryptorust/src/bin/kdb_market_data_recorder.rs. Not
# produced by any process.csv-registered process (no q feed involved at
# all): that binary opens its own raw kdb+ IPC handle straight to stp1's
# port and calls .u.upd directly, same wire protocol our own q feeds use,
# just from Rust. `venue` is a separate column (unlike quotes/wide_book,
# which are FX-only, single-venue-implicit) since cryptorust tracks the
# same symbol across multiple exchanges simultaneously.
CRYPTO_BOOK_TABLE_SCHEMA = (
    "crypto_book:([]time:`timestamp$(); venue:`g#`symbol$(); sym:`g#`symbol$(); "
    "bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())"
)
PROCESS_CSV_FIELDS = (
    "host",
    "port",
    "proctype",
    "procname",
    "U",
    "localtime",
    "g",
    "T",
    "w",
    "load",
    "startwithall",
    "extras",
    "qcmd",
)


class TorqDemoError(RuntimeError):
    """Raised for anything that stops the demo from being runnable as-is."""


@dataclass(frozen=True)
class TorqDemoPaths:
    repo_root: Path
    torqhome: Path
    torqapphome: Path
    torqdata: Path
    scripts_dir: Path
    orchestrator_dir: Path

    @property
    def generated_procs(self) -> Path:
        return self.torqdata / "process.csv"

    @property
    def generated_schema(self) -> Path:
        return self.torqdata / "database.q"

    @property
    def generated_setenv(self) -> Path:
        return self.torqdata / "setenv.sh"

    @property
    def overrides_path(self) -> Path:
        return self.orchestrator_dir / "process_overrides.csv"

    @property
    def extra_processes_path(self) -> Path:
        return self.orchestrator_dir / "extra_processes.csv"

    @property
    def extra_schema_path(self) -> Path:
        return self.orchestrator_dir / "extra_schema.q"

    @property
    def crypto_recorder_pid_path(self) -> Path:
        return self.orchestrator_dir / "crypto_recorder.pid"

    @property
    def crypto_recorder_config_path(self) -> Path:
        return self.torqdata / "crypto_recorder_config.yaml"


def default_paths() -> TorqDemoPaths:
    # this file: <repo_root>/python/torq_orchestrator/src/torq_orchestrator/core.py
    orchestrator_dir = Path(__file__).resolve().parents[2]
    repo_root = orchestrator_dir.parents[1]
    return TorqDemoPaths(
        repo_root=repo_root,
        torqhome=repo_root / "lib" / "torq",
        torqapphome=repo_root / "lib" / "torq-finance-starter-pack",
        torqdata=repo_root / "scripts" / "output" / "torq-demo",
        scripts_dir=repo_root / "scripts",
        orchestrator_dir=orchestrator_dir,
    )


def check_prerequisites(paths: TorqDemoPaths) -> None:
    if not (paths.torqhome / "torq.q").is_file():
        raise TorqDemoError(f"{paths.torqhome} not found or missing torq.q - is lib/torq vendored?")
    if not (paths.torqapphome / "database.q").is_file():
        raise TorqDemoError(
            f"{paths.torqapphome} not found or missing database.q - "
            "is lib/torq-finance-starter-pack vendored?"
        )
    for tool in ("envsubst", "rlwrap"):
        if shutil.which(tool) is None:
            raise TorqDemoError(
                f"'{tool}' not found on PATH - torq.sh needs it "
                "(macOS: brew install gettext rlwrap)"
            )


def clean(paths: TorqDemoPaths) -> None:
    if paths.torqdata.exists():
        log.info("Removing {}", paths.torqdata)
        shutil.rmtree(paths.torqdata)
    else:
        log.info("{} does not exist, nothing to clean", paths.torqdata)


# ---------------------------------------------------------------------------
# process.csv rows + config overrides (get/set)
# ---------------------------------------------------------------------------


def _base_process_rows(paths: TorqDemoPaths) -> list[dict[str, str]]:
    """The vendored process.csv rows, plus uqf's own fxfeed1/quotesfeed1/
    cross1 rows appended (and stp1's -schemafile extras repointed) - never
    mutated, always read fresh from the vendored file.
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
    rows.append(
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{FXFEED_PORT_OFFSET}",
            "proctype": "feed",
            "procname": "fxfeed1",
            "U": "",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/torq_fx_feed.q",
            "startwithall": "1",
            "extras": "",
            "qcmd": "q",
        }
    )
    rows.append(
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{QUOTES_FEED_PORT_OFFSET}",
            "proctype": "feed",
            "procname": "quotesfeed1",
            "U": "",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/torq_quotes_feed.q",
            "startwithall": "1",
            "extras": "",
            "qcmd": "q",
        }
    )
    rows.append(
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{CROSS_ETL_PORT_OFFSET}",
            # proctype "metrics" (not e.g. "etl") deliberately reuses an
            # existing, already-credentialed vendored type: unlike
            # fxfeed1/quotesfeed1 (which only ever *publish* via a
            # self-managed .servers.gethandlebytype handle), cross1 is a
            # real .sub.subscribe subscriber - that needs .servers.startup[]
            # to open a live, access-listed handle to stp1, which in turn
            # needs a proctype with a password file torq.q's own lookup
            # (procname.txt, then proctype.txt, then default.txt) resolves
            # to a credential discovery1's vendored accesslist.txt accepts.
            # A made-up type like "etl" would need a new etl.txt added to
            # the vendored appconfig/passwords/ dir - "metrics" already has
            # one (metrics.txt / metrics:pass), so cross1 borrows it rather
            # than adding to the vendored tree. Harmless: proctype is just
            # a label here, `load` below is what actually decides which
            # script runs, same as multiple real hdb/sortworker rows above
            # already share one proctype each.
            "proctype": "metrics",
            "procname": "cross1",
            "U": "${TORQAPPHOME}/appconfig/passwords/accesslist.txt",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/torq_cross_etl.q",
            "startwithall": "1",
            "extras": "",
            "qcmd": "q",
        }
    )
    rows.append(
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{WIDE_BOOK_FEED_PORT_OFFSET}",
            "proctype": "feed",
            "procname": "widefeed1",
            "U": "",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/torq_wide_book_feed.q",
            "startwithall": "1",
            "extras": "",
            "qcmd": "q",
        }
    )
    rows.append(
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{VECTORIZE_ETL_PORT_OFFSET}",
            # proctype "metrics" borrowed for the same reason as cross1's
            # row above - a real .sub.subscribe subscriber needs
            # .servers.startup[]'s access-listed handle to stp1.
            "proctype": "metrics",
            "procname": "vectorize1",
            "U": "${TORQAPPHOME}/appconfig/passwords/accesslist.txt",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/torq_vectorize_etl.q",
            "startwithall": "1",
            "extras": "",
            "qcmd": "q",
        }
    )
    rows.append(
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{TAP_PORT_OFFSET}",
            # proctype "metrics" borrowed for the same reason as cross1's
            # row above - a real .sub.subscribe subscriber needs
            # .servers.startup[]'s access-listed handle to stp1.
            "proctype": "metrics",
            "procname": "tap1",
            "U": "${TORQAPPHOME}/appconfig/passwords/accesslist.txt",
            "localtime": "1",
            "g": "0",
            "T": "",
            "w": "",
            "load": "${UQFSCRIPTS}/torq_tap.q",
            # debug/verbose utility, not part of the standing demo stack -
            # same reasoning as killtick/tpreplay1 staying off by default.
            "startwithall": "0",
            # -tables t1 t2 ... restricts the tap to those tables; blank
            # (the default) subscribes to every table - see torq_tap.q.
            # `torq-demo config-set tap1 extras "-tables quote wide_book"`
            # to change it.
            "extras": "",
            "qcmd": "q",
        }
    )
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
    return (
        vendored.rstrip("\n")
        + "\n"
        + QUOTES_TABLE_SCHEMA
        + "\n"
        + WIDE_BOOK_TABLE_SCHEMA
        + "\n"
        + MKT_ORDERBOOK_TABLE_SCHEMA
        + "\n"
        + CRYPTO_BOOK_TABLE_SCHEMA
        + "\n"
        + extra
    )


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


# ---------------------------------------------------------------------------
# list_items(paths, kind) - generic listing, not just processes: a small
# registry of kind -> (paths, base_port) -> list[dict], so a new listable
# thing is one function + one registry entry, not a new CLI command/MCP
# tool each time.
# ---------------------------------------------------------------------------


def _list_processes(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    env = build_env(paths, base_port=base_port)
    overrides = _read_overrides(paths)
    items = []
    for row in _base_process_rows(paths):
        eff = dict(row)
        eff.update(overrides.get(row["procname"], {}))
        eff = resolve_process_config(eff, env)
        items.append(
            {
                "procname": eff["procname"],
                "proctype": eff["proctype"],
                "port": eff["port"],
                "startwithall": eff["startwithall"],
            }
        )
    return items


def _list_fields(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    return [{"field": f} for f in PROCESS_CSV_FIELDS]


def _list_overrides(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    return [
        {"procname": procname, "field": field, "value": value}
        for procname, fields in _read_overrides(paths).items()
        for field, value in fields.items()
    ]


def _list_env(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    env = build_env(paths, base_port=base_port)
    return [{"name": name, "value": value} for name, value in env.items()]


LISTABLE_KINDS: dict[str, Any] = {
    "processes": _list_processes,
    "fields": _list_fields,
    "overrides": _list_overrides,
    "env": _list_env,
}


def list_items(
    paths: TorqDemoPaths, kind: str, base_port: int = DEFAULT_BASE_PORT
) -> list[dict[str, str]]:
    """List every item of *kind* - 'processes' (procname/proctype/port/
    startwithall, resolved+overridden), 'fields' (process.csv's valid
    column names, for config-set), 'overrides' (every process_overrides.csv
    entry currently set), or 'env' (build_env()'s resolved KDBBASEPORT/
    KDBHDB/... values). See LISTABLE_KINDS for the full, extensible set.
    """
    if kind not in LISTABLE_KINDS:
        raise TorqDemoError(f"unknown list kind {kind!r} - {sorted(LISTABLE_KINDS)}")
    return LISTABLE_KINDS[kind](paths, base_port)


def build_env(paths: TorqDemoPaths, base_port: int = DEFAULT_BASE_PORT) -> dict[str, str]:
    """The env vars torq.sh (and process.csv's ${VAR}/{VAR}+N placeholders)
    resolve against - pure, no filesystem writes. bootstrap() calls this and
    also writes it out as setenv.sh; get_process_config() calls this to
    resolve a row's placeholders without needing to bootstrap first.
    """
    return {
        "TORQHOME": str(paths.torqhome),
        "TORQAPPHOME": str(paths.torqapphome),
        "TORQDATA": str(paths.torqdata),
        "UQFSCRIPTS": str(paths.scripts_dir),
        "UQFROOT": str(paths.repo_root),
        "KDBCONFIG": str(paths.torqhome / "config"),
        "KDBCODE": str(paths.torqhome / "code"),
        "KDBAPPCONFIG": str(paths.torqapphome / "appconfig"),
        "KDBAPPCODE": str(paths.torqapphome / "code"),
        "KDBLIB": str(paths.torqhome / "lib"),
        "KDBTESTS": str(paths.torqhome / "tests"),
        "KDBLOG": str(paths.torqdata / "logs"),
        "KDBHDB": str(paths.torqdata / "hdb"),
        "KDBWDB": str(paths.torqdata / "wdbhdb"),
        "KDBTPLOG": str(paths.torqdata / "tplogs"),
        "KDBDQCDB": str(paths.torqdata / "dqe" / "dqcdb" / "database"),
        "KDBDQEDB": str(paths.torqdata / "dqe" / "dqedb" / "database"),
        "KDBBASEPORT": str(base_port),
        "KDBSTACKID": f"-stackid {base_port}",
        "TORQPROCESSES": str(paths.generated_procs),
        "RLWRAP": "rlwrap",
        "QCON": "qcon",
        "QCMD": "q",
    }


def bootstrap(paths: TorqDemoPaths, base_port: int = DEFAULT_BASE_PORT) -> dict[str, str]:
    """Idempotently set up the writable data dir and generated config, and
    return the full env dict torq.sh should run under.
    """
    check_prerequisites(paths)

    if not (paths.torqdata / "hdb").is_dir():
        log.info("Bootstrapping {} (first run) - copying sample hdb/dqe data...", paths.torqdata)
        paths.torqdata.mkdir(parents=True, exist_ok=True)
        shutil.copytree(paths.torqapphome / "hdb", paths.torqdata / "hdb")
        shutil.copytree(paths.torqapphome / "dqe", paths.torqdata / "dqe")

    for sub in ("logs", "tplogs", "wdbhdb"):
        (paths.torqdata / sub).mkdir(parents=True, exist_ok=True)

    # Extend (never edit in place) the vendored process.csv with uqf's own
    # extra processes (fxfeed1) and any process_overrides.csv fields set via
    # set_process_config()/`config-set`/torq_demo_set_config.
    overrides = _read_overrides(paths)
    rows = _base_process_rows(paths)
    for row in rows:
        row.update(overrides.get(row["procname"], {}))
    with paths.generated_procs.open("w", newline="") as f:
        # torq.sh's own field lookups are a naive awk -F, parse expecting
        # plain \n line endings, like the vendored csv itself - csv module's
        # default \r\n (the "excel" dialect) corrupts the last column's
        # value (a trailing \r glued onto qcmd breaks the qcmd==qcmd header
        # match, which every single process start looks up), producing a
        # bare `print $` awk syntax error for every process - hard-won via
        # `start all` throwing exactly that for every process at once.
        writer = csv.DictWriter(f, fieldnames=PROCESS_CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    # Same extend-never-edit approach as process.csv above, for stp1's
    # -schemafile (see _generated_schema_content/_base_process_rows).
    paths.generated_schema.write_text(_generated_schema_content(paths))

    env = build_env(paths, base_port=base_port)

    # torq.sh unconditionally sources $SETENV (defaulting to lib/torq/setenv.sh,
    # which would overwrite TORQAPPHOME/TORQPROCESSES/etc back to lib/torq's
    # own defaults) - generate our own and point SETENV at it instead.
    setenv_lines = [f'export {k}="{v}"' for k, v in env.items()]
    paths.generated_setenv.write_text("\n".join(setenv_lines) + "\n")
    env["SETENV"] = str(paths.generated_setenv)

    return env


def run_torq_sh(
    paths: TorqDemoPaths,
    args: list[str],
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Bootstrap, then run lib/torq/torq.sh with *args* under the generated env."""
    overrides = bootstrap(paths, base_port=base_port)
    # subprocess.run's env= *replaces* the environment rather than extending
    # it - merge onto the inherited one (PATH, etc.) or envsubst/rlwrap/q
    # stop resolving even though they're on PATH in the calling shell.
    env = {**os.environ, **overrides}
    cmd = [str(paths.torqhome / "torq.sh"), *args]
    log.debug("running: {}", " ".join(cmd))
    return subprocess.run(
        cmd,
        env=env,
        capture_output=capture,
        text=True,
        check=False,
    )


def start(
    paths: TorqDemoPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
):
    return run_torq_sh(paths, ["start", procs], base_port=base_port, capture=capture)


def stop(
    paths: TorqDemoPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
):
    return run_torq_sh(paths, ["stop", procs], base_port=base_port, capture=capture)


def restart(
    paths: TorqDemoPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
):
    return run_torq_sh(paths, ["restart", procs], base_port=base_port, capture=capture)


def summary(paths: TorqDemoPaths, base_port: int = DEFAULT_BASE_PORT, capture: bool = True):
    return run_torq_sh(paths, ["summary"], base_port=base_port, capture=capture)


def print_procs(
    paths: TorqDemoPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = True,
):
    return run_torq_sh(paths, ["print", procs], base_port=base_port, capture=capture)


def query(
    expr: str,
    port: int,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
) -> Any:
    """Run a synchronous q expression against a running demo process (e.g.
    rdb1 on base_port+2) over kdb+ IPC via kola.
    """
    import kola

    q = kola.Q(host, port, user=user, passwd=passwd)
    q.connect()
    try:
        return q.sync(expr)
    finally:
        q.disconnect()


# ---------------------------------------------------------------------------
# logs - tail each process's out_/err_ log through the Python logger instead
# of raw per-process files. No TorQ-side changes: torq.q's own
# createlog/fileredirect (torq.q:485-489) already maintains
# out_<procname>.log/err_<procname>.log as stable symlink aliases onto the
# current run's timestamped file (dropping the timestamp suffix - see
# `-noredirectalias`), and .lg.format's default (non-jsonlogs) output is a
# fixed 7-field pipe-delimited line - see parse_log_line.
# ---------------------------------------------------------------------------

# .lg.format's plain (non-jsonlogs) line shape, torq.q:203-206:
# time|host|proctype|procname|loglevel|id|message - message itself may
# contain "|", so split with maxsplit rather than a plain split.
_LOG_FIELDS = ("time", "host", "proctype", "procname", "loglevel", "id", "message")

# .lg.outmap's own level vocabulary (torq.q's ERROR/ERR/INF/WARN) mapped
# onto loguru's level names.
_LOGURU_LEVEL = {"ERROR": "ERROR", "ERR": "ERROR", "WARN": "WARNING", "INF": "INFO"}
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

# Dedicated format for `logs` output - the record's own {time}/{function}/
# {line} are Python's (always core.py's _emit, useless here); the kdb
# process's own timestamp/procname/proctype (bound as `extra` below) are
# what's actually informative, so they replace them entirely rather than
# just prefixing the message.
_KDB_LOG_FMT = (
    "<green>{extra[kdb_time]}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[procname]}</cyan>/<cyan>{extra[proctype]}</cyan> - <level>{message}</level>"
)


def _format_kdb_time(t: str) -> str:
    """Trim .lg.format's nanosecond timestamp to millisecond precision for
    display, e.g. '2026.08.22D14:21:10.644413000' -> '2026.08.22D14:21:10.644'.
    """
    head, sep, frac = t.rpartition(".")
    return f"{head}.{frac[:3]}" if sep else t


def _configure_kdb_log_sink() -> Any:
    """(Re)configure the shared loguru logger with _KDB_LOG_FMT for the
    duration of a `logs` command, overriding whatever format main()'s
    configure_logging(component="torq_demo") set up for the rest of the
    CLI - `logs` is always a leaf command, so clobbering the global sink
    here is safe.
    """
    from torq_orchestrator.logger.core import setup_logging

    return setup_logging(level="DEBUG", format_string=_KDB_LOG_FMT)


def resolve_procnames(paths: TorqDemoPaths, procs: str) -> list[str]:
    """'all' -> every process.csv row (not just startwithall=1 - a stopped
    process's last-run log is still worth reading); otherwise the given
    space-separated names, unvalidated (missing log files are just skipped).
    """
    if procs == "all":
        return list_process_names(paths)
    return procs.split()


def parse_log_line(line: str) -> dict[str, str] | None:
    """One .lg.format line -> a field dict, or None if it doesn't match the
    expected 7-field shape (e.g. a startup banner line written before
    .lg's own redirect kicks in).
    """
    parts = line.rstrip("\n").split("|", len(_LOG_FIELDS) - 1)
    if len(parts) != len(_LOG_FIELDS):
        return None
    return dict(zip(_LOG_FIELDS, parts, strict=True))


def _log_files(paths: TorqDemoPaths, procnames: list[str]) -> list[Path]:
    log_dir = paths.torqdata / "logs"
    files = [log_dir / f"{stream}_{name}.log" for name in procnames for stream in ("out", "err")]
    return [f for f in files if f.is_file()]


def _passes_level(level: str, min_level: str | None) -> bool:
    if min_level is None:
        return True
    return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(min_level.upper(), 0)


def _emit(log: Any, rec: dict[str, str], min_level: str | None) -> None:
    level = _LOGURU_LEVEL.get(rec["loglevel"], "INFO")
    if not _passes_level(level, min_level):
        return
    log.bind(
        kdb_time=_format_kdb_time(rec["time"]), procname=rec["procname"], proctype=rec["proctype"]
    ).log(level, rec["message"])


def print_recent_logs(
    paths: TorqDemoPaths, procs: str = "all", lines: int = 20, min_level: str | None = None
) -> None:
    """Print the last *lines* lines of each matching process's out_/err_
    log, merged and sorted by timestamp, through the shared loguru logger.
    """
    procnames = resolve_procnames(paths, procs)
    files = _log_files(paths, procnames)
    if not files:
        raise TorqDemoError(
            f"no log files found for {procnames} under {paths.torqdata / 'logs'} "
            "- has the demo been started at least once?"
        )

    records = []
    for f in files:
        tail = f.read_text(errors="replace").splitlines()[-lines:]
        records.extend(rec for line in tail if (rec := parse_log_line(line)) is not None)
    records.sort(key=lambda r: r["time"])

    log = _configure_kdb_log_sink()
    for rec in records:
        _emit(log, rec, min_level)


def _pump(stream: Any, out_queue: Any) -> None:
    for line in stream:
        out_queue.put(line)


def follow_logs(paths: TorqDemoPaths, procs: str = "all", min_level: str | None = None) -> None:
    """Stream new lines appended to each matching process's out_/err_ log,
    live, through the shared loguru logger - Ctrl-C to stop. One `tail -F`
    subprocess per file (follows the stable alias across TorQ's own log
    rolling, no polling/inotify dependency of our own) fanned into a single
    queue by a reader thread each.
    """
    import queue
    import threading

    procnames = resolve_procnames(paths, procs)
    files = _log_files(paths, procnames)
    if not files:
        raise TorqDemoError(
            f"no log files found for {procnames} under {paths.torqdata / 'logs'} "
            "- has the demo been started at least once?"
        )

    log = _configure_kdb_log_sink()
    line_queue: queue.Queue[str] = queue.Queue()
    tails = [
        subprocess.Popen(
            ["tail", "-n", "0", "-F", str(f)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for f in files
    ]
    threads = [
        threading.Thread(target=_pump, args=(t.stdout, line_queue), daemon=True) for t in tails
    ]
    for thread in threads:
        thread.start()

    try:
        while True:
            rec = parse_log_line(line_queue.get())
            if rec is not None:
                _emit(log, rec, min_level)
    except KeyboardInterrupt:
        pass
    finally:
        for t in tails:
            t.terminate()


# ---------------------------------------------------------------------------
# crypto recorder - spawns ~/github_projects/cryptorust's own
# kdb-market-data-recorder binary (Rust, entirely separate project/language)
# pointed at this demo's stp1. A proof of concept that this demo's kdb+
# infra isn't TorQ/q-specific: any process that can speak kdb+ IPC can
# publish onto the same tickerplant, alongside the q feeds above. Not a
# process.csv row - torq.sh only drives q processes, so this gets its own
# minimal subprocess + pidfile lifecycle instead.
# ---------------------------------------------------------------------------

CRYPTORUST_ROOT_ENV = "CRYPTORUST_ROOT"

# The demo's own accesslist.txt convention (see torq_cross_etl.q's proctype-
# borrowing comment for the same mechanism from the q side): stp1 enforces
# access control on every incoming connection via .z.pw, and
# appconfig/passwords/feed.txt already resolves to a credential
# accesslist.txt accepts - reused here rather than adding a new password
# file to the vendored tree.
CRYPTO_RECORDER_CREDENTIAL = "feed:pass"
CRYPTO_RECORDER_TABLE = "crypto_book"
CRYPTO_RECORDER_DEFAULT_VENUES = ("binance_spot",)
CRYPTO_RECORDER_DEFAULT_SYMBOLS = ("BTC-USDT", "ETH-USDT")


def cryptorust_root(paths: TorqDemoPaths) -> Path:
    """Where the sibling cryptorust checkout lives - override via
    $CRYPTORUST_ROOT; defaults to a sibling of this repo
    (~/github_projects/cryptorust), matching how both are normally checked
    out side by side under the same github_projects/ parent.
    """
    override = os.environ.get(CRYPTORUST_ROOT_ENV)
    if override:
        return Path(override)
    return paths.repo_root.parent / "cryptorust"


def _crypto_recorder_config_yaml(
    stp1_port: int,
    venues: list[str],
    symbols: list[str],
    credential: str,
    table: str,
    top_n_levels: int,
    interval_ms: int,
) -> str:
    """cryptorust's ServiceConfig::from_yaml merges this onto its own
    embedded config/default.yaml, so only fields worth overriding need
    appear here - no PyYAML dependency needed for a handful of flat/list
    fields like this.
    """
    venues_yaml = "\n".join(f"  - {v}" for v in venues)
    symbols_yaml = "\n".join(f"  - {s}" for s in symbols)
    return (
        f"market_symbol: {symbols[0]}\n"
        f"market_symbols:\n{symbols_yaml}\n"
        f"venues:\n{venues_yaml}\n"
        "kdb_market_data_recorder:\n"
        "  enabled: true\n"
        "  host: localhost\n"
        f"  port: {stp1_port}\n"
        f'  credential: "{credential}"\n'
        f"  table: {table}\n"
        f"  top_n_levels: {top_n_levels}\n"
        f"  interval_ms: {interval_ms}\n"
    )


def _read_crypto_recorder_pid(paths: TorqDemoPaths) -> int | None:
    if not paths.crypto_recorder_pid_path.is_file():
        return None
    try:
        return int(paths.crypto_recorder_pid_path.read_text().strip())
    except ValueError:
        return None


def is_crypto_recorder_running(paths: TorqDemoPaths) -> bool:
    pid = _read_crypto_recorder_pid(paths)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # signal 0: existence check only, doesn't actually signal
    except OSError:
        return False
    return True


def start_crypto_recorder(
    paths: TorqDemoPaths,
    base_port: int = DEFAULT_BASE_PORT,
    venues: tuple[str, ...] = CRYPTO_RECORDER_DEFAULT_VENUES,
    symbols: tuple[str, ...] = CRYPTO_RECORDER_DEFAULT_SYMBOLS,
    top_n_levels: int = 5,
    interval_ms: int = 1000,
) -> int:
    """Build (if needed) and launch cryptorust's kdb-market-data-recorder,
    pointed at this demo's own stp1 - see CRYPTO_BOOK_TABLE_SCHEMA for the
    destination table. Returns the spawned PID.
    """
    root = cryptorust_root(paths)
    if not (root / "Cargo.toml").is_file():
        raise TorqDemoError(
            f"{root} doesn't look like a cryptorust checkout (no Cargo.toml) - "
            f"set ${CRYPTORUST_ROOT_ENV} if it's checked out somewhere else"
        )
    if is_crypto_recorder_running(paths):
        raise TorqDemoError("crypto recorder is already running - stop it first")

    stp1 = get_process_config(paths, "stp1", base_port=base_port)

    paths.torqdata.mkdir(parents=True, exist_ok=True)
    paths.crypto_recorder_config_path.write_text(
        _crypto_recorder_config_yaml(
            stp1_port=int(stp1["port"]),
            venues=list(venues),
            symbols=list(symbols),
            credential=CRYPTO_RECORDER_CREDENTIAL,
            table=CRYPTO_RECORDER_TABLE,
            top_n_levels=top_n_levels,
            interval_ms=interval_ms,
        )
    )

    # cryptorust's pyo3 dependency's build script rejects a system Python
    # newer than PyO3 currently supports - this repo doesn't need pyo3 at
    # all (no Python extension built here), so forward-compatibility mode
    # is safe to force rather than requiring a specific Python on PATH.
    env = {**os.environ, "PYO3_USE_ABI3_FORWARD_COMPATIBILITY": "1"}
    log.info("building cryptorust's kdb-market-data-recorder (first run may take a while)...")
    build = subprocess.run(
        [
            "cargo",
            "build",
            "--quiet",
            "--features",
            "standalone",
            "--bin",
            "kdb-market-data-recorder",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise TorqDemoError(f"cargo build failed:\n{build.stderr}")

    binary = root / "target" / "debug" / "kdb-market-data-recorder"
    (paths.torqdata / "logs").mkdir(parents=True, exist_ok=True)
    log_path = paths.torqdata / "logs" / "crypto_recorder.log"
    with log_path.open("w") as log_file:
        process = subprocess.Popen(
            [str(binary), "--config", str(paths.crypto_recorder_config_path)],
            cwd=root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    paths.crypto_recorder_pid_path.write_text(str(process.pid))
    log.info(
        "started cryptorust kdb-market-data-recorder (pid {}), publishing {} to {} - logging to {}",
        process.pid,
        ",".join(venues),
        CRYPTO_RECORDER_TABLE,
        log_path,
    )
    return process.pid


def stop_crypto_recorder(paths: TorqDemoPaths) -> None:
    pid = _read_crypto_recorder_pid(paths)
    if pid is None:
        raise TorqDemoError("crypto recorder is not running (no pid file)")
    try:
        # SIGTERM, not the ManagedService graceful-shutdown path (that only
        # fires on SIGINT/ctrl_c) - fine here: on_stop is a no-op, and an
        # unhandled SIGTERM just terminates the process, dropping its kdb+
        # IPC socket, which stp1 sees as an ordinary disconnect.
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    paths.crypto_recorder_pid_path.unlink(missing_ok=True)
    log.info("stopped cryptorust kdb-market-data-recorder (pid {})", pid)


def crypto_recorder_status(paths: TorqDemoPaths) -> dict[str, str]:
    pid = _read_crypto_recorder_pid(paths)
    return {
        "running": str(is_crypto_recorder_running(paths)),
        "pid": str(pid) if pid is not None else "",
        "table": CRYPTO_RECORDER_TABLE,
        "config": str(paths.crypto_recorder_config_path),
        "log": str(paths.torqdata / "logs" / "crypto_recorder.log"),
    }
