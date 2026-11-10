"""`--json` means one JSON document on stdout, refusals included (#1685).

A refused `uqs job attachment add X --json` wrote its `ERROR | refused...` log line to
stdout ahead of the document, so `json.loads(stdout)` - or jq, or anything that
shells out - failed on exactly the case a caller most needs to read. The console
log sink went to stderr under `--json` first; since #1881 it always does.

The end-to-end cases run the real `python -m uqs` in a subprocess: the bug was
in what reaches the process's stdout, which an in-process runner that replaces
the logger cannot see.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer.main
from loguru import logger
from typer.testing import CliRunner

from uqs import cli, timefmt
from uqs.cells import Shown, cells
from uqs.cli import json_out, shared
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
    assert "needs --job and --base-port" in doc["error"]
    assert "ERROR" in run.stderr  # the log line still exists, on stderr


def test_a_refused_attach_without_json_logs_to_stderr_too(tmp_path):
    """Without --json the refusal is the same log line, on stderr as well:
    the log is never stdout's (#1881)."""
    run = _uqs(tmp_path, "job", "attachment", "add", "nosuch-1685")
    assert run.returncode == 1
    assert run.stdout == ""
    assert "ERROR" in run.stderr
    assert "needs --job and --base-port" in run.stderr


# ------------------------------------------------- every --json command, offline

