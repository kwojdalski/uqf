"""Bootstrapping, the env bridge, and driving lib/torq/torq.sh.

bootstrap() is idempotent and regenerates the overlay config on every call
from the vendored inputs plus uqf's own additions (never hand-edit
vendored configuration, and never depend on a previously generated file
still being correct)."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from uqs.interpreter import q_command
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack import (
    alive,
    attached_publisher,
    deployment,
    gateway_access,
    generated,
    qtree,
    start_lines,
    start_overrides,
    start_policy,
    stopping,
)
from uqs.stack import render as stack_render
from uqs.stack.compression import write_compression_config
from uqs.stack.dqe import write_dqe_config
from uqs.stack.env import build_env, interpreter_env, with_interpreter
from uqs.stack.hdb_fill import fill_hdb_partitions
from uqs.stack.hdb_types import declared_schema
from uqs.stack.housekeeping import write_housekeeping_config
from uqs.stack.launcher import torq_launcher
from uqs.stack.prerequisites import check_prerequisites
from uqs.stack.procs import effective_process_rows
from uqs.stack.reporter import write_reporter_config

log = get_logger()


def bootstrap(
    paths: UqsPaths, base_port: int | None = None, selected: Sequence[str] | None = None
) -> dict[str, str]:
    """Idempotently set up the writable data dir and generated config, and return the full
    env dict torq.sh should run under. `selected` is the set the start brings up, else the
    recorded one: what reporter1's checks (#1248) and monitor1's budget (#1318) are made for."""
    check_prerequisites(paths)
    selected = deployment.members(paths) if selected is None else selected

    _seed_data_dir(paths)
    rows = effective_process_rows(paths, q_command(with_interpreter(paths)), selected)
    generated.write_process_csv(paths, rows)

    # Without overlays (the torq runtime) the schema is the starter pack's
    # own, copied so the HDB filler below and `uqs` read it from the one place
    # every runtime keeps it. Its gateway1 keeps the vendored access list, so
    # there is none to write.
    if not paths.runtime_declaration.overlays:
        generated.write_text(paths.generated_schema, declared_schema(paths))
    else:
        # gateway1's and sctp1's access lists, and the data-access table list
        # the rdbs, hdbs and gateways are started with (stack/gateway_access.py).
        gateway_access.write(paths)
        # Same extend-never-edit approach as process.csv above, for stp1's
        # -schemafile (see _generated_schema_content/_composed_rows).
        generated.write_text(paths.generated_schema, declared_schema(paths))
        # Same again for DQE's query list (stack/dqe.py), and for reporter1's checks,
        # validated against this deployment, not the site's reporter.csv (stack/reporter.py).
        write_dqe_config(paths)
        write_reporter_config(paths, (r["procname"] for r in rows), selected)
        # And housekeeping1's rules, which keep tickerplant logs replayable
        # rather than the site's, which tar them (stack/housekeeping.py, #2155).
        write_housekeeping_config(paths)

    # TorQ's .cmp policy, copied beside the rest of the generated files for
    # scripts/processes/uqs_compression_config.q to point it at (stack/compression.py).
    write_compression_config(paths)

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

    return write_setenv(paths, base_port)


def bootstrap_publisher(
    paths: UqsPaths,
    procs: Sequence[str],
    base_port: int | None = None,
    selected: Sequence[str] | None = None,
    *,
    starting: bool = True,
) -> dict[str, str]:
    """`bootstrap` for a command that names attachments' publishers and nothing
    else (stack/attached_publisher.py, #1847): the logs directory, process.csv
    and setenv.sh - the publishers' own launch configuration - and none of the
    stack's access lists, schema, service configuration or HDB, which belong to
    a stack uqf does not manage. A start refuses an authentication it cannot
    tell; a stop needs none."""
    check_prerequisites(paths)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    rows = effective_process_rows(paths, q_command(with_interpreter(paths)), selected)
    extra: dict[str, str] = {}
    if starting:
        how = attached_publisher.auth(paths, procs)
        rows, extra = attached_publisher.with_auth(rows, procs, how), how.env()
    generated.write_process_csv(paths, rows)
    return write_setenv(paths, base_port, extra)


def _seed_data_dir(paths: UqsPaths) -> None:
    """A first run's data directory: the starter pack's sample HDB and DQE
    data, and the directories the stack writes into."""
    if not paths.hdb_dir.is_dir():
        log.info("Bootstrapping {} (first run) - copying sample hdb/dqe data...", paths.torqdata)
        paths.torqdata.mkdir(parents=True, exist_ok=True)
        shutil.copytree(paths.torqapphome / "hdb", paths.hdb_dir)
        shutil.copytree(paths.torqapphome / "dqe", paths.torqdata / "dqe")

    for sub in ("logs", "tplogs", "wdbhdb"):
        (paths.torqdata / sub).mkdir(parents=True, exist_ok=True)


def write_setenv(
    paths: UqsPaths, base_port: int | None, extra: dict[str, str] | None = None, target=None
) -> dict[str, str]:
    """The env torq.sh runs under, plus `extra`, written as setenv.sh (or `target`)."""
    target = target or paths.generated_setenv
    env = build_env(paths, base_port=base_port)
    # A runtime on PeachQ starts its processes, and only its own, on PeachQ.
    env.update(interpreter_env(paths))
    env.update(extra or {})

    # torq.sh unconditionally sources $SETENV (defaulting to lib/torq/setenv.sh,
    # which would overwrite TORQAPPHOME/TORQPROCESSES/etc back to lib/torq's
    # own defaults) - generate our own and point SETENV at it instead. Each
    # value shell-quoted, as deploy.env's are: torq.sh sources this file, and
    # a `$` or backtick in a path or QCMD must not be expanded or run (#2394).
    setenv_lines = [f"export {k}={shlex.quote(v)}" for k, v in env.items()]
    generated.write_text(target, "\n".join(setenv_lines) + "\n")
    env["SETENV"] = str(target)
    return env


def run_torq_sh(
    paths: UqsPaths,
    args: list[str],
    base_port: int | None = None,
    capture: bool = False,
    timeout: float | None = None,
    selected: Sequence[str] | None = None,
    skips: start_overrides.Overrides = start_overrides.NONE,
    lock_wait: float | None = None,
    before: Callable[[], subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Check the TorQ launcher, bootstrap, then run the launcher with *args*
    under the generated env: `$TORQHOME/torq.sh`, or `UQS_TORQ_LAUNCHER`
    (stack/launcher.py). A launcher that cannot run is refused before
    bootstrap writes anything. *args* naming attachments' publishers alone
    prepares only them (bootstrap_publisher, #1847).

    A `start` or `restart` is refused first by start_policy.refuse_start -
    here, so no caller (`uqs start`, `uqs job backfill`, `uqs replay`, `uqs exec`)
    can skip it (#964).

    `timeout` is seconds to wait before giving up, or None to wait forever -
    which is the right default for `start`/`stop`, whose whole job is to wait
    for something slow. A read-only command that an operator runs to find out
    what is going on should not be the thing that hangs, so `uqs status` sets
    one.

    `selected` is a profile start's set: what TorQ discovers (stack/deployment.py).
    `skips` names refusals to skip for this start (stack/start_overrides.py).

    From bootstrap to torq.sh's exit the stack's lifecycle lock is held
    (stack/generated.py, #2028), so two commands on one stack - `uqs` and the
    frontend, or two tabs - cannot interleave. `lock_wait` is how long to wait
    for another holder: None waits as long as it takes, 0 refuses at once.
    `before` runs once the lock is held; its non-zero exit is returned, unlaunched.
    """
    start_policy.refuse_start(paths, args, base_port, skips)
    args = start_policy.with_publisher_extras(paths, args)
    # The launcher first: bootstrap writes the data directory, process.csv and setenv.sh,
    # and a launcher that is going to be refused must not have had that written for it.
    launcher = torq_launcher(paths)
    with generated.lifecycle_lock(paths, wait=lock_wait):
        if before is not None and (first := before()).returncode:
            return first  # `before` failed - restart's stop left one running
        return _bootstrap_and_run(paths, args, launcher, base_port, capture, timeout, selected)


def _bootstrap_and_run(
    paths: UqsPaths,
    args: list[str],
    launcher: Path,
    base_port: int | None,
    capture: bool,
    timeout: float | None,
    selected: Sequence[str] | None,
) -> subprocess.CompletedProcess[str]:
    """run_torq_sh's work once it holds the stack: bootstrap, then the launcher."""
    plan = deployment.planned(paths, args, selected)
    if attached := attached_publisher.publishers(args):
        starting = bool(args) and args[0] in start_policy.STARTING_VERBS
        overrides = bootstrap_publisher(paths, attached, base_port, plan, starting=starting)
    else:
        overrides = bootstrap(paths, base_port=base_port, selected=plan)
    # subprocess.run's env= *replaces* the environment rather than extending it - merge onto
    # the inherited one (PATH, etc.) or envsubst and q stop resolving, though on PATH.
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


#: What `trace=True` appends to each start line, through torq.sh's `-extras`:
#: the flag uqf's process scripts read to switch source-query tracing on before
#: their first source request, as torq_backfill.q always has (#2675).
TRACE_EXTRAS = ("-trace",)


def _extras(trace: bool) -> list[str]:
    """torq.sh's `-extras` for a start or restart: `-trace`, or nothing."""
    return ["-extras", *TRACE_EXTRAS] if trace else []


def start(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int | None = None,
    trace: bool = False,
    **kw: Any,
):
    """`selected=` a profile's resolved set; `capture=`, `timeout=` as run_torq_sh.
    `trace` starts each process with `-trace` (TRACE_EXTRAS)."""
    kw.setdefault("capture", False)
    return run_torq_sh(paths, ["start", procs, *_extras(trace)], base_port=base_port, **kw)


def stop(paths: UqsPaths, procs: str = "all", base_port: int | None = None, **kw: Any):
    """SIGTERM, confirm, SIGKILL with `kill=True`: stack/stopping.py (#2689)."""
    return stopping.stop(paths, procs, base_port, **kw)


def restart(paths: UqsPaths, procs: str = "all", base_port: int | None = None, **kw: Any):
    """torq.sh's restart once the stop policy has confirmed every pid gone -
    its own stop raced the dying one; a survivor starts nothing (#2689)."""
    kw.setdefault("capture", False)
    first = lambda: stop(paths, procs, base_port, capture=kw["capture"])  # noqa: E731
    args = ["restart", procs, *_extras(kw.pop("trace", False))]
    return run_torq_sh(paths, args, base_port=base_port, before=first, **kw)


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
    selected: Sequence[str] | None = None,
    trace: bool = False,
) -> subprocess.CompletedProcess[str]:
    """What `torq.sh print` shows for `procs`, composed without bootstrapping:
    a dry run writes nothing (stack/start_lines.py, #1880). `trace` as `start`'s."""
    extras = TRACE_EXTRAS if trace else ()
    lines = start_lines.plan(paths, procs, base_port, selected, extras=extras)
    text = "".join(f"{line.printed()}\n" for line in lines)
    return subprocess.CompletedProcess(["print", procs], 0, text, "")


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
