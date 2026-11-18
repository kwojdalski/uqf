"""`--once`: one unit of a job, then an outcome - held to every kind and every launcher (#2798).

The q half is tests/q/test_once.q (the runners, with real jobs on a fake transport) and
tests/q/test_torq_backfill.q (a real process); this is the launcher's half, and the table that
says which kinds and which launch interfaces take part. A kind or interface that does not yet is
named here, with the reason, as a strict xfail: it fails now, and passes - which a strict xfail
reports as a failure - the day it is built, so the mark has to be removed with the support.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import backfill as backfill_cli
from uqs.cli import once_launch, shared
from uqs.cli.shared import EXIT_REFUSED, EXIT_USAGE
from uqs.generated import q_facts
from uqs.model.kinds import (
    DECLARATION_VERBS,
    SCAFFOLD_KINDS,
    SCAFFOLD_VERB,
    VERBS,
)
from uqs.paths import UqsError
from uqs.stack import alive, once, runtime, start_lines
from uqs.stack import backfill as stack_backfill

ROOT = Path(__file__).resolve().parents[3]

#: Stands in for the stack's paths where the status directory comes from the environment.
PATHS: Any = object()

#: The job kinds the contract defines a unit for, and the unit (src/etl/core/once.q).
KINDS = {"poll", "timer", "subscriber", "bounded", "reaction"}


def _command(*path: str) -> Any:
    command: Any = typer.main.get_command(cli.app)
    for word in path:
        command = command.commands[word]
    return command


def _options(*path: str) -> set[str]:
    return {o for p in _command(*path).params for o in getattr(p, "opts", [])}


# ------------------------------------------------------------------- the vocabulary


def test_the_codes_are_q_codes_and_distinct():
    """The CLI exits with what q's process exits with: one number per outcome."""
    assert once.CODES == {"done": 0, "failed": 1, "stopped": 4, "no_work": 6}
    assert set(once.CODES) == set(q_facts.ONCE_OUTCOMES)
    assert len(set(once.CODES.values())) == len(once.CODES)
    assert once.EXIT_STOPPED == q_facts.STOP_EXIT_CODE


def test_no_outcome_takes_a_code_the_cli_already_means_something_else_by():
    """2 is a usage error and 3 rows written but not yet queryable (cli/shared.py)."""
    assert not {shared.EXIT_USAGE, shared.EXIT_PARTIAL, shared.EXIT_UNKNOWN} & set(
        once.CODES.values()
    )


def test_q_and_python_name_the_same_kinds():
    assert set(q_facts.ONCE_KINDS) == KINDS


def test_the_wait_for_input_is_bounded_and_documented():
    assert 0 < q_facts.ONCE_DEFAULT_TIMEOUT_SECONDS <= q_facts.ONCE_MAX_TIMEOUT_SECONDS
    text = (ROOT / "src/etl/core/once.q").read_text()
    assert "input_timeout" in text, "once.q documents the no-work outcome of the input wait"
    assert "not a" in text and "cancelled" in text, "and says it is not a query cancellation"


# --------------------------------------------------------- every kind has a runner


def test_every_declaration_verb_names_the_runner_that_ends_its_unit():
    """One runner per kind, owned by the framework: a verb with none is a kind with no --once."""
    missing = [v.verb for v in VERBS if not v.once_runner]
    assert not missing, f"no once_runner in model/kinds.py for {missing}"


def test_each_runner_is_a_function_q_defines():
    core = "\n".join(p.read_text() for p in (ROOT / "src/etl/core").glob("*.q"))
    for runner in {v.once_runner for v in VERBS}:
        assert re.search(rf"^\.{re.escape(runner)}:", core, flags=re.M), f"q defines no .{runner}"


def test_a_job_does_not_implement_its_own_one_shot_loop():
    """The runner owns termination: no declaration reads the once flag or exits the process."""
    offenders = []
    for sub in ("streaming", "workers", "reactions", "transforms"):
        for path in sorted((ROOT / "src/etl" / sub).glob("*.q")):
            # code only: a comment may say "at-least-once" or "once per window"
            code = "\n".join(
                re.sub(r"\s/ .*$", "", line)
                for line in path.read_text().splitlines()
                if not line.lstrip().startswith("/")
            )
            if re.search(r"\.qetl\.once\.|`once\b|\"-once\"|\bexit\b\s+\d", code):
                offenders.append(f"{sub}/{path.name}")
    assert not offenders, f"a job file handles --once itself: {offenders}"


def _scaffold_takes_part(kind: str) -> bool:
    verb = SCAFFOLD_VERB[kind]
    return verb is not None and any(v.verb == verb and v.once_runner for v in VERBS)


