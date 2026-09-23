"""The crypto recorders' lifecycle: build, spawn, track, refuse, stop.

test_core.py covers the easy edges - the root lookup, the YAML, a missing pid
file. external/crypto.py still sat at 45%: the start paths themselves, and nearly all
of the fills recorder, had never run.

No Rust is built here. `cargo build` is replaced by a recorded call, and the
recorder binary by a real `sleep` process - so the pid file, the liveness
check (`os.kill(pid, 0)`) and the SIGTERM that stops it all act on a genuine
process rather than on a mock that would agree with whatever the code did.

The fills recorder writes to TWO tables, and the one assertion here that
matters most is which is which: `crypto_sim_fills` takes paper fills and
`crypto_trades` real confirmed executions. Swapped, simulated fills would sit
in the table a reader trusts as real trading.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from uqf_stack.external import crypto
from uqf_stack.paths import UqfStackError, UqfStackPaths


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> UqfStackPaths:
    root = tmp_path / "cryptorust"
    root.mkdir()
    (root / "Cargo.toml").write_text("[package]\nname='cryptorust'\n")
    monkeypatch.setenv(crypto.CRYPTORUST_ROOT_ENV, str(root))
    return UqfStackPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "lib" / "starter",
        torqdata=tmp_path / "data",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "orch",
    )


@pytest.fixture
def fake_build(monkeypatch):
    """Record cargo invocations, spawn `sleep` in place of the recorder, and
    kill whatever was spawned when the test ends."""
    record: dict[str, Any] = {"builds": [], "spawned": [], "returncode": 0, "stderr": ""}
    monkeypatch.setattr(crypto, "get_process_config", lambda p, n, base_port: {"port": "6051"})

    def run(cmd, **kw):
        record["builds"].append((cmd, kw))
        return SimpleNamespace(returncode=record["returncode"], stderr=record["stderr"])

    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        record["spawned"].append((cmd, kw))
        return real_popen(["sleep", "30"], stdout=kw.get("stdout"), stderr=kw.get("stderr"))

    monkeypatch.setattr(crypto.subprocess, "run", run)
    monkeypatch.setattr(crypto.subprocess, "Popen", popen)
    return record


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_dead(pid: int, seconds: float = 3) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            # Reap it if it is our child, so a zombie does not read as alive.
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------- book recorder


def test_start_builds_writes_config_spawns_and_records_the_pid(paths, fake_build):
    pid = crypto.start_crypto_recorder(paths, venues=("binance_spot",), symbols=("BTC-USDT",))
    try:
        ((cmd, kw),) = fake_build["builds"]
        assert cmd[:2] == ["cargo", "build"] and "kdb-market-data-recorder" in cmd
        assert kw["env"]["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] == "1"
        assert "port: 6051" in paths.crypto_recorder_config_path.read_text(), (
            "the recorder is pointed at stp1's resolved port"
        )
        ((spawn_cmd, _),) = fake_build["spawned"]
        assert spawn_cmd[-2:] == ["--config", str(paths.crypto_recorder_config_path)]
        assert paths.crypto_recorder_pid_path.read_text() == str(pid)
        assert crypto.is_crypto_recorder_running(paths)
        assert crypto.crypto_recorder_status(paths)["running"] == "True"
    finally:
        crypto.stop_crypto_recorder(paths)


def test_a_second_start_is_refused_while_the_first_is_alive(paths, fake_build):
    """Two recorders publishing the same books would double every row."""
    crypto.start_crypto_recorder(paths)
    try:
        with pytest.raises(UqfStackError, match="already running"):
            crypto.start_crypto_recorder(paths)
        assert len(fake_build["spawned"]) == 1
    finally:
        crypto.stop_crypto_recorder(paths)


def test_stop_terminates_the_process_and_removes_the_pid_file(paths, fake_build):
    pid = crypto.start_crypto_recorder(paths)
    crypto.stop_crypto_recorder(paths)
    assert _wait_dead(pid), "SIGTERM ended the recorder"
    assert not paths.crypto_recorder_pid_path.exists()
    assert crypto.crypto_recorder_status(paths)["running"] == "False"


def test_stop_tolerates_a_process_that_already_exited(paths, fake_build):
    """A recorder that crashed leaves its pid file behind. Stop must clear
    it rather than fail on the missing process and leave `start` refusing."""
    pid = crypto.start_crypto_recorder(paths)
    os.kill(pid, 15)
    assert _wait_dead(pid)
    crypto.stop_crypto_recorder(paths)
    assert not paths.crypto_recorder_pid_path.exists()


def test_a_failed_build_is_reported_with_cargo_s_output_and_spawns_nothing(paths, fake_build):
    fake_build["returncode"] = 101
    fake_build["stderr"] = "error[E0433]: failed to resolve"
    with pytest.raises(UqfStackError, match="E0433"):
        crypto.start_crypto_recorder(paths)
    assert fake_build["spawned"] == []
    assert not paths.crypto_recorder_pid_path.exists()


def test_a_corrupt_pid_file_reads_as_not_running(paths):
    """A half-written pid file must not crash status or block a start."""
    paths.orchestrator_dir.mkdir(parents=True)
    paths.crypto_recorder_pid_path.write_text("not-a-pid")
    assert crypto.is_crypto_recorder_running(paths) is False
    assert crypto.crypto_recorder_status(paths)["pid"] == ""


# ------------------------------------------------------------ fills recorder


def test_fills_recorder_sends_paper_and_real_fills_to_the_right_tables(paths, fake_build):
    """The assertion that matters most in this file. Swapped, simulated fills
    would sit in the table a reader trusts as real trading."""
    crypto.start_crypto_fills_recorder(
        paths, oms_socket_path="/tmp/oms.sock", symbol="ETH-USDT", poll_interval_ms=250
    )
    try:
        ((cmd, _),) = fake_build["spawned"]
        flags = dict(zip(cmd[1::2], cmd[2::2], strict=False))
        assert flags["--kdb-table"] == "crypto_sim_fills"
        assert flags["--real-kdb-table"] == "crypto_trades"
        assert flags["--oms-socket-path"] == "/tmp/oms.sock"
        assert flags["--kdb-port"] == "6051"
        assert flags["--poll-interval-ms"] == "250"
        assert flags["--symbol"] == "ETH-USDT"
        status = crypto.crypto_fills_recorder_status(paths)
        assert (status["sim_table"], status["real_table"]) == ("crypto_sim_fills", "crypto_trades")
        assert status["running"] == "True"
    finally:
        crypto.stop_crypto_fills_recorder(paths)


def test_fills_recorder_builds_its_own_binary(paths, fake_build):
    crypto.start_crypto_fills_recorder(paths)
    try:
        ((cmd, _),) = fake_build["builds"]
        assert "kdb-fills-recorder" in cmd
    finally:
        crypto.stop_crypto_fills_recorder(paths)


def test_the_two_recorders_track_separate_pids(paths, fake_build):
    """Independent lifecycles: stopping one must not stop, or forget, the other."""
    book = crypto.start_crypto_recorder(paths)
    fills = crypto.start_crypto_fills_recorder(paths)
    try:
        assert book != fills
        crypto.stop_crypto_recorder(paths)
        assert crypto.is_crypto_fills_recorder_running(paths)
    finally:
        crypto.stop_crypto_fills_recorder(paths)
        _wait_dead(book)


def test_a_second_fills_start_is_refused(paths, fake_build):
    crypto.start_crypto_fills_recorder(paths)
    try:
        with pytest.raises(UqfStackError, match="already running"):
            crypto.start_crypto_fills_recorder(paths)
    finally:
        crypto.stop_crypto_fills_recorder(paths)


def test_fills_start_refuses_a_directory_that_is_not_a_cryptorust_checkout(paths, monkeypatch):
    monkeypatch.setenv(crypto.CRYPTORUST_ROOT_ENV, str(paths.repo_root / "nowhere"))
    with pytest.raises(UqfStackError, match="Cargo.toml"):
        crypto.start_crypto_fills_recorder(paths)


def test_a_failed_fills_build_spawns_nothing(paths, fake_build):
    fake_build["returncode"] = 1
    fake_build["stderr"] = "linker error"
    with pytest.raises(UqfStackError, match="linker error"):
        crypto.start_crypto_fills_recorder(paths)
    assert fake_build["spawned"] == []


def test_fills_stop_without_a_pid_file_is_refused(paths):
    with pytest.raises(UqfStackError, match="not running"):
        crypto.stop_crypto_fills_recorder(paths)


def test_fills_stop_tolerates_an_exited_process(paths, fake_build):
    pid = crypto.start_crypto_fills_recorder(paths)
    os.kill(pid, 15)
    assert _wait_dead(pid)
    crypto.stop_crypto_fills_recorder(paths)
    assert not paths.crypto_fills_recorder_pid_path.exists()


def test_fills_status_when_never_started(paths):
    status = crypto.crypto_fills_recorder_status(paths)
    assert status["running"] == "False" and status["pid"] == ""


def test_a_corrupt_fills_pid_file_reads_as_not_running(paths):
    paths.orchestrator_dir.mkdir(parents=True)
    paths.crypto_fills_recorder_pid_path.write_text("garbage")
    assert crypto.is_crypto_fills_recorder_running(paths) is False
