"""Tests for per-process load times (stack/startup.py) and `summary --debug`.

The log files here are built line for line in torq.q's own shapes - the
7-field `.lg.format` line and `.lg.banner`'s raw 80-column border - because
the measurement is only as right as its reading of those shapes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.stack import listing, probe, runtime, startup
from uqs.stack.startup import BANNER_BORDER, Startup

runner = CliRunner()


def _line(clock: str, ident: str, message: str, proc: str = "rdb1") -> str:
    return f"2026.09.24D{clock}|host|rdb|{proc}|INF|{ident}|{message}"


def _banner() -> list[str]:
    return [BANNER_BORDER, "#" + " " * 78 + "#", "#   TorQ v5   #".ljust(80), BANNER_BORDER]


def _write(log_dir: Path, name: str, lines: list[str]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / name).write_text("\n".join(lines) + "\n")


def _full_start(proc: str = "rdb1") -> list[str]:
    return [
        _line("08:00:00.000000000", "logging", "creating alias", proc),
        *_banner(),
        _line("08:00:00.500000000", "fileload", "loading code/common", proc),
        _line("08:00:03.250000000", "init", "no initialisation functions found", proc),
        *_banner(),
        _line("08:05:00.000000000", "sub", "a message long after loading", proc),
    ]


def test_a_complete_start_is_timed_from_the_first_line_to_the_closing_banner(tmp_path):
    _write(tmp_path, "out_rdb1_2026_09_24_08_00_00_000.log", _full_start())
    got = startup.read_startup(tmp_path, "rdb1")
    assert got.seconds == pytest.approx(3.25)
    assert got.started == datetime(2026, 9, 24, 8, 0, 0)
    assert got.note == ""


def test_a_start_with_no_closing_banner_is_reported_as_still_loading(tmp_path):
    _write(tmp_path, "out_rdb1_2026_09_24_08_00_00_000.log", _full_start()[:7])
    got = startup.read_startup(tmp_path, "rdb1")
    assert got.seconds is None
    assert "still loading" in got.note
    assert got.started == datetime(2026, 9, 24, 8, 0, 0)


def test_a_daily_roll_is_skipped_for_the_file_that_holds_the_start(tmp_path):
    """After midnight the newest file is a continuation with no start in it.
    Reading it would report "still loading" for a process that loaded hours
    ago."""
    _write(tmp_path, "out_rdb1_2026_09_24_08_00_00_000.log", _full_start())
    rolled = [*_banner(), _line("00:00:00.000000000", "sub", "after the roll")]
    _write(tmp_path, "out_rdb1_2026_09_25_00_00_00_000.log", rolled)
    assert startup.read_startup(tmp_path, "rdb1").seconds == pytest.approx(3.25)


def test_the_newest_start_wins_over_an_older_one(tmp_path):
    _write(tmp_path, "out_rdb1_2026_09_23_08_00_00_000.log", _full_start())
    newer = _full_start()
    # The init line: after the alias line and the four-line opening banner.
    newer[1 + len(_banner()) + 1] = _line("08:00:09.000000000", "init", "slow this time")
    _write(tmp_path, "out_rdb1_2026_09_24_08_00_00_000.log", newer)
    assert startup.read_startup(tmp_path, "rdb1").seconds == pytest.approx(9.0)


def test_one_process_s_files_are_not_mistaken_for_another_s(tmp_path):
    """`out_hdb1_*` must not collect `out_hdb12_*`, nor the alias."""
    _write(tmp_path, "out_hdb12_2026_09_24_08_00_00_000.log", _full_start("hdb12"))
    _write(tmp_path, "out_hdb1.log", _full_start("hdb1"))
    got = startup.read_startup(tmp_path, "hdb1")
    assert got.seconds is None
    assert "no startup log" in got.note


def test_kdb_timestamps_parse_to_the_microsecond():
    assert startup.parse_kdb_timestamp("2026.09.24D08:12:33.123456789") == datetime(
        2026, 9, 24, 8, 12, 33, 123456
    )
    assert startup.parse_kdb_timestamp("2026.09.24D08:12:33") == datetime(2026, 9, 24, 8, 12, 33)
    assert startup.parse_kdb_timestamp("not a time") is None


# ------------------------------------------------------------ summary --debug


def _summary_with_one_process(monkeypatch) -> list[list[str]]:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    completed = type("Completed", (), {"returncode": 0, "stdout": ""})()
    monkeypatch.setattr(runtime, "summary", lambda *_a, **_k: completed)
    monkeypatch.setattr(listing, "configured_ports", lambda *_a, **_k: {})
    monkeypatch.setattr(listing, "heartbeat_states", lambda *_a, **_k: {})
    monkeypatch.setattr(probe, "probe_all", lambda *_a, **_k: {})
    row = {"Time": "", "Process": "rdb1", "Status": "up", "PID": "1", "Port": "6052",
           "PortSource": "reported", "Heartbeat": "ok"}  # fmt: skip
    monkeypatch.setattr(listing, "summary_rows", lambda *_a, **_k: [row])
    asked: list[list[str]] = []

    def fake(_log_dir, procnames):
        asked.append(procnames)
        return [Startup("rdb1", datetime(2026, 9, 24, 8), 12.345)]

    monkeypatch.setattr(startup, "read_startups", fake)
    return asked


@pytest.mark.parametrize(
    "argv", [["summary", "--debug"], ["--debug", "summary"]], ids=["command flag", "global flag"]
)
def test_debug_adds_each_process_s_load_time(monkeypatch, argv):
    asked = _summary_with_one_process(monkeypatch)
    result = runner.invoke(cli.app, [*argv, "--columns", "status"])
    assert result.exit_code == 0, result.output
    assert asked == [["rdb1"]]
    assert "12.35s" in result.output


def test_log_level_debug_adds_the_load_times_too(monkeypatch):
    asked = _summary_with_one_process(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert runner.invoke(cli.app, ["summary", "--columns", "status"]).exit_code == 0
    assert asked == [["rdb1"]]


def test_a_plain_summary_reads_no_logs(monkeypatch):
    asked = _summary_with_one_process(monkeypatch)
    result = runner.invoke(cli.app, ["summary", "--columns", "status"])
    assert result.exit_code == 0, result.output
    assert asked == []
    assert "Load time" not in result.output


def test_only_the_head_of_a_long_log_is_read(tmp_path, monkeypatch):
    """A log that never rolls grows for as long as the process runs. The start
    is at its top, so nothing past the closing banner should be read."""
    lines = _full_start() + [_line("09:00:00.000000000", "sub", "x")] * 50_000
    _write(tmp_path, "out_rdb1_2026_09_24_08_00_00_000.log", lines)
    assert len(startup._head(tmp_path / "out_rdb1_2026_09_24_08_00_00_000.log")) == 8
    assert startup.read_startup(tmp_path, "rdb1").seconds == pytest.approx(3.25)


def test_a_start_that_never_finished_is_read_only_to_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "MAX_HEAD_LINES", 10)
    lines = _full_start()[:7] + [_line("08:00:04.000000000", "init", "still going")] * 100
    _write(tmp_path, "out_rdb1_2026_09_24_08_00_00_000.log", lines)
    assert len(startup._head(tmp_path / "out_rdb1_2026_09_24_08_00_00_000.log")) == 10
    assert "still loading" in startup.read_startup(tmp_path, "rdb1").note
