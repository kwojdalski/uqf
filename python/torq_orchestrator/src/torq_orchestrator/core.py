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
import subprocess
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from torq_orchestrator.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE_PORT = 6010
FXFEED_PORT_OFFSET = 19  # the one offset the vendored process.csv leaves free
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
    def generated_setenv(self) -> Path:
        return self.torqdata / "setenv.sh"

    @property
    def overrides_path(self) -> Path:
        return self.orchestrator_dir / "process_overrides.csv"


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
    """The vendored process.csv rows, plus uqf's own fxfeed1 row appended -
    never mutated, always read fresh from the vendored file.
    """
    vendored_procs = paths.torqapphome / "appconfig" / "process.csv"
    with vendored_procs.open(newline="") as f:
        rows = list(csv.DictReader(f))
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
    return rows


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