#: For each command with a --json option, a command line that answers without a
#: live stack - most of them refused before reaching anything - and the text the
#: document must carry.
CASES: dict[str, tuple[list[str], str]] = {
    "target list": (["target", "list"], "["),
    "target log": (["target", "log"], "edited_outside"),
    "activity list": (
        ["activity", "list", "--db", "/nonexistent-2000/activity.sqlite3"],
        "no activity history at",
    ),
    "runtime list": (["runtime", "list"], "uqf"),
    "job list": (["job", "list"], "["),
    "process list": (["process", "list"], '"process"'),
    "profile list": (["profile", "list"], "["),
    "config field list": (["config", "field", "list"], "["),
    "config env": (["config", "env"], "KDBBASEPORT"),
    "config housekeeping show": (["config", "housekeeping", "show"], "hdbcompress"),
    "job attachment status": (["job", "attachment", "status"], "["),
    "config list": (["config", "list"], "["),
    "job attachment list": (["job", "attachment", "list"], "["),
    "override list": (["override", "list"], "q-version-check"),
    "data check": (["data", "check"], "present"),
    "data alter": (
        ["data", "alter", "pb", "--hdb", "/nonexistent-1755", "--reorder", "a"],
        "no such directory",
    ),
    "data compress": (
        ["data", "compress", "--report", "--hdb", "/nonexistent-2149"],
        "no such directory",
    ),
    # #2235: dry runs unless --apply, with a document like their siblings'
    "data replay": (
        ["data", "replay", "--from", "2026-09-23", "--to", "2026-09-25"],
        "replays one whole day",
    ),
    "data seed": (["data", "seed"], "name the runtime to copy from"),
    # #2247: confirms on the server, like data alter
    "data migrate": (["data", "migrate", "quote", "--date", "not-a-day"], "--date"),
    "runtime diff": (["runtime", "diff", "nosuch1", "nosuch2"], "is not a runtime"),
    "job receiver start": (
        ["job", "receiver", "start", "--attachment", "nosuch"],
        "--listen-port is required",
    ),
    "job receiver stop": (["job", "receiver", "stop", "--attachment", "nosuch", "-n"], "stopped"),
    "job backfill": (
        ["job", "backfill", "w", "--from", "2024-01-01", "--to", "2024-01-02"],
        "source_version",
    ),
    "job attachment add": (["job", "attachment", "add", "nosuch"], "needs --job"),
    "job attachment contract approve": (
        ["job", "attachment", "contract", "approve", "nosuch.json", "--attachment", "nosuch"],
        "cannot read nosuch.json",
    ),
    "job attachment contract list": (
        ["job", "attachment", "contract", "list", "--attachment", "nosuch"],
        "[]",
    ),
    "job attachment show": (["job", "attachment", "show", "nosuch"], "no attachment 'nosuch'"),
    "job attachment remove": (["job", "attachment", "remove", "nosuch"], "no attachment 'nosuch'"),
    "deploy status": (
        ["deploy", "status", "--ssh", "nosuch.invalid", "--dest", "relative/dest"],
        "must be an absolute path",
    ),
    # #2125: push, rollback and prune print one document too, only under --json
    "deploy push": (["deploy", "push", "nosuch.tar.gz", "nosuch-target-2125"], "deployments"),
    "deploy rollback": (
        ["deploy", "rollback", "--ssh", "nosuch.invalid", "--dest", "relative/dest"],
        "must be an absolute path",
    ),
    "deploy prune": (
        ["deploy", "prune", "--ssh", "nosuch.invalid", "--dest", "relative/dest", "--keep", "1"],
        "must be an absolute path",
    ),
    # #1731: every reporting command
    "status": (["status", "--columns", "nosuch"], "unknown summary column"),
    "graph": (["graph", "nosuchproc", "--offline"], "no process nosuchproc in this runtime"),
    "query": (["query"], "--json needs an expression"),
    "schema": (["schema", "nosuchproc", "trade"], "no process nosuchproc in this runtime"),
    "logs": (["logs", "--follow"], "--follow does not apply"),
    "config show": (["config", "show", "nosuchproc"], "no process nosuchproc"),
    "job gaps": (["job", "gaps", "crypto_markout", "--from", "bad", "--to", "2024-01-02"], "bad"),
    "job preview": (["job", "preview", "nosuch-x"], "is not a streaming job name"),
    "job check": (
        ["job", "check", "1bad", "--from", "2026-09-11", "--to", "2026-09-12"],
        "is not a dataset name",
    ),
    "job runs list": (["job", "runs", "list", "--attachment", "nosuch"], "no attachment"),
    "job runs show": (["job", "runs", "show", "r", "--attachment", "nosuch"], "no attachment"),
    "job runs audit": (["job", "runs", "audit", "d", "--from", "bad", "--to", "2024-01-02"], "bad"),
    "data snapshot show": (["data", "snapshot", "show", "not an id"], "is not a snapshot id"),
    # #2033: two sides that are one, refused before anything is reached
    "data diff": (
        [
            "data",
            "diff",
            "--left",
            "prod",
            "--right",
            "prod",
            "--table",
            "t",
            "--date",
            "2026-09-11",
        ],
        "both name prod",
    ),
    "connector status": (["connector", "status", "nosuch"], "no connector 'nosuch'"),
    "connector logs": (["connector", "logs", "crypto", "--follow"], "--follow does not apply"),
    "setup odbc status": (
        ["setup", "odbc", "status", "--odbc-home", "/nonexistent-1731"],
        "cannot read",
    ),
    "deploy check": (["deploy", "check", "--profile", "nosuch"], "unknown profile"),
    "deploy history": (["deploy", "history", "nosuch-server-1731"], "deployments"),
    "target check": (["target", "check", "nosuch"], "no target or deployed server"),
    "target tunnel plan": (["target", "tunnel", "plan", "nosuch"], "no target or deployed server"),
    "target tunnel check": (
        ["target", "tunnel", "check", "nosuch"],
        "no target or deployed server",
    ),
    # the config push history (#2002): a target that is not declared, refused offline
    "target config history": (["target", "config", "history", "nosuch-2002"], "no target"),
    "target config show": (
        ["target", "config", "show", "nosuch-2002", "20261104T000000000Z-00000000"],
        "no target",
    ),
    "target config diff": (
        ["target", "config", "diff", "nosuch-2002", "20261104T000000000Z-00000000"],
        "no target",
    ),
}
#: --json commands with no offline case, and why.
NO_OFFLINE_CASE = {
    # runs q over the source even to refuse it
    "config sources check": "needs q",
    # reads the store through a short-lived q, empty or not
    "data snapshot list": "needs q",
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
    assert run.returncode in (0, 1, 2), run.stderr  # 2: a usage error (#1885)
    doc = json.loads(run.stdout)
    assert expected in json.dumps(doc)
    if run.returncode != 0:
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


def test_the_console_sink_is_stderr_without_json(monkeypatch, capsys):
    """#1881: --json no longer decides where the log goes - it is stderr's."""
    monkeypatch.setattr(sys, "argv", ["uqs", "job", "attachment", "add", "x"])
    logcore.setup_logging(level="INFO", colored_output=False)
    logger.error("refused: something")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "refused: something" in captured.err


def test_json_refusal_prints_nothing_without_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["uqs", "list"])
    shared.json_refusal("nope")
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(sys, "argv", ["uqs", "list", "--json"])
    shared.json_refusal("nope")
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "nope"}


