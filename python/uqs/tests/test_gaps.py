"""`uqs gaps`: the holes in a streaming job's uptime, read back by a fresh q (#630)."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import gaps as gaps_cli
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, paths_for_root
from uqs.stack import uptime

UQF_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()

#: markout up 00:00-10:00 and 10:20-24:00 on 2026-09-13; posbook never.
_WRITE = r"""
\l src/init.q
\l src/etl/init.q
.qetl.uptime.init_table[];
s:{[pid;a;b] `etl_stream_uptime insert (first 1?0Ng;`demo_markout;`demo_markout1;.z.h;pid;a;b)};
s[1i;2026.09.13D00:00;2026.09.13D10:00];
s[2i;2026.09.13D10:20;2026.09.14D00:00];
.qetl.job.bounded.state.durable_set[.qetl.uptime.path[];.qetl.uptime.sessions[]];
exit 0
"""

DAY = (datetime(2026, 9, 13, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC))


@pytest.fixture(scope="module")
def status(tmp_path_factory) -> Path:
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    directory = tmp_path_factory.mktemp("status")
    script = directory / "write.q"
    script.write_text(_WRITE)
    result = subprocess.run(
        [str(q), str(script), "-q"],
        cwd=UQF_ROOT,
        env={**os.environ, "UQF_STATUS_DIR": str(directory)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return directory


def test_the_hole_between_two_sessions_and_the_twin_that_refills_it(status, monkeypatch):
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    holes, twins, sessions = uptime.gaps(paths_for_root(UQF_ROOT), "demo_markout", *DAY)
    assert [(h["range_from"], h["range_to"]) for h in holes] == [
        ("2026-09-13T10:00:00.000000000", "2026-09-13T10:20:00.000000000")
    ]
    assert twins == ["hdb_demo_markouts_backfill"]
    assert sessions == 2


def test_a_job_with_no_sessions_is_down_for_the_whole_range(status, monkeypatch):
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    holes, twins, sessions = uptime.gaps(paths_for_root(UQF_ROOT), "posbook", *DAY)
    assert len(holes) == 1 and sessions == 0
    assert twins == []


@pytest.mark.parametrize("job", ["markout;exit 0", "", "1abc", "a b"])
def test_a_job_name_is_checked_before_it_reaches_q(job):
    with pytest.raises(UqsError, match="not a streaming job name"):
        uptime.gaps(paths_for_root(UQF_ROOT), job, *DAY)


def test_an_empty_range_is_refused():
    with pytest.raises(UqsError, match="the range is empty"):
        uptime.gaps(paths_for_root(UQF_ROOT), "demo_markout", DAY[1], DAY[0])


def _gaps_cli(monkeypatch, holes, twins, sessions=1):
    monkeypatch.setattr(uptime, "gaps", lambda *a: (holes, twins, sessions))
    return runner.invoke(
        cli.app,
        ["gaps", "demo_markout", "--from", "2026-09-13", "--to", "2026-09-14"],
        env={"COLUMNS": "200"},
    )


_HOLE = {"range_from": "2026-09-13T10:00:00.000000000", "range_to": "2026-09-13T10:20:00.000000000"}


def test_the_cli_prints_each_gap_and_the_backfill_that_refills_it(monkeypatch):
    result = _gaps_cli(monkeypatch, [_HOLE], ["hdb_demo_markouts_backfill"])
    assert result.exit_code == 0, result.output
    assert "2026-09-13T10:20:00.000000000" in result.output
    assert (
        "uqs backfill hdb_demo_markouts_backfill --from 2026-09-13T10:00:00 "
        "--to 2026-09-13T10:20:00 --version <V>"
    ) in result.output


def test_the_cli_says_when_nothing_can_refill_the_gap(monkeypatch):
    result = _gaps_cli(monkeypatch, [_HOLE], [])
    assert "cannot be refilled from here" in result.output


def test_the_cli_says_when_no_uptime_was_ever_recorded(monkeypatch):
    result = _gaps_cli(monkeypatch, [_HOLE], [], sessions=0)
    assert "no uptime recorded" in result.output


def test_the_cli_says_when_the_job_was_up_throughout(monkeypatch):
    result = _gaps_cli(monkeypatch, [], ["hdb_demo_markouts_backfill"])
    assert result.exit_code == 0
    assert "up throughout" in result.output


def test_a_gap_today_prints_no_command_and_says_when_it_can_be_refilled(monkeypatch):
    """#770: the HDB writer refuses rows dated today, so no command is advised."""
    monkeypatch.setattr(gaps_cli, "_utc_today", lambda: date(2026, 9, 13))
    result = _gaps_cli(monkeypatch, [_HOLE], ["hdb_demo_markouts_backfill"])
    assert result.exit_code == 0, result.output
    assert "2026-09-13T10:20:00.000000000" in result.output, "the gap itself is still shown"
    assert "uqs backfill" not in result.output
    assert "after end of day" in result.output


def test_a_gap_across_midnight_into_today_is_refilled_up_to_midnight(monkeypatch):
    monkeypatch.setattr(gaps_cli, "_utc_today", lambda: date(2026, 9, 14))
    hole = {
        "range_from": "2026-09-13T23:00:00.000000000",
        "range_to": "2026-09-14T01:00:00.000000000",
    }
    result = _gaps_cli(monkeypatch, [hole], ["hdb_demo_markouts_backfill"])
    assert (
        "uqs backfill hdb_demo_markouts_backfill --from 2026-09-13T23:00:00 "
        "--to 2026-09-14T00:00:00 --version <V>"
    ) in result.output
    assert "after end of day" in result.output


def test_a_gap_before_today_is_advised_whole_and_nothing_is_said_about_today(monkeypatch):
    monkeypatch.setattr(gaps_cli, "_utc_today", lambda: date(2026, 9, 14))
    result = _gaps_cli(monkeypatch, [_HOLE], ["hdb_demo_markouts_backfill"])
    assert "--to 2026-09-13T10:20:00 --version <V>" in result.output
    assert "after end of day" not in result.output