@pytest.mark.parametrize("kind", SCAFFOLD_KINDS)
def test_every_scaffold_kind_gets_once_from_its_runner_or_is_named_below(kind):
    """`uqs job new --kind X` writes a declaration whose verb has a runner, so the new job has
    --once the moment it exists. A new kind has to say which verb it writes (SCAFFOLD_VERB),
    or be named in GAPS with the reason - it cannot be left out unnoticed."""
    assert kind in SCAFFOLD_VERB, f"model/kinds.py SCAFFOLD_VERB does not place --kind {kind}"
    if f"scaffold {kind}" in {g.name for g in GAPS}:
        return
    assert _scaffold_takes_part(kind), f"--kind {kind} writes a verb with no --once runner"
    assert SCAFFOLD_VERB[kind] in DECLARATION_VERBS


def test_the_scaffold_kinds_are_exactly_the_ones_placed():
    assert set(SCAFFOLD_VERB) == set(SCAFFOLD_KINDS)


def test_every_declared_job_is_reached_by_exactly_one_launch_interface():
    """A streaming job by `start --once`, a bounded worker by `job backfill --once`."""
    from uqs.model.declarations import read_declarations
    from uqs.model.pipeline import PipelineKind
    from uqs.paths import repo_root

    streams = once.job_processes()
    for d in read_declarations(repo_root()):
        if d.kind is PipelineKind.BACKFILL:
            assert d.procname not in streams, f"{d.procname}: a worker's process is not `start`'s"
        else:
            assert streams[d.procname] == d.name


# ------------------------------------------------------ what is not supported yet


@dataclass(frozen=True)
class Gap:
    """A kind or launch interface that does not take part in --once yet."""

    name: str
    why: str
    #: whether it takes part now: False today, so its strict xfail fails; True the day it is built
    supported: Callable[[], bool]


def _carries_once(*path: str) -> Callable[[], bool]:
    return lambda: "--once" in _options(*path)


def _backfill_attachment_accepts_once() -> bool:
    return False  # `uqs job backfill --once --attachment` is refused (cli/backfill.py)


def _run_stream_reads_once() -> bool:
    return bool(
        re.search(r"`once\b|\.qetl\.once\.", (ROOT / "scripts/jobs/run_stream.q").read_text())
    )


#: Every kind and launch interface the issue names that does not take part yet, each with the
#: reason. Each is a STRICT xfail below: it fails now, naming itself, and when it gains support
#: the xfail reports an unexpected pass, so the entry has to be removed with the work.
GAPS: tuple[Gap, ...] = (
    Gap(
        "scaffold connector",
        "a connector is a process outside the stack, supervised by `uqs connector`: it has no "
        "framework runner to own its unit yet",
        lambda: _scaffold_takes_part("connector"),
    ),
    Gap(
        "job attachment add",
        "the attached publisher is started once verified, by its own launcher; its unit is "
        "not yet defined",
        _carries_once("job", "attachment", "add"),
    ),
    Gap(
        "connector start",
        "an external connector has no framework runner yet",
        _carries_once("connector", "start"),
    ),
    Gap(
        "connector restart",
        "an external connector has no framework runner yet",
        _carries_once("connector", "restart"),
    ),
    Gap(
        "job backfill --attachment",
        "an attached backfill runs through scripts/jobs/attached_backfill.q, a different runner",
        _backfill_attachment_accepts_once,
    ),
    Gap(
        "scripts/jobs/run_stream.q",
        "the TorQ-free streaming runner hosts a plant in the same process, so a unit is not "
        "yet separable from it",
        _run_stream_reads_once,
    ),
)


@pytest.mark.parametrize(
    "gap",
    [pytest.param(g, id=g.name, marks=pytest.mark.xfail(strict=True, reason=g.why)) for g in GAPS],
)
def test_what_does_not_take_part_yet_is_named_and_fails(gap):
    assert gap.supported(), f"{gap.name} has no --once: {gap.why}"


def test_every_gap_says_why():
    assert all(g.why.strip() for g in GAPS) and len({g.name for g in GAPS}) == len(GAPS)


# ---------------------------------------------------- the launch interfaces that carry it

#: What each launch interface that takes --once carries.
LAUNCHERS = {
    ("start",): {"--once", "--once-timeout"},
    ("restart",): {"--once", "--once-timeout"},
    ("job", "backfill"): {"--once"},
}


@pytest.mark.parametrize("path", LAUNCHERS, ids=" ".join)
def test_every_supported_launcher_carries_its_once_options(path):
    missing = LAUNCHERS[path] - _options(*path)
    assert not missing, f"`uqs {' '.join(path)}` lacks {sorted(missing)}"


