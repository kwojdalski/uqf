"""One exit code per outcome, on every command (#1885).

- an empty result - nothing recorded, nothing written yet - exits 0;
- an unknown named thing - a process, a target, a run, a job - exits 1;
- a command line wrong as given exits 2: Click's usage errors and the
  command's own (`UqsUsageError`) alike;
- a question answered no - or with no terminal to ask on - exits 1
  (`safety.EXIT_DECLINED`), having changed nothing.

The usage rule is held over the whole tree; the others over the cases the
audit found, in a throwaway data root, since each is about what is (not) there.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
import typer
import typer.main
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs.cli import safety
from uqs.cli.shared import app
from uqs.paths import UqsError
from uqs.stack import procs as stack_procs

runner = CliRunner()


def _leaves(command, path=()):
    if getattr(command, "hidden", False):
        return
    subs = getattr(command, "commands", None)
    if subs is None:
        yield path, command
        return
    for name, sub in subs.items():
        yield from _leaves(sub, (*path, name))


LEAVES = dict(_leaves(typer.main.get_command(app)))


# ------------------------------------------------------------- usage errors: 2


@pytest.mark.parametrize(
    "path",
    # `exec` passes every word on to torq.sh: it has no options of its own to miss
    sorted(p for p, c in LEAVES.items() if not c.context_settings.get("ignore_unknown_options")),
    ids=" ".join,
)
def test_an_unknown_option_is_a_usage_error_everywhere(path):
    result = runner.invoke(app, [*path, "--no-such-option-1885"])
    assert result.exit_code == 2, result.output


@pytest.mark.parametrize(
    "path",
    sorted(
        p
        for p, c in LEAVES.items()
        if any(x.required and x.param_type_name == "argument" for x in c.params)
    ),
    ids=" ".join,
)
def test_a_missing_argument_is_a_usage_error_everywhere(path):
    assert runner.invoke(app, list(path)).exit_code == 2


#: The command's own checks of its command line, which Click cannot see.
USAGE = {
    "deploy rollback": ["deploy", "rollback"],
    "deploy prune": ["deploy", "prune"],
    "job gaps": ["job", "gaps", "crypto_markout"],
    "job check": ["job", "check", "demo_deals"],
    "--yes before the command": ["--yes", "job", "remove", "x"],
    "--target after the root": ["--target", "uat", "status"],
    "query PROC and --port": ["query", "rdb1", "1+1", "--port", "6052"],
    "logs --id (renamed --channel)": ["logs", "--id", "x"],
    "logs --channel naming no channel": ["logs", "--channel", "nope"],
    "start --level without --follow": ["start", "--level", "INFO"],
    "--dry-run with --apply": ["data", "migrate", "trade", "--date", "2026-09-01", "--apply", "-n"],
}


@pytest.mark.parametrize("argv", USAGE.values(), ids=USAGE.keys())
def test_a_wrong_command_line_exits_2(argv):
    assert runner.invoke(app, argv).exit_code == 2


# ------------------------------------------------------ unknown names: 1


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> dict[str, str]:
    """An empty data root and HOME: nothing has run here, nothing is attached."""
    root = tmp_path_factory.mktemp("exit-codes")
    for d in ("data", "home", "attachments"):
        (root / d).mkdir()
    drop = ("UQF_PEACHQ", "UQF_Q_IMPL", "UQF_PEACHQ_REASON", "UQS_RUNTIME", "UQS_TARGET")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    return env | {
        "UQS_DATA_ROOT": str(root / "data"),
        "HOME": str(root / "home"),
        "UQS_ATTACH_DIR": str(root / "attachments"),
        "NO_COLOR": "1",
    }


def _uqs(env: dict[str, str], *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "uqs", *argv],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )


#: A name that names nothing -> what the refusal says.
UNKNOWN = {
    "config show": (["config", "show", "nosuch1885"], "no process nosuch1885 in this runtime"),
    "logs": (["logs", "nosuch1885"], "no process nosuch1885 in this runtime"),
    "start": (["start", "nosuch1885"], "no process nosuch1885 in this runtime"),
    "graph": (["graph", "nosuch1885", "--offline"], "no process nosuch1885 in this runtime"),
    "schema": (["schema", "nosuch1885", "trade"], "no process nosuch1885 in this runtime"),
    "query": (["query", "nosuch1885", "1+1"], "no process nosuch1885 in this runtime"),
    "target log": (["target", "log", "nosuch1885"], "no target nosuch1885 - "),
    "target check": (["target", "check", "nosuch1885"], "nosuch1885"),
    "job preview": (["job", "preview", "nosuch1885"], "no streaming job 'nosuch1885'"),
    "job runs show": (
        ["job", "runs", "show", "0a1b2c3d-0000-0000-0000-000000000000"],
        "no run 0a1b2c3d",
    ),
    "job attachment show": (["job", "attachment", "show", "nosuch1885"], "no attachment"),
    "connector status": (["connector", "status", "nosuch1885"], "no connector 'nosuch1885'"),
}


@pytest.mark.parametrize("case", UNKNOWN.values(), ids=UNKNOWN.keys())
def test_an_unknown_name_exits_1_and_says_so(sandbox, case):
    argv, said = case
    run = _uqs(sandbox, *argv)
    assert run.returncode == 1, run.stderr[-600:]
    assert said in run.stderr
    assert "['" not in run.stderr, "no Python list of every known name"


def test_one_message_for_an_unknown_process():
    message = str(stack_procs.unknown_processes("xyz"))
    assert message == "no process xyz in this runtime - `uqs process list` names them"
    two = str(stack_procs.unknown_processes(["a", "b"], "Nothing was started."))
    assert two == (
        "no processes a, b in this runtime - `uqs process list` names them. Nothing was started."
    )


# ------------------------------------------------------- empty results: 0

#: Commands with nothing to show in an empty data root.
EMPTY = {
    "job runs list": ["job", "runs", "list"],
    "job runs upgrade": ["job", "runs", "upgrade"],
    "job runs audit": ["job", "runs", "audit", "demo_deals", "--last", "1d"],
    "job gaps": ["job", "gaps", "crypto_markout", "--last", "1d"],
    "logs": ["logs"],
    "logs PROC": ["logs", "rdb1"],
    "job attachment list": ["job", "attachment", "list"],
    "job attachment status": ["job", "attachment", "status"],
    "deploy history": ["deploy", "history"],
    "data check": ["data", "check"],
    "job checkpoint remove": ["job", "checkpoint", "remove", "demo_deals_backfill", "--yes"],
}


@pytest.mark.parametrize("argv", EMPTY.values(), ids=EMPTY.keys())
def test_an_empty_result_exits_0(sandbox, argv):
    run = _uqs(sandbox, *argv)
    assert run.returncode == 0, run.stderr[-600:]


# ---------------------------------------------------------- declining: 1


def test_without_a_terminal_a_question_is_refused_naming_yes(monkeypatch):
    monkeypatch.setattr(safety, "interactive", lambda: False)
    with pytest.raises(UqsError, match="no terminal to ask on: add --yes"):
        safety.ask("Remove these?")
    safety.confirm("Remove these?", yes=True)  # --yes asks nothing


def test_the_default_answer_is_no(monkeypatch, terminal):
    asked = {}

    def confirm(question, default):
        asked["default"] = default
        return default

    monkeypatch.setattr(safety.typer, "confirm", confirm)
    assert safety.ask("Remove these?") is False and asked["default"] is False


@pytest.mark.parametrize("answer", [False, typer.Abort()], ids=["no", "eof"])
def test_a_no_and_an_eof_decline_alike(monkeypatch, terminal, answer):
    def confirm(*_a, **_k):
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(safety.typer, "confirm", confirm)
    with pytest.raises(typer.Exit) as declined:
        safety.confirm("Remove these?", yes=False)
    assert declined.value.exit_code == safety.EXIT_DECLINED == 1
