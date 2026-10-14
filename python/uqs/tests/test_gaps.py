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
    assert twins == {"hdb_demo_markouts_backfill": None}
    assert sessions == 2


def test_a_job_with_no_sessions_is_down_for_the_whole_range(status, monkeypatch):
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    holes, twins, sessions = uptime.gaps(paths_for_root(UQF_ROOT), "posbook", *DAY)
    assert len(holes) == 1 and sessions == 0
    assert twins == {}


@pytest.mark.parametrize("job", ["markout;exit 0", "", "1abc", "a b"])
def test_a_job_name_is_checked_before_it_reaches_q(job):
    with pytest.raises(UqsError, match="not a streaming job name"):
        uptime.gaps(paths_for_root(UQF_ROOT), job, *DAY)


def test_an_empty_range_is_refused():
    with pytest.raises(UqsError, match="the range is empty"):
        uptime.gaps(paths_for_root(UQF_ROOT), "demo_markout", DAY[1], DAY[0])


def _gaps_cli(monkeypatch, holes, twins, sessions=1):
    grains = twins if isinstance(twins, dict) else dict.fromkeys(twins)
    monkeypatch.setattr(uptime, "gaps", lambda *a: (holes, grains, sessions))
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


# --- a nanosecond gap, as .z.p records one on Linux (#1253) ------------------

_NS_HOLE = {
    "range_from": "2026-09-13T10:00:01.123456789",
    "range_to": "2026-09-13T10:04:07.987654321",
}


def _printed_command(output: str) -> str:
    return next(line.strip() for line in output.splitlines() if "uqs backfill" in line)


def test_a_nanosecond_gap_is_widened_to_the_microsecond_backfill_carries(monkeypatch):
    """The start rounds down and the end up: the refill covers the whole hole,
    and `parse_bound`, which stops at the microsecond, accepts both."""
    from uqs.stack import backfill

    result = _gaps_cli(monkeypatch, [_NS_HOLE], ["hdb_demo_markouts_backfill"])
    assert result.exit_code == 0, result.output
    command = _printed_command(result.output)
    assert "--from 2026.09.13D10:00:01.123456000 --to 2026.09.13D10:04:07.987655000" in command, (
        command
    )
    assert backfill.parse_bound("--from", command.split("--from ")[1].split()[0]) == datetime(
        2026, 9, 13, 10, 0, 1, 123456, tzinfo=UTC
    )
    assert backfill.parse_bound("--to", command.split("--to ")[1].split()[0]) == datetime(
        2026, 9, 13, 10, 4, 7, 987655, tzinfo=UTC
    )