# ------------------------------------------------ the contract, tree-wide (#1882)

#: A positional no command takes as a name of anything it knows.
BOGUS = "bogus-positional-1882"
#: A time as q, TorQ or ISO-8601 spells it with no zone: a document never
#: carries one, since nobody reading it could tell whose clock it was.
_ZONELESS = re.compile(
    r"\d{4}[.-]\d{2}[.-]\d{2}[DT ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?|\d{2}:\d{2}:\d{2}"
)


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> dict[str, str]:
    """An environment whose state - data root, attachments, HOME - is empty
    and throwaway, so a command run with nothing to go on writes nowhere real."""
    root = tmp_path_factory.mktemp("json-contract")
    dirs = {name: root / name for name in ("data", "attachments", "home")}
    for d in dirs.values():
        d.mkdir()
    drop = ("UQF_PEACHQ", "UQF_Q_IMPL", "UQF_PEACHQ_REASON", "UQS_RUNTIME", "UQS_TARGET")
    env = {k: v for k, v in os.environ.items() if k not in drop and k != "LOG_REGEX"}
    return env | {
        "UQS_DATA_ROOT": str(dirs["data"]),
        "UQS_ATTACH_DIR": str(dirs["attachments"]),
        "HOME": str(dirs["home"]),
        "NO_COLOR": "1",
    }


