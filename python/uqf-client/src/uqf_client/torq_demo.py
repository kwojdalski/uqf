"""Shared business logic for running the vendored TorQ Finance Starter Pack
demo (see docs/torq-demo.md) - bootstrapping the writable data directory,
generating process.csv/setenv.sh, and driving lib/torq/torq.sh.

Pure logic, no CLI/MCP framework concerns - scripts/torq_demo.py (Typer)
and scripts/torq_demo_mcp.py (FastMCP) both import from here so the two
front ends can't drift out of sync. This is the Python port of the
original scripts/torq_demo.sh (see git history) - same env-var bridging
approach (TorQ's own installtorqapp.sh convention: point TORQHOME/
TORQAPPHOME/etc at wherever the framework and app live, generate an
overlay process.csv/setenv.sh rather than editing either vendored tree),
just with real error handling and a shared library instead of a shell
script.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uqf_client.logger import get_logger

log = get_logger(__name__)

DEFAULT_BASE_PORT = 6010
FXFEED_PORT_OFFSET = 19  # the one offset the vendored process.csv leaves free


class TorqDemoError(RuntimeError):
    """Raised for anything that stops the demo from being runnable as-is."""


@dataclass(frozen=True)
class TorqDemoPaths:
    repo_root: Path
    torqhome: Path
    torqapphome: Path
    torqdata: Path
    scripts_dir: Path

    @property
    def generated_procs(self) -> Path:
        return self.torqdata / "process.csv"

    @property
    def generated_setenv(self) -> Path:
        return self.torqdata / "setenv.sh"


def default_paths() -> TorqDemoPaths:
    # this file: <repo_root>/python/uqf-client/src/uqf_client/torq_demo.py
    repo_root = Path(__file__).resolve().parents[4]
    scripts_dir = repo_root / "scripts"
    return TorqDemoPaths(
        repo_root=repo_root,
        torqhome=repo_root / "lib" / "torq",
        torqapphome=repo_root / "lib" / "torq-finance-starter-pack",
        torqdata=repo_root / "scripts" / "output" / "torq-demo",
        scripts_dir=scripts_dir,
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
    # extra processes - currently just fxfeed1 (scripts/torq_fx_feed.q), a
    # second row-generating process publishing synthetic FX quotes into the
    # same `quote` table feed1 already writes equity quotes into.
    vendored_procs = paths.torqapphome / "appconfig" / "process.csv"
    procs_text = vendored_procs.read_text()
    if not procs_text.endswith("\n"):
        procs_text += "\n"
    procs_text += (
        f"localhost,{{KDBBASEPORT}}+{FXFEED_PORT_OFFSET},feed,fxfeed1,,1,0,,,"
        "${UQFSCRIPTS}/torq_fx_feed.q,1,,q\n"
    )
    paths.generated_procs.write_text(procs_text)

    env: dict[str, str] = {
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
