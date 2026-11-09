"""Concurrent writers keep deploy_history.json whole (#2578).

Every writer used the same deploy_history.tmp with no lock around its
read-append-write, so two pushes from one workstation interleaved bytes in
that file, renamed it from under each other, and could publish a mix that no
later read accepted.
"""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

from uqs.deploy import history

WRITERS, EACH = 4, 60


def _write_many(path: str, writer: int) -> None:
    os.environ[history.HISTORY_ENV] = path
    for n in range(EACH):
        history.record_adopted(Path(path).parent, f"t{writer}", "h", "/srv/uqf", None, f"r{n}")


def test_concurrent_writers_lose_no_entry_and_leave_the_file_readable(tmp_path, monkeypatch):
    path = tmp_path / "deploy_history.json"
    monkeypatch.setenv(history.HISTORY_ENV, str(path))
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_write_many, args=(str(path), w)) for w in range(WRITERS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert [p.exitcode for p in procs] == [0] * WRITERS
    entries = history.read(tmp_path)
    assert len(entries) == WRITERS * EACH
    assert not [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]


def test_a_rename_rewrites_only_the_matching_entries(tmp_path, monkeypatch):
    monkeypatch.setenv(history.HISTORY_ENV, str(tmp_path / "deploy_history.json"))
    history.record_adopted(tmp_path, "uat", "h", "/srv/a", None, "r1")
    history.record_adopted(tmp_path, "prod", "h", "/srv/b", None, "r2")
    history.rename_target(tmp_path, "uat", "uat2")
    assert [e["target"] for e in history.read(tmp_path)] == ["uat2", "prod"]