@pytest.mark.parametrize("path", [("start",), ("restart",), ("job", "backfill")], ids=" ".join)
def test_the_option_means_the_same_on_every_launcher(path):
    helps = {" ".join(p): next(o for o in _command(*p).params if "--once" in o.opts).help
             for p in LAUNCHERS}  # fmt: skip
    assert len(set(helps.values())) == 1, "--once drifted between launchers"
    assert q_facts.ONCE_EXIT_NO_WORK.__str__() in helps[" ".join(path)], "its help states the codes"


def test_the_start_flags_are_what_q_reads():
    assert once.flags() == ["-once"]
    assert once.flags(90) == ["-once", "-once_timeout", "90"]
    assert once.flags(1.5) == ["-once", "-once_timeout", "1.5"]
    for bad in (0, -1, 3601, 1e-7):
        with pytest.raises(UqsError):
            once.flags(bad)


# --------------------------------------------------------------- start / restart --once


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


class _Launched:
    """What `start --once` asked of the world, with the world stubbed."""

    def __init__(self, monkeypatch, *, running=(), outcome=None):
        self.torq_sh: list[list[str]] = []
        self.stopped: list[Any] = []
        monkeypatch.setattr(once_launch, "_paths", lambda: object())
        monkeypatch.setattr(alive, "running", lambda paths, port=None: set(running))
        monkeypatch.setattr(runtime, "stop", lambda *a, **kw: self.stopped.append(a))

        def run_torq_sh(paths, args, **kw):
            self.torq_sh.append(list(args))
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(runtime, "run_torq_sh", run_torq_sh)
        self.outcome = outcome
        monkeypatch.setattr(once, "wait", lambda *a, **kw: self.outcome)


def _outcome(name, reason="", rows=0, **doc):
    return once.Outcome(
        name, once.CODES[name], reason, {"kind": "poll", "rows": rows, "units": 1, **doc}
    )


def _invoke(verb, *argv):
    return CliRunner().invoke(cli.app, [verb, *argv], catch_exceptions=False)


@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.parametrize(
    ("name", "code"), [("done", 0), ("no_work", 6), ("failed", 1), ("stopped", 4)]
)
def test_the_exit_status_is_the_units_outcome(monkeypatch, verb, name, code):
    seen = _Launched(monkeypatch, outcome=_outcome(name))
    result = _invoke(verb, "demo_deals_feed1", "--once")
    assert result.exit_code == code, result.output
    assert seen.torq_sh == [["start", "demo_deals_feed1", "-extras", "-once"]]
    assert seen.stopped == [], "nothing was stopped, restart included"


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_json_reports_the_outcome_and_the_exit_code(monkeypatch, verb):
    _Launched(monkeypatch, outcome=_outcome("no_work", "no_new_data"))
    result = _invoke(verb, "demo_deals_feed1", "--once", "--json")
    doc = json.loads(result.stdout)
    assert result.exit_code == 6
    assert (doc["outcome"], doc["reason"], doc["exit_code"], doc["ok"]) == (
        "no_work", "no_new_data", 6, True,
    )  # fmt: skip
    assert (doc["process"], doc["job"], doc["once"]) == (
        "demo_deals_feed1",
        "demo_deals_feed",
        True,
    )


def test_a_failure_is_not_ok_in_the_document(monkeypatch):
    _Launched(monkeypatch, outcome=_outcome("failed", "upstream refused"))
    doc = json.loads(_invoke("start", "demo_deals_feed1", "--once", "--json").stdout)
    assert (doc["outcome"], doc["ok"], doc["exit_code"], doc["reason"]) == (
        "failed", False, 1, "upstream refused",
    )  # fmt: skip


def test_the_input_timeout_reaches_the_start_line(monkeypatch):
    seen = _Launched(monkeypatch, outcome=_outcome("no_work", "input_timeout"))
    _invoke("start", "fxpositions1", "--once", "--once-timeout", "90")
    assert seen.torq_sh == [["start", "fxpositions1", "-extras", "-once", "-once_timeout", "90"]]


def test_trace_and_once_combine(monkeypatch):
    seen = _Launched(monkeypatch, outcome=_outcome("done"))
    _invoke("start", "demo_deals_feed1", "--once", "--trace")
    assert seen.torq_sh == [["start", "demo_deals_feed1", "-extras", "-trace", "-once"]]


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_a_running_process_is_refused_and_left_alone(monkeypatch, verb):
    seen = _Launched(monkeypatch, running={"demo_deals_feed1"}, outcome=_outcome("done"))
    result = _invoke(verb, "demo_deals_feed1", "--once")
    assert result.exit_code == EXIT_REFUSED
    assert seen.torq_sh == [] and seen.stopped == [], "no start line was run and nothing stopped"


