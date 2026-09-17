"""The cryptorust recorders - two external, non-TorQ publishers.

Neither is a process.csv-registered process: each is a Rust binary that
opens its own kdb+ IPC handle straight to the tickerplant and calls .u.upd,
the same wire protocol the q feeds use. So they are started and stopped
here rather than by torq.sh."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from torq_orchestrator.logger import get_logger
from torq_orchestrator.paths import UqfStackError, UqfStackPaths
from torq_orchestrator.pipelines import DEFAULT_BASE_PORT
from torq_orchestrator.procs import get_process_config

log = get_logger(__name__)


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


def cryptorust_root(paths: UqfStackPaths) -> Path:
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


def _read_crypto_recorder_pid(paths: UqfStackPaths) -> int | None:
    if not paths.crypto_recorder_pid_path.is_file():
        return None
    try:
        return int(paths.crypto_recorder_pid_path.read_text().strip())
    except ValueError:
        return None


def is_crypto_recorder_running(paths: UqfStackPaths) -> bool:
    pid = _read_crypto_recorder_pid(paths)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # signal 0: existence check only, doesn't actually signal
    except OSError:
        return False
    return True


def start_crypto_recorder(
    paths: UqfStackPaths,
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
        raise UqfStackError(
            f"{root} doesn't look like a cryptorust checkout (no Cargo.toml) - "
            f"set ${CRYPTORUST_ROOT_ENV} if it's checked out somewhere else"
        )
    if is_crypto_recorder_running(paths):
        raise UqfStackError("crypto recorder is already running - stop it first")

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
        raise UqfStackError(f"cargo build failed:\n{build.stderr}")

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


def stop_crypto_recorder(paths: UqfStackPaths) -> None:
    pid = _read_crypto_recorder_pid(paths)
    if pid is None:
        raise UqfStackError("crypto recorder is not running (no pid file)")
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


def crypto_recorder_status(paths: UqfStackPaths) -> dict[str, str]:
    pid = _read_crypto_recorder_pid(paths)
    return {
        "running": str(is_crypto_recorder_running(paths)),
        "pid": str(pid) if pid is not None else "",
        "table": CRYPTO_RECORDER_TABLE,
        "config": str(paths.crypto_recorder_config_path),
        "log": str(paths.torqdata / "logs" / "crypto_recorder.log"),
    }


# ---------------------------------------------------------------------------
# crypto fills recorder - kdb-fills-recorder, cryptorust's fills publisher
# (src/bin/kdb_fills_recorder.rs) - polls BOTH get_recent_fills
# (SIMULATED/paper, -> crypto_sim_fills) and get_recent_real_fills (real
# confirmed executions, -> crypto_trades) each tick, into two separate
# tables. Independent lifecycle from start_crypto_recorder above: that one
# owns a Supervisor + live exchange connectors and needs a generated YAML
# config; this one just polls an already-running cryptorust service's OMS
# IPC socket (no connectors of its own), so plain CLI flags are enough -
# no config file to generate. Can run with or without the book recorder.
# ---------------------------------------------------------------------------

CRYPTO_FILLS_RECORDER_TABLE = "crypto_sim_fills"
CRYPTO_REAL_FILLS_RECORDER_TABLE = "crypto_trades"
DEFAULT_OMS_SOCKET_PATH = "/tmp/beacon.sock"
CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL = "BTC-USDT"
CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS = 1000


def _read_crypto_fills_recorder_pid(paths: UqfStackPaths) -> int | None:
    if not paths.crypto_fills_recorder_pid_path.is_file():
        return None
    try:
        return int(paths.crypto_fills_recorder_pid_path.read_text().strip())
    except ValueError:
        return None


def is_crypto_fills_recorder_running(paths: UqfStackPaths) -> bool:
    pid = _read_crypto_fills_recorder_pid(paths)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def start_crypto_fills_recorder(
    paths: UqfStackPaths,
    base_port: int = DEFAULT_BASE_PORT,
    oms_socket_path: str = DEFAULT_OMS_SOCKET_PATH,
    symbol: str = CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    poll_interval_ms: int = CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS,
) -> int:
    """Build (if needed) and launch cryptorust's kdb-fills-recorder,
    pointed at this demo's own stp1 - publishes SIMULATED (paper) fills
    into `crypto_sim_fills` (CRYPTO_SIM_FILLS_TABLE_SCHEMA) and real
    confirmed executions into `crypto_trades` (CRYPTO_TRADES_TABLE_SCHEMA)
    - see that binary's own doc header for the full trace of how each
    source differs. `oms_socket_path` must point at an already-running
    cryptorust service's IPC socket (its own `ipc.socket_path` config,
    default /tmp/beacon.sock) - this recorder has no exchange connectors
    of its own, it only polls that socket. Returns the spawned PID.
    """
    root = cryptorust_root(paths)
    if not (root / "Cargo.toml").is_file():
        raise UqfStackError(
            f"{root} doesn't look like a cryptorust checkout (no Cargo.toml) - "
            f"set ${CRYPTORUST_ROOT_ENV} if it's checked out somewhere else"
        )
    if is_crypto_fills_recorder_running(paths):
        raise UqfStackError("crypto fills recorder is already running - stop it first")

    stp1 = get_process_config(paths, "stp1", base_port=base_port)

    # same PyO3/system-Python workaround as start_crypto_recorder above.
    env = {**os.environ, "PYO3_USE_ABI3_FORWARD_COMPATIBILITY": "1"}
    log.info("building cryptorust's kdb-fills-recorder (first run may take a while)...")
    build = subprocess.run(
        ["cargo", "build", "--quiet", "--features", "standalone", "--bin", "kdb-fills-recorder"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise UqfStackError(f"cargo build failed:\n{build.stderr}")

    binary = root / "target" / "debug" / "kdb-fills-recorder"
    (paths.torqdata / "logs").mkdir(parents=True, exist_ok=True)
    log_path = paths.torqdata / "logs" / "crypto_fills_recorder.log"
    with log_path.open("w") as log_file:
        process = subprocess.Popen(
            [
                str(binary),
                "--oms-socket-path",
                oms_socket_path,
                "--kdb-host",
                "localhost",
                "--kdb-port",
                str(stp1["port"]),
                "--kdb-credential",
                CRYPTO_RECORDER_CREDENTIAL,
                "--kdb-table",
                CRYPTO_FILLS_RECORDER_TABLE,
                "--real-kdb-table",
                CRYPTO_REAL_FILLS_RECORDER_TABLE,
                "--poll-interval-ms",
                str(poll_interval_ms),
                "--symbol",
                symbol,
            ],
            cwd=root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    paths.orchestrator_dir.mkdir(parents=True, exist_ok=True)
    paths.crypto_fills_recorder_pid_path.write_text(str(process.pid))
    log.info(
        "started cryptorust kdb-fills-recorder (pid {}), polling {} - "
        "SIMULATED fills -> {}, real fills -> {} - logging to {}",
        process.pid,
        oms_socket_path,
        CRYPTO_FILLS_RECORDER_TABLE,
        CRYPTO_REAL_FILLS_RECORDER_TABLE,
        log_path,
    )
    return process.pid


def stop_crypto_fills_recorder(paths: UqfStackPaths) -> None:
    pid = _read_crypto_fills_recorder_pid(paths)
    if pid is None:
        raise UqfStackError("crypto fills recorder is not running (no pid file)")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    paths.crypto_fills_recorder_pid_path.unlink(missing_ok=True)
    log.info("stopped cryptorust kdb-fills-recorder (pid {})", pid)


def crypto_fills_recorder_status(paths: UqfStackPaths) -> dict[str, str]:
    pid = _read_crypto_fills_recorder_pid(paths)
    return {
        "running": str(is_crypto_fills_recorder_running(paths)),
        "pid": str(pid) if pid is not None else "",
        "sim_table": CRYPTO_FILLS_RECORDER_TABLE,
        "real_table": CRYPTO_REAL_FILLS_RECORDER_TABLE,
        "log": str(paths.torqdata / "logs" / "crypto_fills_recorder.log"),
    }