def test_the_printed_refill_runs_through_the_backfill_cli(monkeypatch):
    """Pasted back into `uqs backfill`, the command reaches the start of a
    run with the widened range, rather than a refusal of its bounds."""
    import shlex

    from uqs.cli import backfill as backfill_cli

    command = _printed_command(
        _gaps_cli(monkeypatch, [_NS_HOLE], ["hdb_demo_markouts_backfill"]).output
    )
    started = []

    def start(paths, worker, version, bound_from, bound_to, **kw):
        started.append((worker, version, bound_from, bound_to))
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(backfill_cli.stack_backfill, "start", start)
    args = shlex.split(command.replace("<V>", "v1"))[1:]
    result = runner.invoke(cli.app, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert started == [
        (
            "hdb_demo_markouts_backfill",
            "v1",
            datetime(2026, 9, 13, 10, 0, 1, 123456, tzinfo=UTC),
            datetime(2026, 9, 13, 10, 4, 7, 987655, tzinfo=UTC),
        )
    ]


def test_an_end_a_nanosecond_before_midnight_rounds_up_into_the_next_day(monkeypatch):
    monkeypatch.setattr(gaps_cli, "_utc_today", lambda: date(2026, 9, 20))
    hole = {
        "range_from": "2026-09-13T23:00:00.000000000",
        "range_to": "2026-09-13T23:59:59.999999999",
    }
    result = _gaps_cli(monkeypatch, [hole], ["hdb_demo_markouts_backfill"])
    assert "--from 2026-09-13T23:00:00 --to 2026-09-14T00:00:00 --version" in result.output


# --- a bar-building twin is advised a range on its grid (#1323) --------------

_MINUTE_NS = 60 * 10**9


def test_the_declared_grain_of_each_twin_is_read_from_its_declaration(status, monkeypatch):
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    _, twins, _ = uptime.gaps(paths_for_root(UQF_ROOT), "exec_bars", *DAY)
    assert twins == {"hdb_exec_bars_backfill": _MINUTE_NS}
    _, twins, _ = uptime.gaps(paths_for_root(UQF_ROOT), "crypto_book_1s", *DAY)
    assert twins == {"hdb_crypto_book_backfill": 10**9}


@pytest.mark.parametrize(
    ("value", "align", "upper", "expected"),
    [
        ("2026-09-13T10:00:01.123456789", _MINUTE_NS, False, "2026-09-13T10:00:00.000000000"),
        ("2026-09-13T10:04:07.987654321", _MINUTE_NS, True, "2026-09-13T10:05:00.000000000"),
        ("2026-09-13T10:05:00.000000000", _MINUTE_NS, True, "2026-09-13T10:05:00.000000000"),
        ("2026-09-13T23:59:59.500000000", 10**9, True, "2026-09-14T00:00:00.000000000"),
        ("2026-09-13T10:00:01.123456789", None, False, "2026-09-13T10:00:01.123456789"),
    ],
)
def test_a_bound_is_moved_outwards_onto_the_grid(value, align, upper, expected):
    assert gaps_cli.widen_to_grain(value, align, upper=upper) == expected


def _q_nanos(text: str) -> int:
    """`"j"$` of a widened bound: nanoseconds since q's 2000.01.01."""
    stamp, _, fraction = text.partition(".")
    whole = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
    return int(whole.timestamp()) * 10**9 + int(fraction) - gaps_cli.Q_EPOCH_NS


@pytest.mark.parametrize("upper", [False, True])
def test_a_grain_that_does_not_divide_the_epoch_gap_lands_on_qs_grid(upper):
    """#1345: 7 minutes does not divide 1970->2000, so the Unix grid's
    09:57:00 is 240 s off q's - and require_aligned refused the very command
    #1323 prints to refill the gap."""
    align = 7 * _MINUTE_NS
    got = gaps_cli.widen_to_grain("2026-10-09T10:03:00.000000000", align, upper=upper)
    assert _q_nanos(got) % align == 0, got
    assert got == ("2026-10-09T10:07:00.000000000" if upper else "2026-10-09T10:00:00.000000000")


def test_the_advised_exec_bars_refill_is_minute_aligned_and_widened(monkeypatch):
    result = _gaps_cli(monkeypatch, [_NS_HOLE], {"hdb_exec_bars_backfill": _MINUTE_NS})
    command = _printed_command(result.output)
    assert "--from 2026-09-13T10:00:00 --to 2026-09-13T10:05:00 --version" in command, command


def test_the_advised_exec_bars_refill_is_accepted_by_init(monkeypatch, status):
    """The printed bounds, handed to the real `init`, are not refused."""
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    command = _printed_command(
        _gaps_cli(monkeypatch, [_NS_HOLE], {"hdb_exec_bars_backfill": _MINUTE_NS}).output
    )
    lo = command.split("--from ")[1].split()[0].replace("T", "D")
    hi = command.split("--to ")[1].split()[0].replace("T", "D")
    lo, hi = (v.replace("-", ".") if "D" in v else v for v in (lo, hi))
    script = status / "init.q"
    script.write_text(
        "\\l src/init.q\n\\l src/etl/init.q\n"
        ".qpipe.job.hdb_exec_bars_backfill.init "
        f"`source_version`range_from`range_to!(`v1;{lo};{hi});\n"
        '-1 "INIT_OK"; exit 0\n'
    )
    result = subprocess.run(
        [str(q), str(script), "-q"],
        cwd=UQF_ROOT,
        env={**os.environ, "UQF_STATUS_DIR": str(status), "UQF_FIXTURE_WRITES": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "must be multiples of it" not in result.stdout + result.stderr, result.stdout
    assert "INIT_OK" in result.stdout, result.stdout + result.stderr