def test_the_refusal_says_why_and_how_to_get_a_fresh_unit(monkeypatch):
    log = _errors(monkeypatch)
    _Launched(monkeypatch, running={"demo_deals_feed1"})
    _invoke("start", "demo_deals_feed1", "--once")
    assert any("never stops or reuses a running process" in m for m in log), log


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["--once"], "name exactly one job process"),
        (["all", "--once"], "name exactly one job process"),
        (["demo_deals_feed1", "fxfeed1", "--once"], "name exactly one job process"),
        (["stp1", "--once"], "not a job process"),
        (["rdb1", "--once"], "not a job process"),
        (["gateway1", "--once"], "not a job process"),
        (["deals_backfill1", "--once"], "uqs job backfill"),
    ],
)
def test_only_one_streaming_jobs_process_has_a_unit(monkeypatch, argv, says):
    log = _errors(monkeypatch)
    seen = _Launched(monkeypatch)
    result = _invoke("start", *argv)
    assert result.exit_code == EXIT_REFUSED
    assert any(says in m for m in log), log
    assert seen.torq_sh == [], "refused before torq.sh was asked for anything"


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["demo_deals_feed1", "--once-timeout", "5"], "--once-timeout needs --once"),
        (["demo_deals_feed1", "--once", "--follow"], "--follow does not apply with --once"),
    ],
)
def test_options_that_do_not_fit_the_unit_are_usage_errors(monkeypatch, argv, says):
    log = _errors(monkeypatch)
    seen = _Launched(monkeypatch)
    assert _invoke("start", *argv).exit_code == EXIT_USAGE
    assert any(says in m for m in log), log
    assert seen.torq_sh == []


def test_a_dry_run_shows_the_start_line_with_once_and_starts_nothing(monkeypatch):
    seen = _Launched(monkeypatch, outcome=_outcome("done"))
    planned: list[Any] = []

    def plan(paths, procs, port, selected, extras=()):
        planned.append((procs, list(extras)))
        return [start_lines.StartLine(procs, f"q torq_stream.q {' '.join(extras)}", "/log")]

    monkeypatch.setattr(start_lines, "plan", plan)
    result = _invoke(
        "start", "demo_deals_feed1", "--once", "--once-timeout", "30", "--dry-run", "--json"
    )
    doc = json.loads(result.stdout)
    assert result.exit_code == 0
    assert planned == [("demo_deals_feed1", ["-once", "-once_timeout", "30"])]
    assert (doc["dry_run"], doc["once"], doc["ok"]) == (True, True, True)
    assert seen.torq_sh == [], "a dry run starts nothing"


def test_a_profile_has_no_single_unit(monkeypatch):
    log = _errors(monkeypatch)
    _Launched(monkeypatch)
    assert _invoke("start", "--profile", "fx", "--once").exit_code == EXIT_REFUSED
    assert any("not --profile" in m for m in log), log


def test_without_once_the_launch_does_not_go_through_the_once_path(monkeypatch):
    """Without --once nothing changes: the once launcher is never entered."""
    entered = []
    monkeypatch.setattr(once_launch, "start", lambda *a, **kw: entered.append(1))
    monkeypatch.setattr(runtime, "start", lambda *a, **kw: type("R", (), {"returncode": 0})())
    _invoke("start", "demo_deals_feed1", "--dry-run")
    assert entered == []


# --------------------------------------------------------------- job backfill --once


def test_the_backfill_start_line_carries_once_only_when_asked():
    from uqs.stack import backfill

    frm, to = datetime(2026, 9, 13, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC)
    plain = backfill.backfill_flags("demo_deals_backfill", "v1", frm, to)
    once_ = backfill.backfill_flags("demo_deals_backfill", "v1", frm, to, once=True)
    assert "-once" not in plain
    assert "-once" in once_ and [f for f in once_ if f != "-once"] == plain


class _Backfill:
    def __init__(self, monkeypatch, *, running=(), outcome=None):
        from uqs.stack import backfill

        self.started: list[dict[str, Any]] = []
        monkeypatch.setattr(backfill_cli, "_paths", lambda: object())
        monkeypatch.setattr(alive, "running", lambda paths, port=None: set(running))
        monkeypatch.setattr(backfill, "resolve_version", lambda w, v: v or "v1")

        def start(paths, worker, version, frm, to, **kw):
            self.started.append(kw)
            return type("R", (), {"returncode": 0, "stdout": ""})()

        monkeypatch.setattr(backfill, "start", start)
        monkeypatch.setattr(once, "wait", lambda *a, **kw: outcome)


