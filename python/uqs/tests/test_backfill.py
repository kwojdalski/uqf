"""Tests for `uqs backfill` and the flags it hands torq_backfill.q.

The flags reach q on a start line torq.sh builds as a string and `eval`s, so
what is refused here is as much the point as what is built.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs import paths as stack_paths
from uqs.cli import shared
from uqs.paths import UqsError
from uqs.stack import backfill, runtime

runner = CliRunner()

FROM = datetime(2026, 9, 13, tzinfo=UTC)
TO = datetime(2026, 9, 15, tzinfo=UTC)


def test_every_declared_worker_resolves_to_the_process_that_runs_it():
    workers = backfill.backfill_workers()
    assert workers["demo_deals_backfill"] == "deals_backfill1"
    assert len(set(workers.values())) == len(workers), "one process per worker"


def test_an_unknown_worker_is_refused_naming_the_known_ones():
    with pytest.raises(UqsError, match="known workers: .*demo_deals_backfill"):
        backfill.procname_for("nope")


def test_a_bound_without_an_offset_is_utc_and_one_with_is_converted():
    assert backfill.parse_bound("--from", "2026-09-13") == FROM
    assert backfill.parse_bound("--from", "2026-09-13T02:00+02:00") == FROM


@pytest.mark.parametrize(
    "text",
    [
        "2026-09-13T00:00",
        "2026-09-13T00:00:00Z",
        "2026.09.13",
        "2026.09.13D00:00",
        "2026.09.13D00:00:00.000000000",
    ],
)
def test_iso_with_a_t_and_q_literals_are_both_accepted(text):
    """The `T` form is one shell word, so it needs no quoting; the q form is
    what someone working in q types."""
    assert backfill.parse_bound("--from", text) == FROM


def test_a_q_literal_finer_than_a_microsecond_is_refused_not_truncated():
    """datetime stops at the microsecond. A bound silently moved is a
    different range."""
    with pytest.raises(UqsError, match="finer than a microsecond"):
        backfill.parse_bound("--from", "2026.09.13D00:00:00.000000001")


def test_a_q_literal_that_is_not_a_real_date_is_refused():
    with pytest.raises(UqsError, match="not a real date"):
        backfill.parse_bound("--from", "2026.02.30")


def test_a_bound_that_is_not_iso_is_refused_by_name():
    with pytest.raises(UqsError, match="--to must be an ISO-8601"):
        backfill.parse_bound("--to", "13/09/2026")


def test_the_flags_carry_q_timestamps():
    """torq_backfill.q parses the bounds with "P"$."""
    flags = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO)
    assert flags == [
        "-worker",
        "demo_deals_backfill",
        "-version",
        "v1",
        "-from",
        "2026.09.13D00:00:00.000000000",
        "-to",
        "2026.09.15D00:00:00.000000000",
    ]


@pytest.mark.parametrize("version", ["v1;rm -rf x", "v 1", "$(id)", "v1`x"])
def test_a_value_a_shell_would_interpret_is_refused(version):
    """torq.sh `eval`s the start line."""
    with pytest.raises(UqsError, match="only letters, digits"):
        backfill.backfill_flags("demo_deals_backfill", version, FROM, TO)


@pytest.mark.parametrize("version", ["v1-csv", "extras2"])
def test_a_value_torq_sh_would_read_as_its_own_flag_is_refused(version):
    """torq.sh greps every argument for `csv` and `extras`."""
    with pytest.raises(UqsError, match="'csv' or 'extras'"):
        backfill.backfill_flags("demo_deals_backfill", version, FROM, TO)


def test_an_empty_range_is_refused():
    with pytest.raises(UqsError, match="the range is empty"):
        backfill.backfill_flags("demo_deals_backfill", "v1", TO, FROM)


def test_start_passes_the_flags_through_torq_sh_extras(monkeypatch):
    """`-extras` is torq.sh's own way to add to one process's start line, so
    the process still starts the way every other one does."""
    seen = []
    monkeypatch.setattr(runtime, "run_torq_sh", lambda paths, args, **kw: seen.append(args))
    backfill.start(stack_paths.default_paths(), "demo_deals_backfill", "v1", FROM, TO)
    assert seen[0][:3] == ["start", "deals_backfill1", "-extras"]
    assert seen[0][3:] == backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO)


def test_the_command_reaches_start_with_parsed_bounds(monkeypatch):
    seen = {}

    def fake(paths, worker, version, range_from, range_to, base_port, verbose):
        seen.update(worker=worker, version=version, range=(range_from, range_to), port=base_port)
        seen["verbose"] = verbose
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(backfill, "start", fake)
    argv = ["backfill", "demo_deals_backfill", "--version", "v1"]
    argv += ["--from", "2026-09-13", "--to", "2026-09-15", "--port", "7000"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert seen == {
        "worker": "demo_deals_backfill",
        "version": "v1",
        "range": (FROM, TO),
        "port": 7000,
        "verbose": False,
    }


@pytest.mark.parametrize("argv_debug", [["--debug"], []])
def test_debug_starts_the_process_verbose(monkeypatch, argv_debug):
    """Both spellings: the command's own --debug, and the global one."""
    seen = {}

    def fake(paths, worker, version, range_from, range_to, base_port, verbose):
        seen["verbose"] = verbose
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(backfill, "start", fake)
    # The global flag reconfigures logging for the whole process, which would
    # leak DEBUG output into every later test's captured stdout.
    monkeypatch.setattr(shared, "configure_logging", lambda **kw: None)
    argv = ["backfill", "demo_deals_backfill", "--version", "v1"]
    argv += ["--from", "2026-09-13", "--to", "2026-09-15", *argv_debug]
    prefix = [] if argv_debug else ["--debug"]
    result = runner.invoke(cli.app, [*prefix, *argv])
    assert result.exit_code == 0, result.output
    assert seen["verbose"] is True


def test_verbose_adds_the_flag_torq_backfill_reads():
    plain = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO)
    loud = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, verbose=True)
    assert loud == [*plain, "-verbose"]


@pytest.mark.parametrize("missing", ["--version", "--from", "--to"])
def test_every_part_of_the_range_is_required(monkeypatch, missing):
    """No default range, anywhere: a guessed one would be recorded as covered."""
    monkeypatch.setattr(backfill, "start", lambda *a, **k: pytest.fail("must not start"))
    args = {"--version": "v1", "--from": "2026-09-13", "--to": "2026-09-15"}
    del args[missing]
    argv = ["backfill", "demo_deals_backfill", *[x for kv in args.items() for x in kv]]
    assert runner.invoke(cli.app, argv).exit_code != 0


def test_a_refusal_exits_one_rather_than_raising():
    result = runner.invoke(
        cli.app,
        ["backfill", "nope", "--version", "v1", "--from", "2026-09-13", "--to", "2026-09-15"],
    )
    assert result.exit_code == 1
    assert not isinstance(result.exception, UqsError)
