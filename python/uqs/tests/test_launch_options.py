"""One launch-option set on every command that launches a job (#2687).

`uqs start`/`restart` (a normal job), `uqs job backfill` (a bounded run) and
`uqs job attachment add` (which starts the attached publisher once verified)
answer the same questions - stream it, filter the stream, stop it on Ctrl-C,
trace its source queries, report as JSON, show it without doing it - with the
same options, each declared once (cli/launch_options.py). This holds the set on
every one of them, with the same help wherever the meaning is the same; then
what each option newly added to a command does there.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import typer
from test_attach_cli import REAL_START, new, run, world  # noqa: F401 - the attach world
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import publisher
from uqs.cli import backfill_attached, shared
from uqs.cli.shared import EXIT_USAGE
from uqs.stack import attached_logs, log_follow, runtime

#: The commands that launch something, as the words that reach them.
LAUNCHERS = (("start",), ("restart",), ("job", "backfill"), ("job", "attachment", "add"))

#: What every launch command takes (#2687).
SHARED = {
    "--base-port", "--dry-run", "--override", "--reason",
    "--follow", "--level", "--stop-on-exit", "--trace", "--json",
}  # fmt: skip

#: (command, option) -> why that command does without it. Empty: keep it so.
EXCEPTIONS: dict[tuple[str, str], str] = {}

#: Options declared once in cli/launch_options.py: the same help on every command.
#: --json names each command's own document, --dry-run what each one shows, and
#: --base-port/--override/--reason were each command's before #2687 (an
#: attachment's --override names a finding on a COMPONENT): their one meaning is
#: held by test_cli_conventions' OPTIONS.
DECLARED_ONCE = ("--follow", "--level", "--stop-on-exit", "--trace")


def _command(*path: str) -> Any:
    command: Any = typer.main.get_command(cli.app)
    for word in path:
        command = command.commands[word]
    return command


def _options(*path: str) -> dict[str, Any]:
    return {o: p for p in _command(*path).params for o in getattr(p, "opts", [])}


@pytest.mark.parametrize("path", LAUNCHERS, ids=" ".join)
def test_every_launch_command_carries_the_shared_set(path):
    name = " ".join(path)
    missing = {o for o in SHARED - set(_options(*path)) if (name, o) not in EXCEPTIONS}
    assert not missing, f"`uqs {name}` lacks {sorted(missing)}: take them from launch_options"


def test_every_exception_is_still_needed():
    stale = [k for k in EXCEPTIONS if k[1] in _options(*k[0].split())]
    assert not stale, f"these commands now take the option: drop the exception {stale}"


@pytest.mark.parametrize("option", DECLARED_ONCE)
def test_each_shared_option_means_the_same_on_every_command(option):
    helps = {" ".join(p): _options(*p)[option].help for p in LAUNCHERS}
    assert len(set(helps.values())) == 1, f"{option} drifted: {helps}"
    shorts = {" ".join(p): tuple(_options(*p)[option].opts) for p in LAUNCHERS}
    assert len(set(shorts.values())) == 1, f"{option} is spelled differently: {shorts}"


# ----------------------------------------------------------------- job backfill


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


def _backfill(*argv: str):
    return CliRunner().invoke(
        cli.app,
        ["job", "backfill", "upstream_trades_backfill", "--source-version", "v1",
         "--date", "2015-01-07", *argv],
        catch_exceptions=False,
    )  # fmt: skip


def test_backfill_level_needs_follow(monkeypatch):
    errors = _errors(monkeypatch)
    called: list[Any] = []
    monkeypatch.setattr(backfill_attached, "run", lambda *a, **kw: called.append(kw))
    result = _backfill("--attachment", "pb-uat", "--level", "WARNING")
    assert result.exit_code == EXIT_USAGE, result.output
    assert errors == ["--level needs --follow"]
    assert called == [], "nothing was started"


def test_backfill_level_reaches_the_attached_run(monkeypatch):
    called: list[dict[str, Any]] = []
    monkeypatch.setattr(backfill_attached, "run", lambda *a, **kw: called.append(kw))
    result = _backfill("--attachment", "pb-uat", "--follow", "--level", "warning")
    assert result.exit_code == 0, result.output
    (kw,) = called
    assert kw["follow"] is True and kw["level"] == "WARNING"


def test_backfill_level_filters_the_stream_as_start_level_does(monkeypatch, tmp_path):
    """The stream is attached_logs.stream, handed the level as `uqs logs --level` is:
    stack/logs.emit drops what is below it and never a line with no level."""
    seen: dict[str, Any] = {}

    def stream(path, worker, **kw):
        seen.update(kw)

    monkeypatch.setattr(attached_logs, "stream", stream)

    class _Run:
        pid, log, stop_file = 1, tmp_path / "w-20260101T000000000000.log", tmp_path / "stop"
        process = None

    backfill_attached._stream("pb-uat", "w", cast(Any, _Run()), level="ERROR")
    assert seen["min_level"] == "ERROR"


# ------------------------------------------------------------- job attachment add


@pytest.mark.parametrize("flag", [["--follow"], ["--level", "ERROR"], ["--stop-on-exit"],
                                  ["--trace"]])  # fmt: skip
def test_attach_launch_options_are_refused_with_no_start(world, flag):  # noqa: F811
    result = run("add", *new(world, "--yes", "--no-start", *flag))
    assert result.exit_code == EXIT_USAGE, result.output
    assert world.errors == [
        f"{flag[0]} does not apply with --no-start: it acts on the publisher this starts, "
        "and --no-start leaves it stopped"
    ]
    assert world.started == [] and not world.state_dir.exists(), "nothing was changed"


@pytest.mark.parametrize(
    ("flags", "said"),
    [
        (["--level", "ERROR"], "--level needs --follow"),
        (["--stop-on-exit"], "--stop-on-exit needs --follow: it stops what the stream started"),
        (["--follow", "--json"], "--follow does not apply with --json: use --json alone"),
        (["--follow", "--dry-run"], "--dry-run does not apply with --follow"),
    ],
)
def test_attach_launch_options_are_refused_where_they_cannot_apply(world, flags, said):  # noqa: F811
    result = run("add", *new(world, "--yes", *flags))
    assert result.exit_code == EXIT_USAGE, result.output
    assert world.errors == [said]
    assert world.started == [] and not world.state_dir.exists()


def _start_calls(world, monkeypatch) -> list[dict[str, Any]]:  # noqa: F811
    """runtime.start as the real publisher calls it: each call's keywords."""
    seen: list[dict[str, Any]] = []

    def fake_start(paths, names, base_port=None, **kw):
        from types import SimpleNamespace

        seen.append({"names": names, "base_port": base_port, **kw})
        world.started.append(names)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runtime, "start", fake_start)
    monkeypatch.setattr(publisher.UqsPublisher, "start", REAL_START)
    return seen


