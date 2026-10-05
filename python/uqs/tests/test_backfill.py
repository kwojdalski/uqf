"""Tests for `uqs backfill` and the flags it hands torq_backfill.q.

The flags reach q on a start line torq.sh builds as a string and `eval`s, so
what is refused here is as much the point as what is built.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
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

    def fake(
        paths, worker, version, range_from, range_to, base_port, verbose, trace, on_conflict, mode
    ):
        seen.update(worker=worker, version=version, range=(range_from, range_to), port=base_port)
        seen["verbose"] = verbose
        seen["trace"] = trace
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
        "trace": False,
    }


@pytest.mark.parametrize("argv_debug", [["--debug"], []])
def test_debug_starts_the_process_verbose(monkeypatch, argv_debug):
    """Both spellings: the command's own --debug, and the global one."""
    seen = {}

    def fake(
        paths, worker, version, range_from, range_to, base_port, verbose, trace, on_conflict, mode
    ):
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


def test_on_conflict_reaches_the_process_as_a_flag():
    flags = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, on_conflict="replace")
    assert flags[-2:] == ["-on_conflict", "replace"]


def test_no_on_conflict_leaves_the_worker_its_own():
    assert "-on_conflict" not in backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO)


def test_an_unknown_on_conflict_is_refused_naming_the_strategies():
    with pytest.raises(UqsError, match="append, fail, ignore, replace, upsert"):
        backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, on_conflict="merge")


def test_the_cli_passes_on_conflict_through(monkeypatch):
    seen = {}

    def fake(
        paths, worker, version, range_from, range_to, base_port, verbose, trace, on_conflict, mode
    ):
        seen["on_conflict"] = on_conflict
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(backfill, "start", fake)
    argv = ["backfill", "demo_deals_backfill", "--version", "v2", "--from", "2026-09-13"]
    argv += ["--to", "2026-09-15", "--on-conflict", "replace"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert seen == {"on_conflict": "replace"}


def test_verbose_adds_the_flag_torq_backfill_reads():
    plain = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO)
    loud = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, verbose=True)
    assert loud == [*plain, "-verbose"]


def test_trace_adds_its_own_flag_independent_of_verbose():
    plain = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO)
    traced = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, trace=True)
    both = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, verbose=True, trace=True)
    assert traced == [*plain, "-trace"]
    assert both == [*plain, "-verbose", "-trace"]


