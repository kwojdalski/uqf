"""The generated configuration is replaced whole, and one lifecycle command
runs on a stack at a time (#2028).

Every start, stop and restart rewrites process.csv. It used to truncate the
file and then write it, so a reader in between - the frontend's Processes
view, or a second command's torq.sh - saw a short fleet and could not tell.
"""

from __future__ import annotations

import csv
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from uqs.paths import UqsPaths
from uqs.stack import generated, runtime
from uqs.stack.site_fields import process_csv_fields


def _paths(tmp_path: Path) -> UqsPaths:
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "lib" / "torq-finance-starter-pack",
        torqdata=tmp_path / "output" / "uqs",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "python" / "uqs",
    )


def _rows(n: int) -> list[dict[str, str]]:
    fields = process_csv_fields()
    return [
        {f: "" for f in fields}
        | {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{i}",
            "proctype": "rdb",
            "procname": f"rdb{i}",
            "load": "x.q",
            "startwithall": "1",
            "qcmd": "q",
        }
        for i in range(n)
    ]


def test_a_reader_never_sees_a_partial_process_csv(tmp_path):
    """What the frontend's Processes view does while a start rewrites the
    file: every read sees the whole fleet, the old one or the new one."""
    paths = _paths(tmp_path)
    paths.torqdata.mkdir(parents=True)
    rows = _rows(64)
    generated.write_process_csv(paths, rows)
    stop = threading.Event()

    def rewrite() -> None:
        while not stop.is_set():
            generated.write_process_csv(paths, rows)

    writer = threading.Thread(target=rewrite)
    writer.start()
    try:
        seen = []
        for _ in range(2000):
            with paths.generated_procs.open(newline="") as f:
                seen.append(len(list(csv.DictReader(f))))
    finally:
        stop.set()
        writer.join()
    assert set(seen) == {64}


def test_setenv_sh_is_replaced_whole(tmp_path, monkeypatch):
    """torq.sh sources it: a prefix of it is a stack started half-configured."""
    paths = _paths(tmp_path)
    paths.torqdata.mkdir(parents=True)
    replaced: list[Path] = []
    real = generated.os.replace
    monkeypatch.setattr(
        generated.os, "replace", lambda a, b: replaced.append(Path(b)) or real(a, b)
    )
    runtime.write_setenv(paths, 7000)
    assert replaced == [paths.generated_setenv]
    assert 'export KDBBASEPORT="7000"' in paths.generated_setenv.read_text()


def _held_elsewhere(paths: UqsPaths):
    """Hold the stack's lock on another thread until the returned event is set."""
    taken, release = threading.Event(), threading.Event()

    def hold() -> None:
        with generated.lifecycle_lock(paths):
            taken.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    assert taken.wait(10)
    return thread, release


def test_a_lifecycle_command_is_refused_while_another_holds_the_stack(tmp_path, monkeypatch):
    """The frontend refuses (wait=0) rather than queue a second Start behind
    the first, and nothing of the second is bootstrapped or run."""
    paths = _paths(tmp_path)
    monkeypatch.setattr(runtime.start_policy, "refuse_start", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "torq_launcher", lambda p: Path("/bin/true"))
    monkeypatch.setattr(runtime, "bootstrap", lambda *a, **k: pytest.fail("bootstrapped"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran torq.sh"))
    thread, release = _held_elsewhere(paths)
    try:
        with pytest.raises(generated.StackBusy, match="another start, stop or restart"):
            runtime.run_torq_sh(paths, ["stop", "rdb1"], lock_wait=0)
    finally:
        release.set()
        thread.join()


def test_a_lifecycle_command_waits_its_turn_by_default(tmp_path, monkeypatch):
    """A terminal's `uqs start` queues behind the other command instead."""
    paths = _paths(tmp_path)
    monkeypatch.setattr(runtime.start_policy, "refuse_start", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "torq_launcher", lambda p: Path("/bin/true"))
    order: list[str] = []
    monkeypatch.setattr(runtime, "bootstrap", lambda *a, **k: order.append("bootstrap") or {})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: order.append("ran"))
    thread, release = _held_elsewhere(paths)
    done = threading.Event()
    runner = threading.Thread(
        target=lambda: (runtime.run_torq_sh(paths, ["stop", "rdb1"]), done.set())
    )
    runner.start()
    assert not done.wait(0.3), "ran while the other command held the stack"
    order.append("released")
    release.set()
    thread.join()
    runner.join(10)
    assert order == ["released", "bootstrap", "ran"]


def test_the_lock_is_reentrant_within_a_thread(tmp_path):
    """The frontend holds it around a whole action, whose run_torq_sh takes it again."""
    paths = _paths(tmp_path)
    with generated.lifecycle_lock(paths, wait=0), generated.lifecycle_lock(paths, wait=0):
        pass
    seen: dict[str, Any] = {}
    with generated.lifecycle_lock(paths, wait=0):
        seen["held"] = True
    assert seen == {"held": True}
