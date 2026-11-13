"""`uqs job backfill --attachment NAME --follow` (#2677), on real q processes.

The world is test_attach_backfill_run's: an upstream q process the worker reads, the runner
(scripts/jobs/attached_backfill.q) as a child, and a native-shaped HDB it writes. What is under
test is that --follow streams THIS run's log - the file the launch reports - and ends with the
run's outcome and exit code, exactly as --wait does, and that Ctrl-C ends the stream only.
"""

from __future__ import annotations

import pytest
from test_attach_backfill_run import (  # noqa: F401 - the shared world, its fixtures and its PeachQ skip
    WORKER,
    _cli,
    pytestmark,
    upstream,
    world,
)
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_owner, backfill_run
from uqs.cli import backfill_attached, shared
from uqs.cli.shared import EXIT_PARTIAL, EXIT_REFUSED, EXIT_USAGE
from uqs.stack import attached_logs


def _flat(text: str) -> str:
    return " ".join(text.split())


def _launched(monkeypatch) -> list[backfill_run.Launched]:
    """Every run the command starts, as the launch returned it."""
    started: list[backfill_run.Launched] = []
    real = backfill_run.launch

    def launch(*a, **kw):
        run_, plan = real(*a, **kw)
        started.append(run_)
        return run_, plan

    monkeypatch.setattr(backfill_run, "launch", launch)
    return started


# ------------------------------------------------------------------------- following


def test_follow_streams_the_runs_own_log_and_exits_with_its_outcome(world, monkeypatch):  # noqa: F811
    started = _launched(monkeypatch)
    result = _cli("--mode", "run", "--follow")
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    (run_,) = started
    assert f"following {WORKER} (pid {run_.pid})" in out
    # the run's own lines, streamed as `uqs logs --attachment -f` shows them, then its RESULT
    assert "writing into an attached stack's HDB" in out
    assert "attached backfill finished" in out and 'RESULT {"worker"' in out
    # then the outcome, as --wait reports it - after every line of the log
    assert out.index(f"{WORKER}: queryable (exit 0)") > out.index('RESULT {"worker"')
    assert str(run_.log) in out


def test_follow_exits_with_the_code_wait_uses_for_a_run_that_is_not_queryable(world, monkeypatch):  # noqa: F811
    world.reader.refuse()
    result = _cli("--mode", "run", "--follow")
    assert result.exit_code == EXIT_PARTIAL, result.output
    assert "written_not_queryable (exit 3)" in _flat(result.output)
    owner = backfill_owner.active("pb-uat")
    assert owner == [], "the run is over, and owns nothing"


def test_a_run_that_fails_at_once_still_shows_its_log_and_result(world, monkeypatch):  # noqa: F811
    # a flag the runner refuses (its exit 2): it is gone before the stream is opened
    flags = backfill_run.runner_flags
    monkeypatch.setattr(
        backfill_run, "runner_flags", lambda *a, **kw: [*flags(*a, **kw), "-fixture"]
    )
    started = _launched(monkeypatch)
    real_stream = attached_logs.stream

    def late(path, worker, **kw):
        # follow only once the run has ended: what an already-finished run looks like
        started[0].process.wait(timeout=60)
        return real_stream(path, worker, **kw)

    monkeypatch.setattr(attached_logs, "stream", late)
    result = _cli("--mode", "run", "--follow")
    out = _flat(result.output)
    assert result.exit_code == EXIT_REFUSED, result.output
    assert "-fixture is refused" in out, "the runner's own refusal is shown"
    assert f"{WORKER}: unknown (exit {EXIT_REFUSED})" in out


def test_ctrl_c_ends_the_stream_and_leaves_the_run_running(world, monkeypatch):  # noqa: F811
    started = _launched(monkeypatch)
    real_stream = attached_logs.stream

    def interrupted(path, worker, **kw):
        # Ctrl-C once the run is writing: the stream ends where the operator stopped it
        def until() -> bool:
            if "initialised" in path.read_text():
                raise KeyboardInterrupt
            return False

        return real_stream(path, worker, until=until)

    monkeypatch.setattr(attached_logs, "stream", interrupted)
    result = CliRunner().invoke(
        cli.app,
        [
            "job",
            "backfill",
            WORKER,
            "--attachment",
            "pb-uat",
            "--source-version",
            "v1",
            "--on-conflict",
            "replace",
            "--from",
            "2015-01-05",
            "--to",
            "2015-01-10",
            "--mode",
            "run",
            "--follow",
        ],  # fmt: skip
        catch_exceptions=False,
    )
    (run_,) = started
    try:
        out = _flat(result.output)
        assert result.exit_code == backfill_attached.EXIT_INTERRUPTED, result.output
        assert run_.process.poll() is None, "Ctrl-C stopped the stream, not the run"
        assert f"{WORKER} (pid {run_.pid}) is still running" in out
        assert f"uqs logs --attachment pb-uat --worker {WORKER} -f" in out
        assert [o["pid"] for o in backfill_owner.active("pb-uat")] == [run_.pid]
    finally:
        code = run_.process.wait(timeout=300)
    assert code == 0, "left alone, the run finished on its own"


# --------------------------------------------------------------------------- refused


class _ErrorLog:
    """What `_die` hands the log: loguru does not write into CliRunner's output."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


def _errors(monkeypatch) -> list[str]:
    log = _ErrorLog()
    monkeypatch.setattr(shared, "log", log)
    return log.messages


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["--follow", "--json"], "--follow does not apply with --json"),
        (["--follow", "--mode", "plan"], "--follow has nothing to follow with --mode plan"),
        (["--follow", "--writer", "localhost:1"], "--follow is not supported with --writer"),
    ],
)
def test_follow_is_refused_where_it_cannot_apply(world, monkeypatch, argv, said):  # noqa: F811
    errors = _errors(monkeypatch)
    result = _cli(*argv)
    assert result.exit_code in (EXIT_REFUSED, EXIT_USAGE), result.output
    assert any(said in m for m in errors), errors
    assert backfill_owner.active("pb-uat") == [], "nothing was started"


def test_follow_without_an_attachment_is_refused(monkeypatch):
    errors = _errors(monkeypatch)
    result = CliRunner().invoke(
        cli.app,
        ["job", "backfill", WORKER, "--source-version", "v1", "--date", "2015-01-07", "--follow"],
        catch_exceptions=False,
    )
    assert result.exit_code == EXIT_USAGE, result.output
    assert any("--follow is for --attachment" in m for m in errors), errors


# ------------------------------------------------------------------------------ wait


def test_wait_says_it_is_waiting_not_following(world):  # noqa: F811
    result = _cli("--dry-run", "--wait")
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert f"waiting for {WORKER} (pid" in out and "Ctrl-C stops waiting, not the run" in out
    assert "following" not in out
