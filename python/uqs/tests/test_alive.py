"""Tests for stack/alive.py - the up/down check that replaced `torq.sh summary`.

`ps` and `lsof` are faked, and so is the process list, so nothing here
depends on what is running on the machine.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from uqs import paths as stack_paths
from uqs.paths import UqsError
from uqs.stack import alive, listing, runtime

ROWS = [
    {"host": "localhost", "proctype": "rdb", "procname": "rdb1"},
    {"host": "localhost", "proctype": "hdb", "procname": "hdb1"},
    {"host": "localhost", "proctype": "rdb", "procname": "rdb10"},
    {"host": "elsewhere.example", "proctype": "rdb", "procname": "remote1"},
]

PS = """\
  101 q torq.q -stackid 6050 -proctype rdb -procname rdb1 -U x
  102 q torq.q -stackid 6100 -proctype hdb -procname hdb1 -U x
  103 grep -stackid 6050 -proctype rdb -procname rdb1
  104 q torq.q -stackid 6050 -proctype rdb -procname rdb1 -U x
  105 q torq.q -stackid 6050 -proctype rdb -procname rdb10 -U x
"""

LSOF = "p104\nf5\nn*:6052\np105\nf6\nn127.0.0.1:6062\nf7\nn[::1]:6062\n"


@pytest.fixture
def fake_os(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"calls": []}

    def run(cmd, **kw):
        seen["calls"].append(cmd)
        out = PS if cmd[0] == "ps" else LSOF
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(alive.subprocess, "run", run)
    monkeypatch.setattr(alive, "_process_rows", lambda paths, base_port: ROWS)
    return seen


def _rows(table: str) -> dict[str, list[str]]:
    return {
        cells[1]: cells[2:]
        for cells in ([c.strip() for c in line.split("|")] for line in table.splitlines()[1:])
    }


def test_it_matches_what_torq_sh_findproc_matches(fake_os: dict[str, Any]) -> None:
    rows = _rows(alive.status_table(stack_paths.default_paths(), base_port=6050))
    # The newest of several matching pids, as torq.sh reports it.
    assert rows["rdb1"] == ["up", "104", "6052"]
    # Another stack's hdb1 (base port 6100) is not this stack's.
    assert rows["hdb1"] == ["down", ""]
    # rdb1's pattern ends in a space, so it does not match rdb10.
    assert rows["rdb10"] == ["up", "105", "6062"]
    # torq.sh prints no row for a process on another host.
    assert "remote1" not in rows


def test_it_is_two_subprocesses_however_many_processes(fake_os: dict[str, Any]) -> None:
    alive.status_table(stack_paths.default_paths(), base_port=6050)
    assert [c[0] for c in fake_os["calls"]] == ["ps", "lsof"]
    assert "104,105" in fake_os["calls"][1], "lsof is asked about the matched pids only"


def test_the_table_is_what_summary_rows_reads(fake_os: dict[str, Any]) -> None:
    table = alive.status_table(stack_paths.default_paths(), base_port=6050)
    parsed = listing.summary_rows(table, {"hdb1": "6053"}, None)
    by_name = {row["Process"]: row for row in parsed}
    assert by_name["rdb1"]["PortSource"] == "reported"
    assert (by_name["hdb1"]["Port"], by_name["hdb1"]["PortSource"]) == ("6053", "configured")


def test_running_is_the_up_procnames(fake_os: dict[str, Any]) -> None:
    assert alive.running(stack_paths.default_paths(), base_port=6050) == {"rdb1", "rdb10"}


def test_summary_no_longer_runs_torq_sh(fake_os: dict[str, Any], monkeypatch) -> None:
    def refuse(*_a, **_kw):
        raise AssertionError("summary must not bootstrap or shell out to torq.sh")

    monkeypatch.setattr(runtime, "run_torq_sh", refuse)
    result = runtime.summary(stack_paths.default_paths(), base_port=6050)
    assert result.returncode == 0
    assert result.stdout.startswith(alive.HEADER)


def test_a_missing_lsof_falls_back_to_configured_ports(monkeypatch) -> None:
    def run(cmd, **kw):
        if cmd[0] == "lsof":
            raise FileNotFoundError("lsof")
        return subprocess.CompletedProcess(cmd, 0, PS, "")

    monkeypatch.setattr(alive.subprocess, "run", run)
    monkeypatch.setattr(alive, "_process_rows", lambda paths, base_port: ROWS)
    rows = _rows(alive.status_table(stack_paths.default_paths(), base_port=6050))
    assert rows["rdb1"] == ["up", "104", ""]


def test_a_ps_that_hangs_is_a_refusal(monkeypatch) -> None:
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(alive.subprocess, "run", run)
    with pytest.raises(UqsError, match="within 2s"):
        alive.status_table(stack_paths.default_paths(), timeout=2)