def test_attach_trace_starts_the_publisher_traced(world, monkeypatch):  # noqa: F811
    seen = _start_calls(world, monkeypatch)
    result = run("add", *new(world, "--yes", "--trace"))
    assert result.exit_code == 0, result.output
    assert [(c["names"], c["base_port"], c["trace"]) for c in seen] == [("pb_ledger1", 6000, True)]


def test_attach_trace_names_a_publisher_already_running(world, monkeypatch):  # noqa: F811
    assert run("add", *new(world, "--yes")).exit_code == 0
    result = run("add", "pb-uat", "--yes", "--trace")
    assert result.exit_code == 0, result.output
    assert "already running, so --trace did not reach: pb_ledger1" in " ".join(
        result.output.split()
    )


def _follow(monkeypatch) -> dict[str, Any]:
    """log_follow.follow_during, which streams until Ctrl-C: runs the launch, then
    returns as a Ctrl-C would."""
    seen: dict[str, Any] = {}

    def follow_during(paths, procnames, start_fn, min_level=None):
        seen.update(procnames=procnames, min_level=min_level)
        start_fn()

    monkeypatch.setattr(log_follow, "follow_during", follow_during)
    return seen


def _stops(monkeypatch) -> list[tuple[str, Any]]:
    stops: list[tuple[str, Any]] = []

    def stop(paths, procs, base_port=None, **kw):
        stops.append((procs, base_port))
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime, "stop", stop)
    return stops


def test_attach_follow_streams_the_publisher_it_starts_and_leaves_it_running(world, monkeypatch):  # noqa: F811
    followed, stops = _follow(monkeypatch), _stops(monkeypatch)
    result = run("add", *new(world, "--yes", "-f", "--level", "warning"))
    assert result.exit_code == 0, result.output
    assert followed == {"procnames": ["pb_ledger1"], "min_level": "WARNING"}
    assert world.started == ["pb_ledger1"], "the stream began, then the publisher started"
    assert "attached" in result.output and "left running: pb_ledger1" in result.output
    assert stops == []


def test_attach_stop_on_exit_stops_the_publisher_it_started(world, monkeypatch):  # noqa: F811
    _follow(monkeypatch)
    stops = _stops(monkeypatch)
    result = run("add", *new(world, "--yes", "--follow", "--stop-on-exit"))
    assert result.exit_code == 0, result.output
    assert stops == [("pb_ledger1", 6000)], "through uqs stop's path, on the stack's base port"


def test_attach_stop_on_exit_leaves_a_publisher_that_was_already_running(world, monkeypatch):  # noqa: F811
    assert run("add", *new(world, "--yes")).exit_code == 0
    _follow(monkeypatch)
    stops = _stops(monkeypatch)
    result = run("add", "pb-uat", "--yes", "--follow", "--stop-on-exit")
    assert result.exit_code == 0, result.output
    assert stops == [] and "every process was already running" in result.output


def test_attach_follow_ends_with_a_failed_stage(world, monkeypatch):  # noqa: F811
    _follow(monkeypatch)

    def refused(self, att):
        raise publisher.AttachmentError("no licence")

    monkeypatch.setattr(publisher.UqsPublisher, "start", refused)
    result = run("add", *new(world, "--yes", "--follow"))
    assert result.exit_code == 1, result.output
    assert any("start failed - no licence" in m for m in world.errors), world.errors