def _times_without_a_zone(value, at: str = "$") -> list[str]:
    """Every string in a document that is a time spelt with no offset."""
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _times_without_a_zone(v, f"{at}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _times_without_a_zone(v, f"{at}[{i}]")]
    if isinstance(value, str) and _ZONELESS.fullmatch(value):
        return [f"{at} = {value!r}"]
    return []


@pytest.mark.parametrize("extra", [(), (BOGUS,)], ids=["no-arguments", "bogus-positional"])
@pytest.mark.parametrize("command", sorted(_json_commands()))
def test_every_json_command_prints_one_object_whatever_it_is_given(sandbox, command, extra):
    """The promise `--json` makes, held over every command that takes it:
    stdout is one JSON object whatever the command is given - nothing, or a
    name of nothing - and a non-zero exit is a refusal saying so (`ok: false`).
    Click's own usage errors (exit 2) and an uncaught refusal included: both
    used to leave stdout empty and a box or a traceback on stderr."""
    run = subprocess.run(
        [sys.executable, "-m", "uqs", *command.split(), *extra, "--json"],
        capture_output=True,
        text=True,
        env=sandbox,
        stdin=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )
    assert run.stdout.strip(), f"stdout is empty (exit {run.returncode}): {run.stderr[-600:]}"
    doc = json.loads(run.stdout)
    assert isinstance(doc, dict), f"a {type(doc).__name__}, not an object"
    if run.returncode != 0:
        assert doc.get("ok") is False and doc.get("error"), doc
    assert not _times_without_a_zone(doc)


def test_a_usage_error_under_json_is_a_refusal_with_exit_code_2(sandbox):
    run = subprocess.run(
        [sys.executable, "-m", "uqs", "config", "show", "--json"],
        capture_output=True,
        text=True,
        env=sandbox,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    assert run.returncode == 2
    doc = json.loads(run.stdout)
    assert doc["ok"] is False and "Missing argument 'PROC'" in doc["error"]
    assert "uqs config show --help" in doc["error"]


def test_an_unknown_target_under_json_is_a_refusal(sandbox):
    run = subprocess.run(
        [sys.executable, "-m", "uqs", "--target", "nosuch-1882", "status", "--json"],
        capture_output=True,
        text=True,
        env=sandbox,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    assert run.returncode == 1
    doc = json.loads(run.stdout)
    assert doc["ok"] is False and "nosuch-1882" in doc["error"]


def _printed_json(path: Path) -> list[str]:
    """Where `path` prints a json.dumps: given to print (to any stream - the
    report went to an `out` that was stdout), typer.echo or sys.stdout.write -
    a JSON printer. A json.dumps written into a file through its handle is
    not one, nor is one inside a script's text that runs on a server."""

    def printer(call: ast.Call) -> bool:
        f = call.func
        if isinstance(f, ast.Name):
            return f.id == "print"
        return isinstance(f, ast.Attribute) and (
            f.attr == "echo" or (f.attr == "write" and ast.unparse(f.value) == "sys.stdout")
        )

    def dumps(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "dumps"
        )

    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and printer(node):
            if any(dumps(n) for a in node.args for n in ast.walk(a)):
                found.append(f"{path.parent.name}/{path.name}:{node.lineno}")
    return found


def test_no_command_prints_json_but_through_emit():
    """One printer, so one contract: every document is json_out.emit's - an
    object, its values plain, its times zoned. A json.dumps written to stdout
    anywhere else in the CLI is a second printer that holds to none of it.
    deploy/ too (#2125): push, rollback and prune printed their reports there,
    outside emit and with no --json, where a scan of cli/ alone never looked."""
    cli_dir = Path(cli.__file__).parent
    found = [
        f"{path.name}:{n}"
        for path in sorted(cli_dir.glob("*.py"))
        if path.name != "json_out.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if "json.dumps(" in line
    ]
    deploy_dir = cli_dir.parent / "deploy"
    found += [hit for path in sorted(deploy_dir.glob("*.py")) for hit in _printed_json(path)]
    assert not found, found


def test_the_json_printer_scan_finds_a_printer_and_nothing_else(tmp_path):
    """The deploy/ scan's own check: the shapes the three commands printed in
    are found, a file write and a server script's text are not."""
    path = tmp_path / "mod.py"
    path.write_text(
        "import json, sys\n"
        "print(json.dumps({}, indent=2), file=out)\n"
        "typer.echo(json.dumps({}))\n"
        "sys.stdout.write(json.dumps({}))\n"
        "fh.write_text(json.dumps({}))\n"
        "handle.write(json.dumps({}) + '\\n')\n"
        "body = json.dumps({})\n"
        "SCRIPT = 'print(json.dumps(out))'\n"
    )
    assert _printed_json(path) == [f"{tmp_path.name}/mod.py:{n}" for n in (2, 3, 4)]


def test_emit_refuses_a_document_that_is_not_an_object():
    with pytest.raises(TypeError):
        json_out.emit([1, 2])  # ty: ignore[invalid-argument-type] - the point of the test


@pytest.mark.parametrize(
    ("value", "naive", "expected"),
    [
        ("2026.08.22D14:21:10.644413000", "utc", "2026-08-22T14:21:10.644413000+00:00"),
        ("2026-09-13T06:30:00.123456789", "utc", "2026-09-13T06:30:00.123456789+00:00"),
        ("2026-08-22T14:21:10Z", "local", "2026-08-22T14:21:10+00:00"),
        ("2026-08-22 14:21:10+0200", "utc", "2026-08-22T14:21:10+02:00"),
        (datetime(2026, 1, 2, 3, 4, 5), "utc", "2026-01-02T03:04:05+00:00"),
        (datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "local", "2026-01-02T03:04:05+00:00"),
        ("not a time", "utc", "not a time"),
        (None, "utc", None),
        ("", "utc", None),
    ],
)
def test_a_time_is_iso_8601_with_its_offset(value, naive, expected):
    assert timefmt.timestamp(value, naive=naive) == expected


def test_a_zoneless_local_time_takes_this_machines_offset():
    stamped = timefmt.timestamp("2026.08.22D14:21:10", naive="local")
    expected = datetime(2026, 8, 22, 14, 21, 10).astimezone().isoformat()
    assert stamped == expected
    today = timefmt.timestamp("04:05:06")  # TorQ's summary clock
    assert today is not None and today[10:19] == "T04:05:06" and not _ZONELESS.fullmatch(today)


def test_a_row_is_typed_for_a_document_and_rendered_for_a_table():
    row = {"procname": "rdb1", "port": 6052, "up": True, "inputs": ["quote", "trade"],
           "cap": Shown(None, "no cap"), "labels": {"env": "prod"}, "pid": None}  # fmt: skip
    assert json_out.records([row]) == [
        {
            "process": "rdb1",
            "port": 6052,
            "up": True,
            "inputs": ["quote", "trade"],
            "cap": None,
            "labels": {"env": "prod"},
            "pid": None,
        }  # fmt: skip
    ]
    assert cells(row) == {
        "procname": "rdb1", "port": "6052", "up": "yes", "inputs": "quote, trade",
        "cap": "no cap", "labels": "env=prod", "pid": "",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (["process", "list"], "processes"),
        (["profile", "list"], "profiles"),
        (["runtime", "list"], "runtimes"),
        (["job", "list"], "jobs"),
        (["override", "list"], "overrides"),
    ],
)
def test_a_list_is_an_object_of_typed_records(argv, key):
    """`{kind: [...]}`, never a bare array; a number a number, a flag a bool,
    a set of names a list - never the text a table cell showed (#1882)."""
    result = CliRunner().invoke(cli.app, [*argv, "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert list(doc) == [key] and doc[key]
    for record in doc[key]:
        assert "procname" not in record, "a process is `process` in a document"
        for field, value in record.items():
            assert not (isinstance(value, str) and value.isdigit()), (field, value)
            assert value not in ("yes", "no", "NO", ""), (field, value)


def test_list_processes_carries_numbers_bools_and_lists():
    doc = json.loads(CliRunner().invoke(cli.app, ["process", "list", "--json"]).stdout)
    rdb = next(r for r in doc["processes"] if r["process"] == "rdb1")
    assert isinstance(rdb["port"], int) and isinstance(rdb["start_with_all"], bool)
    assert isinstance(rdb["inputs"], list) and isinstance(rdb["outputs"], list)


def test_an_older_releases_bare_process_list_is_refused_with_the_redeploy():
    """`--target` tunnels read the SERVER's `process list --json`, which is
    whatever release was deployed there. The #1882 document gives the ports;
    the bare `procname` list from before it is refused, not read under the
    old key (#2109)."""
    from uqs.deploy import control, tunnel
    from uqs.paths import UqsError

    remote = control.Remote("t", "h", "/srv/t")
    new = json.dumps({"processes": [{"process": "rdb1", "proctype": "rdb", "port": 6052}]})
    assert tunnel._parse_rows(remote, new) == [{"process": "rdb1", "proctype": "rdb", "port": 6052}]
    old = json.dumps([{"procname": "rdb1", "port": "6052"}])
    with pytest.raises(UqsError, match=r"target t: .*before #1882 \(`procname`, not `process`\)"):
        tunnel._parse_rows(remote, old)
    keyed = json.dumps({"processes": [{"procname": "rdb1", "port": 6052}]})
    with pytest.raises(UqsError, match="did not list its processes as JSON"):
        tunnel._parse_rows(remote, keyed)


def test_status_json_says_all_its_interpreter_line_says(monkeypatch):
    """With no q, the text says `not runnable (no q found)`; the document used
    to say only `{binary: null, actual: null, problem: null}`, and never gave
    the connection budget (#1882)."""
    from uqs.cli import summary
    from uqs.interpreter import InterpreterStatus

    none = summary._interpreter(InterpreterStatus(None, None, None))
    assert none["runnable"] is False and none["connection_limit"] is None
    assert none["summary"] == "interpreter: not runnable (no q found)"
    monkeypatch.setattr(summary.profiles, "licence_limit", lambda: 16)
    kdbx = summary._interpreter(InterpreterStatus(Path("/opt/q"), "kdbx", None))
    assert kdbx["runnable"] is True and kdbx["connection_limit"] == 16
    assert kdbx["summary"].endswith("16 connections")


#: A document's keys are snake_case (#1888): `target list` printed "as" and
#: "last deployed". Under these keys the keys are data - a table's or a
#: process's name - not the document's own.
DATA_KEYS = {"rows", "sample", "result", "busy", "fields", "facts_by", "missing_tables",
             "missing_columns", "types", "renames", "tables_by"}  # fmt: skip
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


def _bad_keys(doc, path="") -> list[str]:
    if isinstance(doc, list):
        return [b for i, v in enumerate(doc) for b in _bad_keys(v, f"{path}[{i}]")]
    if not isinstance(doc, dict):
        return []
    bad = [f"{path}.{k}" for k in doc if not _KEY.match(str(k))]
    return bad + [
        b for k, v in doc.items() if k not in DATA_KEYS for b in _bad_keys(v, f"{path}.{k}")
    ]


#: A spelling a document used before #2122 settled it, at any depth, and the one it
#: uses now. KEYS' are renamed by `emit`; `stack.id` is a stack's base port.
RETIRED = {**json_out.KEYS, "half_open": "range_from/range_to"}


def _breaches(doc, path="$") -> list[str]:
    """Every place below `doc` where the #1882 key contract does not hold: a
    retired spelling, an `ok` that is not a bool, a range that is an object, a
    stack whose base port is `id`. Every key, at every depth: the contract is
    the document's, not only its top level's (#2122)."""
    if isinstance(doc, list):
        return [b for i, v in enumerate(doc) for b in _breaches(v, f"{path}[{i}]")]
    if not isinstance(doc, dict):
        return []
    bad = [f"{path}.{k}: say {RETIRED[k]}" for k in doc if k in RETIRED]
    if "ok" in doc and not isinstance(doc["ok"], bool):
        bad.append(f"{path}.ok = {doc['ok']!r}: a bool")
    if isinstance(doc.get("range"), dict):
        bad.append(f"{path}.range: say range_from/range_to")
    if isinstance(doc.get("stack"), dict) and "id" in doc["stack"]:
        bad.append(f"{path}.stack.id: say base_port")
    return bad + [b for k, v in doc.items() for b in _breaches(v, f"{path}.{k}")]


@pytest.mark.parametrize("command", sorted(CASES))
def test_every_json_key_is_snake_case(tmp_path, command):
    argv, _ = CASES[command]
    run = _uqs(tmp_path, *argv, "--json")
    doc = json.loads(run.stdout)
    assert not _bad_keys(doc)
    assert not _breaches(doc)


# ------------------------------------------- the contract below the top level (#2122)


def _emitted(capsys, document) -> dict:
    json_out.emit(document)
    return json.loads(capsys.readouterr().out)


def test_emit_spells_every_key_at_every_depth(capsys):
    """`data alter`'s readers said `procname` two levels down, `process list`
    said `startwithall`, `job backfill` `worker` and its destination `stack_id`:
    one concept, one key, wherever in the document it sits."""
    doc = _emitted(capsys, {
        "ok": True,
        "result": {"readers": [{"procname": "hdb1", "ok": False, "outcome": "skipped"}]},
        "processes": [{"procname": "rdb1", "startwithall": True}],
        "job": "markout", "plan": {"worker": "w", "destination": {"stack_id": 6000}},
    })  # fmt: skip
    assert doc["result"]["readers"] == [{"process": "hdb1", "ok": False, "outcome": "skipped"}]
    assert doc["processes"] == [{"process": "rdb1", "start_with_all": True}]
    assert doc["plan"] == {"job": "w", "destination": {"base_port": 6000}}
    assert not _breaches(doc)


def test_verbatim_data_keeps_its_own_keys(capsys):
    """A query's result is the table's: a column called `procname` is the
    column's name, not a key the document spells."""
    rows = [{"procname": "rdb1", "ok": "yes", "range": {"from": 1}}]
    doc = _emitted(capsys, {"expression": "x", "result": json_out.Verbatim(rows)})
    assert doc["result"] == rows


@pytest.mark.parametrize(
    ("document", "said"),
    [
        ({"result": {"readers": [{"ok": "skipped"}]}}, "`ok` is a --json document's success flag"),
        ({"ok": True, "plan": {"range": {"from": "a", "to": "b"}}}, "range_from"),
        ({"rows": [{"procname": "a", "process": "b"}]}, "both 'procname' and 'process'"),
        ({"ok": False, "result": {"exit": 1}}, "is `exit_code`, not `exit` (#2228)"),
    ],
)
def test_emit_refuses_a_document_that_breaks_the_contract_below_the_top(document, said):
    """A string `ok` is truthy whatever it says - `jq 'select(.ok)'` picked
    every reader, failed ones included - and a range has one shape: the tree's
    bug, refused, rather than printed for a script to misread."""
    with pytest.raises(ValueError, match=re.escape(said)):
        json_out.emit(document)


def test_a_reader_row_types_its_ok_and_says_its_outcome_apart():
    from uqs.stack.hdb_alter import READER_OUTCOMES, reader_row

    rows = {o: reader_row("hdb1", o, "") for o in READER_OUTCOMES}
    assert [o for o, r in rows.items() if r["ok"]] == ["reloaded"]
    assert all(r["ok"] is False for o, r in rows.items() if o != "reloaded")
    assert rows["skipped"] == {"process": "hdb1", "ok": False, "outcome": "skipped", "detail": ""}
    assert not _breaches({"ok": True, "result": {"readers": list(rows.values())}})


def test_attachment_json_writes_the_documents_keys_and_reads_an_older_files(tmp_path):
    """attachment.json is persisted, and `job attachment show --json` prints it:
    a new file says `stack.base_port` and `publisher.process`, and one written
    before #2122 (`stack.id`, `publisher.procname`) still loads, the same."""
    from uqs.attach import config
    from uqs.stack import attach_state

    att = config.Attachment(
        name="a2122",
        stack=config.Stack(id=6000, host="localhost", discovery_host="localhost",
                           discovery_port=6001),
        publisher=config.Publisher(job="markout", procname="markout1"),
        tables=(),
    )  # fmt: skip
    new = config.to_json(att)
    assert new["stack"]["base_port"] == 6000 and "id" not in new["stack"]
    assert new["publisher"]["process"] == "markout1" and "procname" not in new["publisher"]
    assert not _breaches(new)
    old = json.loads(json.dumps(new))
    old["stack"]["id"] = old["stack"].pop("base_port")
    old["publisher"]["procname"] = old["publisher"].pop("process")
    assert config.from_json(old) == config.from_json(new) == att
    for cfg in (old, new):
        assert attach_state.stored_base_port(cfg) == 6000
        assert attach_state.stored_publisher(cfg) == "markout1"


def test_an_override_record_written_before_2122_reads_as_one_written_now(tmp_path):
    """backfill_overrides.jsonl is persisted and `attachment show` prints it: an
    entry that says `worker`, `range: {from, to}` and `stack_id` reads with the
    keys every document uses."""
    from uqs.attach import backfill_audit

    log = tmp_path / backfill_audit.OVERRIDES_LOG
    old = {"event": "override", "worker": "w", "destination": {"stack_id": 6000},
           "range": {"from": "a", "to": "b", "half_open": "[from, to)"}}  # fmt: skip
    log.write_text(json.dumps(old) + "\n")
    [entry] = backfill_audit.read(log)
    assert entry == {"event": "override", "job": "w", "destination": {"base_port": 6000},
                     "range_from": "a", "range_to": "b"}  # fmt: skip
    assert not _breaches(entry)