def _backfill(*argv):
    return CliRunner().invoke(
        cli.app,
        [
            "job",
            "backfill",
            "demo_deals_backfill",
            "--source-version",
            "v1",
            "--date",
            "2026-09-13",
            *argv,
        ],
        catch_exceptions=False,
    )


@pytest.mark.parametrize(
    ("name", "code"), [("done", 0), ("no_work", 6), ("failed", 1), ("stopped", 4)]
)
def test_a_backfill_once_exits_with_the_windows_outcome(monkeypatch, name, code):
    seen = _Backfill(monkeypatch, outcome=_outcome(name, kind="bounded"))
    result = _backfill("--once")
    assert result.exit_code == code, result.output
    assert seen.started and seen.started[0]["once"] is True


def test_a_backfill_once_reports_json(monkeypatch):
    _Backfill(monkeypatch, outcome=_outcome("no_work", "all_covered", kind="bounded"))
    result = _backfill("--once", "--json")
    doc = json.loads(result.stdout)
    assert (result.exit_code, doc["outcome"], doc["reason"], doc["job"]) == (
        6, "no_work", "all_covered", "demo_deals_backfill",
    )  # fmt: skip


def test_a_backfill_once_refuses_a_running_process_before_starting_anything(monkeypatch):
    seen = _Backfill(
        monkeypatch,
        running={stack_backfill.procname_for("demo_deals_backfill")},
        outcome=_outcome("done"),
    )
    assert _backfill("--once").exit_code == EXIT_REFUSED
    assert seen.started == []


@pytest.mark.parametrize(
    "argv", [["--mode", "plan"], ["--mode", "validate"], ["--attachment", "pb-uat"]]
)
def test_a_backfill_once_refuses_what_runs_no_window_here(monkeypatch, argv):
    seen = _Backfill(monkeypatch, outcome=_outcome("done"))
    assert _backfill("--once", *argv).exit_code == EXIT_REFUSED
    assert seen.started == []


def test_without_once_a_backfill_is_asked_for_no_once(monkeypatch):
    seen = _Backfill(monkeypatch, outcome=None)
    _backfill()
    assert "once" not in seen.started[0], "the start is called exactly as it was before --once"


# ------------------------------------------------------------ waiting for the outcome


def _write(path: Path, *, outcome="done", code=0, started=None, **extra):
    started = started or datetime.now(UTC) + timedelta(seconds=1)
    path.write_text(
        json.dumps(
            {"outcome": outcome, "code": code, "reason": "", "started_at": started.strftime(
                "%Y-%m-%dT%H:%M:%S.%f000"), **extra}
        )
    )  # fmt: skip


@pytest.fixture
def status(tmp_path, monkeypatch):
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    return tmp_path / f"{q_facts.ONCE_FILE_PREFIX}feed1{q_facts.ONCE_FILE_SUFFIX}"


def test_the_outcome_of_this_launch_is_read(status):
    launched = datetime.now(UTC)
    _write(status, outcome="no_work", code=6, reason="x")
    got = once.wait(PATHS, "feed1", launched, sleep=lambda s: None)
    assert (got.outcome, got.code) == ("no_work", 6)


def test_a_file_an_earlier_launch_left_is_not_this_ones(status):
    launched = datetime.now(UTC)
    _write(status, started=launched - timedelta(minutes=5))
    ticks = iter(range(100))
    clock = lambda: launched + timedelta(seconds=next(ticks) * 10)  # noqa: E731
    got = once.wait(PATHS, "feed1", launched, sleep=lambda s: None, now=clock, is_up=lambda: False)
    assert got.outcome == "failed" and "without recording an outcome" in got.reason


def test_a_process_that_is_up_is_waited_for_past_the_startup_grace(status):
    launched = datetime.now(UTC)
    ups = iter([True, True, True])
    state = {"n": 0}

    def sleep(_):
        state["n"] += 1
        if state["n"] == 4:
            _write(status)

    clock = lambda: launched + timedelta(minutes=state["n"])  # noqa: E731
    got = once.wait(PATHS, "feed1", launched, sleep=sleep, now=clock, is_up=lambda: next(ups))
    assert got.outcome == "done" and state["n"] == 4


def test_a_code_that_is_not_q_s_for_the_outcome_is_refused_loudly(status):
    launched = datetime.now(UTC)
    _write(status, outcome="done", code=9)
    with pytest.raises(UqsError, match="not q's contract"):
        once.wait(PATHS, "feed1", launched, sleep=lambda s: None)
