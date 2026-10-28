"""`--json` means one JSON document on stdout, refusals included (#1685).

A refused `uqs job attachment add X --json` wrote its `ERROR | refused...` log line to
stdout ahead of the document, so `json.loads(stdout)` - or jq, or anything that
shells out - failed on exactly the case a caller most needs to read. The console
log sink now goes to stderr whenever the command line carries `--json`; without
it, output is what it always was.

The end-to-end cases run the real `python -m uqs` in a subprocess: the bug was
in what reaches the process's stdout, which an in-process runner that replaces
the logger cannot see.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import typer.main
from loguru import logger

from uqs import cli
from uqs.cli import shared
from uqs.logger import core as logcore


@pytest.fixture(autouse=True)
def _restore_loguru():
    yield
    logger.remove()
    logger.add(sys.stderr)


def _uqs(tmp_path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"UQS_ATTACH_DIR": str(tmp_path / "attachments")}
    env.pop("LOG_REGEX", None)
    return subprocess.run(
        [sys.executable, "-m", "uqs", *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


# ------------------------------------------------------------ the reported case


def test_a_refused_attach_with_json_prints_one_parseable_document(tmp_path):
    run = _uqs(tmp_path, "job", "attachment", "add", "nosuch-1685", "--json")
    assert run.returncode == 1
    doc = json.loads(run.stdout)  # the whole of stdout, nothing before it
    assert doc["ok"] is False
    assert doc["attachment"] == "nosuch-1685"
    assert "needs --job and --stack-id" in doc["error"]
    assert "ERROR" in run.stderr  # the log line still exists, on stderr


def test_a_refused_attach_without_json_still_logs_to_stdout(tmp_path):
    """Human output is unchanged: only --json moves the log sink."""
    run = _uqs(tmp_path, "job", "attachment", "add", "nosuch-1685")
    assert run.returncode == 1
    assert "ERROR" in run.stdout
    assert "needs --job and --stack-id" in run.stdout


# ------------------------------------------------- every --json command, offline

#: For each command with a --json option, a command line that answers without a
#: live stack - most of them refused before reaching anything - and the text the
#: document must carry.
CASES: dict[str, tuple[list[str], str]] = {
    "list": (["list"], "processes"),
    "target list": (["target", "list"], "["),
    "runtime list": (["runtime", "list"], "uqf"),
    "job list": (["job", "list"], "["),
    "job attachment list": (["job", "attachment", "list"], "["),
    "data hdb-check": (["data", "hdb-check"], "present"),
    "runtime diff": (["runtime", "diff", "nosuch1", "nosuch2"], "is not a runtime"),
    "job receive": (["job", "receive", "--attach", "nosuch"], "--listen-port is required"),
    "job backfill": (
        ["job", "backfill", "w", "--from", "2024-01-01", "--to", "2024-01-02"],
        "--json is for --attach",
    ),
    "job attachment add": (["job", "attachment", "add", "nosuch"], "needs --job"),
    "job attachment contract approve": (
        ["job", "attachment", "contract", "approve", "nosuch.json", "--attach", "nosuch"],
        "cannot read nosuch.json",
    ),
    "job attachment contract list": (
        ["job", "attachment", "contract", "list", "--attach", "nosuch"],
        "[]",
    ),
    "job attachment show": (["job", "attachment", "show", "nosuch"], "no attachment 'nosuch'"),
    "job attachment remove": (["job", "attachment", "remove", "nosuch"], "no attachment 'nosuch'"),
    "deploy status": (
        ["deploy", "status", "--host", "nosuch.invalid", "--dest", "relative/dest"],
        "must be an absolute path",
    ),
    # #1731: every reporting command
    "summary": (["summary", "--columns", "nosuch"], "unknown summary column"),
    "graph": (["graph", "nosuchproc", "--offline"], "no process 'nosuchproc'"),
    "query": (["query"], "--json needs an expression"),
    "schema": (["schema", "nosuchproc", "trade"], "is not a declared process"),
    "logs": (["logs", "--follow"], "--follow does not apply"),
    "config get": (["config", "get", "nosuchproc"], "unknown process"),
    "job gaps": (["job", "gaps", "demo_markout", "--from", "bad", "--to", "2024-01-02"], "bad"),
    "job preview": (["job", "preview", "nosuch-x"], "is not a streaming job name"),
    "job runs status": (["job", "runs", "status", "--attach", "nosuch"], "no attachment"),
    "job runs list": (["job", "runs", "list", "--attach", "nosuch"], "no attachment"),
    "job runs show": (["job", "runs", "show", "r", "--attach", "nosuch"], "no attachment"),
    "job runs audit": (["job", "runs", "audit", "d", "--from", "bad", "--to", "2024-01-02"], "bad"),
    "connector status": (["connector", "status", "nosuch"], "no feed 'nosuch'"),
    "connector logs": (["connector", "logs", "crypto", "--follow"], "--follow does not apply"),
    "setup odbc status": (
        ["setup", "odbc", "status", "--home", "/nonexistent-1731"],
        "cannot read",
    ),
    "deploy verify": (["deploy", "verify", "--profile", "nosuch"], "unknown profile"),
    "deploy history": (["deploy", "history", "nosuch-server-1731"], "deployments"),
    "target check": (["target", "check", "nosuch"], "no target or deployed server"),
}
#: --json commands with no offline case, and why.
NO_OFFLINE_CASE = {
    # runs q over the source even to refuse it, and prints JSON lines by design
    "config sources check": "needs q",
}


def _json_commands() -> set[str]:
    def walk(cmd, path):
        if getattr(cmd, "hidden", False):
            return  # an old spelling kept for one release: its new one has the case
        subs = getattr(cmd, "commands", None)
        if subs:
            for name, sub in subs.items():
                yield from walk(sub, [*path, name])
        elif any("--json" in p.opts for p in cmd.params):
            yield " ".join(path)

    return set(walk(typer.main.get_command(cli.app), []))


def test_every_json_command_has_a_case():
    """A new --json command must be added to CASES, so it is checked too."""
    assert _json_commands() == set(CASES) | set(NO_OFFLINE_CASE)


@pytest.mark.parametrize("command", sorted(CASES))
def test_json_stdout_is_exactly_one_document(tmp_path, command):
    argv, expected = CASES[command]
    run = _uqs(tmp_path, *argv, "--json")
    assert run.returncode in (0, 1), run.stderr
    doc = json.loads(run.stdout)
    assert expected in json.dumps(doc)
    if run.returncode == 1:
        assert doc["ok"] is False


# ------------------------------------------------------------------ the pieces


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["job", "attachment", "add", "x", "--json"], True),
        (["job", "attachment", "add", "x"], False),
        (["query", "--", "--json"], False),  # after --, an argument, not an option
        (["--json", "--", "x"], True),
        (["--jsonl"], False),
    ],
)
def test_json_requested_reads_the_options_only(argv, expected):
    assert logcore.json_requested(argv) is expected


def test_the_console_sink_is_stderr_when_json_is_asked_for(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["uqs", "job", "attachment", "add", "x", "--json"])
    logcore.setup_logging(level="INFO", colored_output=False)
    logger.error("refused: something")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "refused: something" in captured.err


def test_the_console_sink_is_stdout_without_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["uqs", "job", "attachment", "add", "x"])
    logcore.setup_logging(level="INFO", colored_output=False)
    logger.error("refused: something")
    assert "refused: something" in capsys.readouterr().out


def test_json_refusal_prints_nothing_without_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["uqs", "list"])
    shared.json_refusal("nope")
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(sys, "argv", ["uqs", "list", "--json"])
    shared.json_refusal("nope")
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "nope"}
