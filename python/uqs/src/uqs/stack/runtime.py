"""Bootstrapping, the env bridge, and driving lib/torq/torq.sh.

bootstrap() is idempotent and regenerates the overlay config on every call
from the vendored inputs plus uqf's own additions (never hand-edit
vendored configuration, and never depend on a previously generated file
still being correct)."""

from __future__ import annotations

import csv
import os
import shutil
import socket
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from uqs.interpreter import q_command
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths, check_prerequisites
from uqs.stack import alive, deployment, gateway_access, qtree, start_overrides, start_policy
from uqs.stack import procs as stack_procs
from uqs.stack import render as stack_render
from uqs.stack.carriable import check_carriable
from uqs.stack.dqe import write_dqe_config
from uqs.stack.env import build_env, interpreter_env, with_interpreter
from uqs.stack.hdb_fill import fill_hdb_partitions
from uqs.stack.hdb_types import declared_schema
from uqs.stack.launcher import torq_launcher
from uqs.stack.procs import effective_process_rows
from uqs.stack.reporter import write_reporter_config
from uqs.stack.site_fields import process_csv_fields

log = get_logger()


def bootstrap(
    paths: UqsPaths, base_port: int | None = None, selected: Sequence[str] | None = None
) -> dict[str, str]:
    """Idempotently set up the writable data dir and generated config, and return the full
    env dict torq.sh should run under. `selected` is the set the start brings up, else the
    recorded one: what reporter1's checks (#1248) and monitor1's budget (#1318) are made for."""
    check_prerequisites(paths)
    selected = deployment.members(paths) if selected is None else selected

    if not paths.hdb_dir.is_dir():
        log.info("Bootstrapping {} (first run) - copying sample hdb/dqe data...", paths.torqdata)
        paths.torqdata.mkdir(parents=True, exist_ok=True)
        shutil.copytree(paths.torqapphome / "hdb", paths.hdb_dir)
        shutil.copytree(paths.torqapphome / "dqe", paths.torqdata / "dqe")

    for sub in ("logs", "tplogs", "wdbhdb"):
        (paths.torqdata / sub).mkdir(parents=True, exist_ok=True)

    # Extend (never edit in place) the vendored process.csv with uqf's own extra processes
    # (fxfeed1) and any process_overrides.csv fields set via set_process_config()/`config
    # set`/uqs_set_config. Each row TorQ leaves to its default interpreter - an empty qcmd, or `q`
    # spelled out, as the starter pack and the pipeline rows both do - names the configured one:
    # QCMD, or the runtime's own (#764). torq.sh prefers a row's qcmd to $QCMD, so a row saying
    # `q` ran PATH's q whatever QCMD named, and a site launcher need not fall back to $QCMD at
    # all. An operator's override still wins, even one to bare `q`.
    rows = effective_process_rows(paths, q_command(with_interpreter(paths)), selected)
    check_carriable(rows)
    with paths.generated_procs.open("w", newline="") as f:
        # torq.sh's own field lookups are a naive awk -F, parse expecting
        # plain \n line endings, like the vendored csv itself - csv module's
        # default \r\n (the "excel" dialect) corrupts the last column's
        # value (a trailing \r glued onto qcmd breaks the qcmd==qcmd header
        # match, which every single process start looks up), producing a
        # bare `print $` awk syntax error for every process - hard-won via
        # `start all` throwing exactly that for every process at once.
        writer = csv.DictWriter(f, fieldnames=process_csv_fields(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    # Without overlays (the torq runtime) the schema is the starter pack's
    # own, copied so the HDB filler below and `uqs` read it from the one place
    # every runtime keeps it. Its gateway1 keeps the vendored access list, so
    # there is none to write.
    if not paths.runtime_declaration.overlays:
        paths.generated_schema.write_text(declared_schema(paths))
    else:
        # gateway1's and sctp1's access lists, and the data-access table list
        # the rdbs, hdbs and gateways are started with (stack/gateway_access.py).
        gateway_access.write(paths)
        # Same extend-never-edit approach as process.csv above, for stp1's
        # -schemafile (see _generated_schema_content/_composed_rows).
        paths.generated_schema.write_text(declared_schema(paths))
        # Same again for DQE's query list (stack/dqe.py), and for reporter1's checks,
        # validated against this deployment, not the site's reporter.csv (stack/reporter.py).
        write_dqe_config(paths)
        write_reporter_config(paths, (r["procname"] for r in rows), selected)

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
    # A runtime on PeachQ starts its processes, and only its own, on PeachQ.
    env.update(interpreter_env(paths))

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
    base_port: int | None = None,
    capture: bool = False,
    timeout: float | None = None,
    selected: Sequence[str] | None = None,
    skips: start_overrides.Overrides = start_overrides.NONE,
) -> subprocess.CompletedProcess[str]:
    """Check the TorQ launcher, bootstrap, then run the launcher with *args*
    under the generated env: `$TORQHOME/torq.sh`, or `UQS_TORQ_LAUNCHER`
    (stack/launcher.py). A launcher that cannot run is refused before
    bootstrap writes anything.

    A `start` or `restart` is refused first by start_policy.refuse_start -
    here, so no caller (`uqs start`, `uqs job backfill`, `uqs replay`, `uqs exec`)
    can skip it (#964).

    `timeout` is seconds to wait before giving up, or None to wait forever -
    which is the right default for `start`/`stop`, whose whole job is to wait
    for something slow. A read-only command that an operator runs to find out
    what is going on should not be the thing that hangs, so `summary` sets
    one.

    `selected` is a profile start's set: what TorQ discovers (stack/deployment.py).
    `skips` names refusals to skip for this start (stack/start_overrides.py).
    """
    start_policy.refuse_start(paths, args, base_port, skips)
    args = start_policy.with_publisher_extras(paths, args)
    # The launcher first: bootstrap writes the data directory, process.csv and setenv.sh,
    # and a launcher that is going to be refused must not have had that written for it.
    launcher = torq_launcher(paths)
    plan = deployment.planned(paths, args, selected)
    overrides = bootstrap(paths, base_port=base_port, selected=plan)
    # subprocess.run's env= *replaces* the environment rather than extending it - merge onto
    # the inherited one (PATH, etc.) or envsubst/rlwrap/q stop resolving, though on PATH.
    env = {**os.environ, **overrides}
    if args and args[0] in start_policy.STARTING_VERBS:
        # What a start or restart loads, converted and proven first - refused before
        # anything starts. stop and summary leave the published tree alone.
        if qtree.is_flattened(paths):
            qtree.prepare(paths, q_command(env), env)
        # What TorQ discovers: `selected` is a profile's set (stack/deployment.py)
        deployment.record(paths, start_policy.started_procs(args), selected)
    cmd = [str(launcher), *args]
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


def start(paths: UqsPaths, procs: str = "all", base_port: int | None = None, **kw: Any):
    """`selected=` a profile's resolved set; `capture=`, `timeout=` as run_torq_sh."""
    kw.setdefault("capture", False)
    return run_torq_sh(paths, ["start", procs], base_port=base_port, **kw)


def stop(paths: UqsPaths, procs: str = "all", base_port: int | None = None, **kw: Any):
    stack_procs.assert_known_procnames(paths, procs)  # below every front end (#887)
    kw.setdefault("capture", False)
    return run_torq_sh(paths, ["stop", procs], base_port=base_port, **kw)


def restart(paths: UqsPaths, procs: str = "all", base_port: int | None = None, **kw: Any):
    kw.setdefault("capture", False)
    return run_torq_sh(paths, ["restart", procs], base_port=base_port, **kw)


def summary(
    paths: UqsPaths,
    base_port: int | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """torq.sh summary's table, without torq.sh: see stack/alive.py for why."""
    table = alive.status_table(paths, base_port=base_port, timeout=timeout)
    return subprocess.CompletedProcess(["summary"], 0, table, "")


def print_procs(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int | None = None,
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
    it is the same exposure as the documented `uqs exec -- qcon gateway1
    admin:admin`.
    """
    prefix = ["rlwrap"] if rlwrap else []
    return [*prefix, "qcon", f"{host}:{port}:{user}:{passwd}"]


def kola_host(host: str, timeout: int) -> str:
    """The address to hand kola for `host`: its IPv4 address when a timeout is set.

    kola 2.5 connects to only the FIRST address a name resolves to when given
    a timeout, and on this Mac `localhost` resolves to `::1` first while q
    listens on IPv4 alone - so every connection with a timeout was refused
    ("Connection refused (os error 61)") against a process that was plainly
    up. Without a timeout kola tries every address and works. A name with no
    IPv4 address is returned unchanged. The frontend's
    gateway client has the same helper; it does not import uqs on its query
    path.
    """
    if not timeout:
        return host
    try:
        found = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return host
    return str(found[0][4][0]) if found else host


def query(
    expr: str,
    port: int,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    timeout: int = 0,
    render: tuple[int, int] | None = None,
) -> Any:
    """Run a synchronous q expression against a running demo process (e.g.
    rdb1 on base_port+2) over kdb+ IPC via kola.

    With `render` - a (rows, columns) console size - the process formats the
    result itself and this returns the text q's console would print; without
    it, kola's Python objects. See stack/render.py.

    `timeout` is whole SECONDS, kola's own unit, and 0 means wait forever -
    kola's default, kept as this function's default so an interactive query
    against a slow process is not cut off at an arbitrary point.

    It matters most for a process that accepts the TCP connection and then
    does not answer, which is not hypothetical here: a q process at its
    licence connection cap resets late in the handshake, and monitor1 sits at
    that cap on a full stack.
    """
    import kola

    q = kola.Q(kola_host(host, timeout), port, user=user, passwd=passwd, timeout=timeout)
    q.connect()
    try:
        if render is None:
            return q.sync(expr)
        return stack_render.text(q.sync(stack_render.wrap(expr, render)))
    finally:
        q.disconnect()


def export_table(rows: Any, path: Path) -> None:
    """Write *rows* to *path* as CSV or Parquet, format inferred from the
    file extension. *rows* is either a list[dict] (list_items/config show's
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