def test_trace_on_the_command_line_reaches_the_process(monkeypatch):
    seen = {}

    def fake(
        paths, worker, version, range_from, range_to, base_port, verbose, trace, on_conflict, mode
    ):
        seen.update(verbose=verbose, trace=trace)
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(backfill, "start", fake)
    argv = ["backfill", "demo_deals_backfill", "--version", "v1"]
    argv += ["--from", "2026-09-13", "--to", "2026-09-15", "--trace"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert seen == {"verbose": False, "trace": True}


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


# --- remove checkpoint ------------------------------------------------------

WORKER = "demo_deals_backfill"


@pytest.fixture
def status(tmp_path, monkeypatch):
    """A status directory of this test's own, through the variable q reads."""
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    return tmp_path


def _lock(status, owner: dict | None):
    lock = status / f"{WORKER}.lock"
    lock.mkdir()
    if owner is not None:
        (lock / "owner").write_text(json.dumps(owner))


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_clearing_deletes_the_checkpoint_and_names_it(status):
    (status / f"{WORKER}.checkpoint").write_text("{}")
    path = backfill.clear_checkpoint(stack_paths.default_paths(), WORKER)
    assert path is not None
    assert path == status / f"{WORKER}.checkpoint"
    assert not path.exists()


def test_clearing_also_deletes_the_previous_generation(status):
    """q falls back to `.checkpoint.bak` when the checkpoint will not parse, so
    a clear that left it would let the next run resume from before the clear."""
    for suffix in ("", ".bak", ".tmp"):
        (status / f"{WORKER}.checkpoint{suffix}").write_text("{}")
    backfill.clear_checkpoint(stack_paths.default_paths(), WORKER)
    assert not list(status.glob(f"{WORKER}.checkpoint*"))


def test_clearing_a_worker_with_no_checkpoint_is_not_an_error(status):
    assert backfill.clear_checkpoint(stack_paths.default_paths(), WORKER) is None


def test_clearing_an_unknown_worker_is_refused(status):
    with pytest.raises(UqsError, match="known workers"):
        backfill.clear_checkpoint(stack_paths.default_paths(), "nope")


@pytest.mark.parametrize(
    "owner",
    [
        pytest.param({"pid": os.getpid(), "host": socket.gethostname()}, id="running-here"),
        pytest.param({"pid": 1, "host": "some-other-box"}, id="another-host"),
        pytest.param({"started": "2026.10.01D00:00"}, id="no-pid-or-host"),
        pytest.param(None, id="mid-acquire-no-owner-file"),
    ],
)
def test_a_lock_that_may_be_live_keeps_the_checkpoint(status, owner):
    """The same rule as lock_is_stale: whatever cannot be proved dead is live."""
    (status / f"{WORKER}.checkpoint").write_text("{}")
    _lock(status, owner)
    with pytest.raises(UqsError, match="may still be running"):
        backfill.clear_checkpoint(stack_paths.default_paths(), WORKER)
    assert (status / f"{WORKER}.checkpoint").exists()


def test_a_lock_left_by_an_exited_run_does_not_block_clearing(status):
    (status / f"{WORKER}.checkpoint").write_text("{}")
    _lock(status, {"pid": _dead_pid(), "host": socket.gethostname()})
    assert backfill.clear_checkpoint(stack_paths.default_paths(), WORKER) is not None


def test_remove_checkpoint_reports_what_it_did(status):
    (status / f"{WORKER}.checkpoint").write_text("{}")
    result = runner.invoke(cli.app, ["remove", "checkpoint", WORKER])
    assert result.exit_code == 0, result.output
    assert "cleared demo_deals_backfill's checkpoint" in result.output
    again = runner.invoke(cli.app, ["remove", "checkpoint", WORKER])
    assert "has no checkpoint" in again.output


def test_remove_checkpoint_refusal_exits_one(status):
    _lock(status, {"pid": os.getpid(), "host": socket.gethostname()})
    result = runner.invoke(cli.app, ["remove", "checkpoint", WORKER])
    assert result.exit_code == 1
    assert not isinstance(result.exception, UqsError)


@pytest.mark.parametrize(
    ("mode", "sent"),
    [("validate", "validate"), ("plan", "plan"), ("dry-run", "dry_run"), ("run", "run")],
)
def test_a_mode_reaches_the_process_spelled_as_q_spells_it(mode, sent):
    flags = backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, mode=mode)
    assert flags[-2:] == ["-mode", sent]


def test_no_mode_runs_for_real():
    """Left out, the process decides - which is `run` unless UQF_DRY_RUN says otherwise."""
    assert "-mode" not in backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO)


def test_an_unknown_mode_is_refused_naming_the_four():
    with pytest.raises(UqsError, match="validate, plan, dry-run, run"):
        backfill.backfill_flags("demo_deals_backfill", "v1", FROM, TO, mode="rehearse")


