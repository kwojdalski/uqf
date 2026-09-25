"""Bootstrapping, the env bridge, and driving lib/torq/torq.sh.

bootstrap() is idempotent and regenerates the overlay config on every call
from the vendored inputs plus uqf's own additions (never hand-edit
vendored configuration, and never depend on a previously generated file
still being correct)."""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from uqs.logger import get_logger
from uqs.model.pipelines import PROCESS_CSV_FIELDS
from uqs.model.plant_schema import _generated_schema_content
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError, UqsPaths, check_prerequisites, q_interpreter
from uqs.stack import alive
from uqs.stack.env import build_env
from uqs.stack.procs import _base_process_rows, _read_overrides

log = get_logger(__name__)


#: The q script that writes an empty table into a partition that lacks it,
#: and a declared column into a table that lacks it. q rather than Python
#: because both have to be written with their schema and enumerated against
#: the HDB's sym file; a directory of the right name is not a table, and a
#: file of the right name is not a column.
FILL_HDB_SCRIPT = "gates/fill_hdb_partitions.q"


def fill_hdb_partitions(paths: UqsPaths) -> bool:
    """Write an empty copy of every declared table into every partition
    that lacks one, and every declared column into every table that lacks
    one. Returns whether the filler ran at all.

    Idempotent and additive: a table directory or column file that exists
    is never touched, and a table or column a partition holds that
    database.q no longer declares is left alone - that is history, and
    deleting history is not this function's business. A column whose
    declared TYPE changed is reported by `uqs hdb-check`, not
    repaired here.

    NEVER FAILS A BOOTSTRAP. Every `uqs` command bootstraps, so a
    problem here - no q on the path, an HDB mid-write, a permissions
    fault - must not stop an operator stopping the stack or reading a log.
    It is reported and stepped over; `uqs hdb-check` says the same
    thing on demand, and the smoke lane says it about a running stack.
    """
    hdb_root = paths.torqdata / "hdb"
    if not hdb_root.is_dir():
        return False
    script = paths.scripts_dir / FILL_HDB_SCRIPT
    if not script.is_file():  # pragma: no cover - a broken checkout
        log.warning("HDB partition filler not found at {}", script)
        return False
    result = subprocess.run(
        [
            str(q_interpreter()),
            str(script),
            str(hdb_root),
            str(paths.generated_schema),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=paths.repo_root,
    )
    if result.returncode != 0:
        log.warning(
            "could not fill HDB partitions ({}) - a query spanning every table "
            "may fail until `uqs hdb-check` is run: {}",
            result.returncode,
            (result.stderr or result.stdout).strip()[:300],
        )
        return False
    for line in result.stdout.splitlines():
        # "added" too, or the column half of the repair happens silently and
        # an operator reading the log believes the database was untouched.
        if "wrote" in line or "filled" in line or "added" in line:
            log.info("hdb: {}", line.strip())
    return True


def bootstrap(paths: UqsPaths, base_port: int = DEFAULT_BASE_PORT) -> dict[str, str]:
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
    # set_process_config()/`config-set`/uqs_set_config.
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

    # Make the HDB rectangular, now that database.q says what it should
    # hold. A partitioned kdb+ database needs every table in every
    # partition, and this one never has: the vendored sample ships two
    # partitions holding `quote` and `trade`, this tree declares
    # twenty-five more, and each day the stack runs bakes in whatever
    # existed that day. The symptom is not a missing column - it is every
    # cross-table HDB query failing outright, naming whichever table sorts
    # first (#348).
    #
    # HERE, after the schema is written, because the filler reads it: the
    # schema for a table no partition holds has to come from somewhere, and
    # .Q.chk - which copies it from a partition that HAS the table - cannot
    # help in exactly the case that matters.
    fill_hdb_partitions(paths)

    env = build_env(paths, base_port=base_port)

    # torq.sh unconditionally sources $SETENV (defaulting to lib/torq/setenv.sh,
    # which would overwrite TORQAPPHOME/TORQPROCESSES/etc back to lib/torq's
    # own defaults) - generate our own and point SETENV at it instead.
    setenv_lines = [f'export {k}="{v}"' for k, v in env.items()]
    paths.generated_setenv.write_text("\n".join(setenv_lines) + "\n")
    env["SETENV"] = str(paths.generated_setenv)

    return env


def run_torq_sh(
    paths: UqsPaths,
    args: list[str],
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Bootstrap, then run lib/torq/torq.sh with *args* under the generated env.

    `timeout` is seconds to wait before giving up, or None to wait forever -
    which is the right default for `start`/`stop`, whose whole job is to wait
    for something slow. A read-only command that an operator runs to find out
    what is going on should not be the thing that hangs, so `summary` sets
    one.
    """
    overrides = bootstrap(paths, base_port=base_port)
    # subprocess.run's env= *replaces* the environment rather than extending
    # it - merge onto the inherited one (PATH, etc.) or envsubst/rlwrap/q
    # stop resolving even though they're on PATH in the calling shell.
    env = {**os.environ, **overrides}
    cmd = [str(paths.torqhome / "torq.sh"), *args]
    log.debug("running: {} (timeout={})", " ".join(cmd), timeout)
    try:
        return subprocess.run(
            cmd,
            env=env,
            capture_output=capture,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills the child before re-raising, so there is no
        # orphan left behind. Reported as a refusal rather than a traceback,
        # and named as a timeout rather than a failure: torq.sh did not say
        # no, it did not say anything.
        raise UqsError(
            f"torq.sh {' '.join(args)} did not finish within {timeout:g}s. "
            "It is still bootstrapping, or a process it queries is not "
            "answering - raise --timeout if the stack is simply slow to start"
        ) from exc


def start(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
):
    return run_torq_sh(paths, ["start", procs], base_port=base_port, capture=capture)


def stop(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
):
    return run_torq_sh(paths, ["stop", procs], base_port=base_port, capture=capture)


def restart(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = False,
):
    return run_torq_sh(paths, ["restart", procs], base_port=base_port, capture=capture)


def summary(
    paths: UqsPaths,
    base_port: int = DEFAULT_BASE_PORT,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """torq.sh summary's table, without torq.sh: see stack/alive.py for why."""
    table = alive.status_table(paths, base_port=base_port, timeout=timeout)
    return subprocess.CompletedProcess(["summary"], 0, table, "")


def print_procs(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int = DEFAULT_BASE_PORT,
    capture: bool = True,
):
    return run_torq_sh(paths, ["print", procs], base_port=base_port, capture=capture)


def qcon_command(host: str, port: int, user: str, passwd: str, *, rlwrap: bool) -> list[str]:
    """The argv for an interactive `qcon` session against one process.

    `qcon` takes ONE colon-joined argument - `host:port:user:password` - not
    four separate ones. Passing them separately does not fail as a usage
    error; qcon reads the first as the whole target and the rest as files, so
    the symptom is a connection refusal that looks like the process is down.

    `rlwrap` gives the session line editing and history and is optional -
    qcon runs without it, which is why it is a caller's decision rather than
    a hard requirement here. torq.sh makes the same choice through its
    RLWRAP variable.

    NOTE the password is on the command line, so it is visible in `ps` to
    anyone on the box. That is qcon's interface, not a choice made here, and
    it is the same exposure as the documented `uqs raw -- qcon gateway1
    admin:admin`.
    """
    prefix = ["rlwrap"] if rlwrap else []
    return [*prefix, "qcon", f"{host}:{port}:{user}:{passwd}"]


def query(
    expr: str,
    port: int,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    timeout: int = 0,
) -> Any:
    """Run a synchronous q expression against a running demo process (e.g.
    rdb1 on base_port+2) over kdb+ IPC via kola.

    `timeout` is whole SECONDS, kola's own unit, and 0 means wait forever -
    kola's default, kept as this function's default so an interactive query
    against a slow process is not cut off at an arbitrary point.

    It matters most for a process that accepts the TCP connection and then
    does not answer, which is not hypothetical here: a q process at its
    licence connection cap resets late in the handshake, and monitor1 sits at
    that cap on a full stack.
    """
    import kola

    q = kola.Q(host, port, user=user, passwd=passwd, timeout=timeout)
    q.connect()
    try:
        return q.sync(expr)
    finally:
        q.disconnect()


def export_table(rows: Any, path: Path) -> None:
    """Write *rows* to *path* as CSV or Parquet, format inferred from the
    file extension. *rows* is either a list[dict] (list_items/config-get's
    own shape) or a polars.DataFrame (what kola's query() returns for a
    table-shaped q result - kola is a Polars interface to q, so this is
    already the native return type for `select ... from t`, no conversion
    needed). Anything else (a q scalar/atom from query(), e.g. `count t`)
    isn't rows, so it's rejected rather than silently wrapped into a bogus
    one-cell table.
    """
    import polars as pl

    if isinstance(rows, pl.DataFrame):
        df = rows
    elif isinstance(rows, list):
        df = pl.DataFrame(rows)
    else:
        raise UqsError(
            f"can't export a {type(rows).__name__} result to a table - --export needs "
            "tabular output (a process/config list, or a query returning a table)"
        )

    suffix = path.suffix.lower()
    if suffix == ".csv":
        df.write_csv(path)
    elif suffix == ".parquet":
        df.write_parquet(path)
    else:
        raise UqsError(f"unsupported export extension {suffix!r} - use .csv or .parquet")