def test_the_cli_passes_the_mode_through(monkeypatch):
    seen = {}

    def fake(
        paths, worker, version, range_from, range_to, base_port, verbose, trace, on_conflict, mode
    ):
        seen["mode"] = mode
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(backfill, "start", fake)
    argv = ["backfill", "demo_deals_backfill", "--version", "v2", "--from", "2026-09-13"]
    argv += ["--to", "2026-09-15", "--mode", "plan"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert seen == {"mode": "plan"}


def test_a_given_version_is_used_as_is():
    assert backfill.resolve_version("demo_deals_backfill", "v9") == "v9"


def test_a_worker_with_a_declared_default_needs_no_version():
    """The event tape is append-only, so its worker declares v1."""
    assert backfill.resolve_version("demo_events_backfill", None) == "v1"


def test_a_worker_with_no_default_refuses_a_missing_version():
    """Deals can be restated upstream, so the release must be named."""
    with pytest.raises(UqsError, match="declares no default source_version - pass --version"):
        backfill.resolve_version("demo_deals_backfill", None)


def test_the_cli_runs_without_version_for_a_worker_with_a_default(monkeypatch):
    seen = {}

    def fake(
        paths, worker, version, range_from, range_to, base_port, verbose, trace, on_conflict, mode
    ):
        seen["version"] = version
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(backfill, "start", fake)
    argv = ["backfill", "demo_events_backfill", "--from", "2026-09-13", "--to", "2026-09-15"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert seen == {"version": "v1"}


# --- --wait: exit with the run's outcome, not torq.sh's (#637) -------------

_LAUNCH = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _status(tmp_path, **over) -> None:
    """A status file as .qetl.status.write_status writes it, for this run."""
    body = {
        "worker": "demo_deals_backfill",
        "instance_id": "deals_backfill1",
        "state": "completed",
        "source_version": "v1",
        "range_from": "2026-09-13T00:00:00.000000000",
        "range_to": "2026-09-15T00:00:00.000000000",
        "updated_at": "2026-10-04T12:00:05.000000000",
        "pid": os.getpid(),
        "host": socket.gethostname().lower(),
        "error": "",
    }
    body.update(over)
    (tmp_path / "airflow_status_deals_backfill1.txt").write_text(json.dumps(body))


def _wait(tmp_path, monkeypatch, sleeps=None):
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    return backfill.wait_for_outcome(
        stack_paths.default_paths(),
        "deals_backfill1",
        "v1",
        FROM,
        TO,
        _LAUNCH,
        sleep=sleeps if sleeps is not None else (lambda s: None),
    )


@pytest.mark.parametrize(("state", "code"), [("completed", 0), ("idle", 0), ("failed", 1)])
def test_wait_returns_the_runs_own_outcome(tmp_path, monkeypatch, state, code):
    _status(tmp_path, state=state)
    assert _wait(tmp_path, monkeypatch) == (state, code)


def test_wait_ignores_the_previous_runs_file_until_this_one_writes(tmp_path, monkeypatch):
    """The file is rewritten by every run: one written before this launch,
    or for another version, is not this run's outcome."""
    _status(tmp_path, state="failed", updated_at="2026-10-04T11:00:00.000000000")
    calls = []

    def sleep(_):
        calls.append(1)
        if len(calls) == 1:
            _status(tmp_path, state="completed", source_version="v0")
        elif len(calls) == 2:
            _status(tmp_path, state="completed")

    assert _wait(tmp_path, monkeypatch, sleep) == ("completed", 0)
    assert len(calls) == 2


def test_wait_reports_a_run_whose_process_died_as_abandoned(tmp_path, monkeypatch):
    proc = subprocess.Popen(["true"])  # noqa: S603, S607
    proc.wait()
    _status(tmp_path, state="running", pid=proc.pid)
    assert _wait(tmp_path, monkeypatch) == ("abandoned", 1)


def test_the_command_exits_with_the_runs_outcome_under_wait(monkeypatch):
    monkeypatch.setattr(
        backfill, "start", lambda *a, **k: type("Completed", (), {"returncode": 0})()
    )
    monkeypatch.setattr(backfill, "wait_for_outcome", lambda *a, **k: ("failed", 1))
    argv = ["backfill", "demo_deals_backfill", "--version", "v1"]
    argv += ["--from", "2026-09-13", "--to", "2026-09-15", "--wait"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 1, result.output
    assert "failed" in result.output


def test_wait_with_a_mode_that_writes_no_status_is_refused(monkeypatch):
    import typer

    from uqs.cli import backfill as cli_backfill

    started, refused = [], []

    def die(exc):
        refused.append(str(exc))
        raise typer.Exit(code=1)

    monkeypatch.setattr(backfill, "start", lambda *a, **k: started.append(a))
    monkeypatch.setattr(cli_backfill, "_die", die)
    argv = ["backfill", "demo_deals_backfill", "--version", "v1"]
    argv += ["--from", "2026-09-13", "--to", "2026-09-15", "--wait", "--mode", "plan"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 1
    assert refused and "nothing to wait for" in refused[0]
    assert started == []
