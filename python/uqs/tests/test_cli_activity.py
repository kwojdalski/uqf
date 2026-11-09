"""`uqs activity list` and `uqs activity export` (#2000): the frontend's
history, read from its file with no API running.

The file is written by the frontend's own store, so these read what the API
would have written - and the export must be byte for byte what
`GET /activity/export` serves under the same filters.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqf_frontend import activity
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqs import activity_export, activity_log
from uqs.cli.shared import app

RID = "req-0123456789"
SESSION = "session-abc12345"


def _event(ts: str, kind: str, action: str, **over: object) -> dict:
    return {
        "event_id": activity.new_id(),
        "session_id": SESSION,
        "time": ts,
        "kind": kind,
        "action": action,
        "trigger": "pointer",
        **over,
    }


@pytest.fixture
def history(tmp_path) -> tuple[Path, TestClient]:
    """A history the API wrote: a click, a stop's submit and confirm, and a
    debug request; the server's audit row for the stop."""
    db = tmp_path / "activity.sqlite3"
    client = TestClient(create_app(gateway=FakeGateway(), settings=Settings(activity_db=db)))
    client.post(
        "/activity/events",
        json={
            "events": [
                _event("2026-11-03T10:00:00Z", "interact", "ui.click", subject="nav.Control"),
                _event("2026-11-03T10:00:01Z", "submit", "process.stop", subject="rdb1",
                       request_id=RID),
                _event("2026-11-03T10:00:02Z", "confirm", "process.stop", subject="rdb1",
                       request_id=RID),
                _event("2026-11-03T10:00:04Z", "debug", "request.finish",
                       subject="GET /ops/queue", outcome="succeeded", duration_ms=3.0),
            ],
            "drops": [{"level": "debug", "reason": "sampled", "count": 5}],
        },
    )  # fmt: skip
    store: activity.ActivityStore = client.app.state.activity  # ty: ignore[unresolved-attribute]
    store.add(
        source="server",
        kind="request",
        action="process.stop",
        time=dt.datetime(2026, 11, 3, 10, 0, 3, tzinfo=dt.UTC),
        subject="rdb1",
        outcome="refused",
        request_id=RID,
        method="POST",
        status_code=403,
    )
    return db, client


def _run(*argv: str) -> str:
    result = CliRunner().invoke(app, list(argv))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_list_reads_the_file_newest_first_as_json(history):
    db, _ = history
    doc = json.loads(_run("activity", "list", "--db", str(db), "--json"))
    assert doc["db"] == str(db)
    assert [e["action"] for e in doc["events"]][:2] == ["request.finish", "process.stop"]
    # #2241: an event says when under `time`, as every log line's record does - never `ts`
    assert all("time" in e and "ts" not in e and "level" in e for e in doc["events"])
    assert doc["events"][0]["time"].startswith("2026-11-03T10:00:04")
    assert doc["drops"] == [
        {"level": "debug", "reason": "sampled", "count": 5, "last_at": doc["drops"][0]["last_at"]}
    ]


@pytest.mark.parametrize(
    ("argv", "kinds"),
    [
        (["--level", "actions"], {"submit", "confirm", "request"}),
        (["--level", "interactions"], {"interact", "submit", "confirm", "request"}),
        (["--recorded-by", "server"], {"request"}),
        (["--request", RID], {"submit", "confirm", "request"}),
        (["--action", "ui"], {"interact"}),
        (["--subject", "nav.Control"], {"interact"}),
        (["--outcome", "refused"], {"request"}),
        (["--session", SESSION, "--level", "actions"], {"submit", "confirm"}),
        (["--from", "2026-11-03T10:00:01Z", "--to", "2026-11-03T10:00:03Z"], {"submit", "confirm"}),
    ],
)
def test_list_takes_the_views_filters(history, argv, kinds):
    db, _ = history
    doc = json.loads(_run("activity", "list", "--db", str(db), "--json", *argv))
    assert {e["kind"] for e in doc["events"]} == kinds


@pytest.mark.parametrize("fmt", activity_log.EXPORT_FORMATS)
@pytest.mark.parametrize(
    ("argv", "params"),
    [
        ([], {}),
        (["--level", "interactions"], {"level": "interactions"}),
        (["--recorded-by", "browser"], {"source": "browser"}),
        (["--request", RID], {"request_id": RID}),
    ],
)
def test_export_is_what_the_api_serves(history, fmt, argv, params):
    """The same filters, the same document - but for the export's own time."""
    db, client = history
    cli = _run("activity", "export", "--db", str(db), "--format", fmt, *argv)
    api = client.get("/activity/export", params={**params, "format": fmt}).text
    if fmt == "text":
        cli, api = (t.split("\n", 1)[1] for t in (cli, api))  # the stamped first line
    assert cli == api


@pytest.mark.parametrize("fmt", activity_log.EXPORT_FORMATS)
def test_an_export_cut_at_its_limit_is_what_the_api_serves(history, fmt):
    """#2527: the CLI's export says it was cut, as the API's does."""
    db, client = history
    result = CliRunner().invoke(
        app, ["activity", "export", "--db", str(db), "--format", fmt, "--limit", "2"]
    )
    assert result.exit_code == 0, result.output
    api = client.get("/activity/export", params={"format": fmt, "limit": 2}).text
    cli = result.stdout
    if fmt == "text":
        cli, api = (t.split("\n", 1)[1] for t in (cli, api))
        assert activity_export.TRUNCATED_NOTE in cli
    else:
        assert activity_export.TRUNCATED_NOTE in result.stderr
    assert cli == api


def test_export_writes_a_file_and_reads_back(history, tmp_path):
    db, _ = history
    out = tmp_path / "out.csv"
    _run("activity", "export", "--db", str(db), "--format", "csv", "-o", str(out))
    rows = list(csv.DictReader(io.StringIO(out.read_text())))
    assert [r["action"] for r in rows][0] == "ui.click", "oldest first"
    assert {r["level"] for r in rows} == {"actions", "interactions", "debug"}


def test_the_text_export_groups_the_stop_under_its_request(history):
    db, _ = history
    text = _run("activity", "export", "--db", str(db), "--request", RID)
    lines = text.splitlines()
    assert lines[3] == "# not in the history (counted, not shown): debug sampled 5"
    assert lines[4] == f"# {activity_log.RENAMED_DETAIL_NOTE}"
    assert lines[5] == f"request {RID[:8]} · process.stop · refused"
    assert [line.split(" · ")[3] for line in lines[6:]] == [
        "browser submit process.stop",
        "browser confirm process.stop",
        "server POST process.stop",
    ]


def test_the_file_is_opened_read_only(history):
    db, _ = history
    with activity_log.open_readonly(db) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM activity")


def test_a_missing_file_is_a_refusal_naming_what_writes_it(tmp_path):
    """What the refusal says is test_json_stdout.py's to read, in its --json
    document: the log goes to the real stderr, past this runner."""
    result = CliRunner().invoke(app, ["activity", "export", "--db", str(tmp_path / "none")])
    assert result.exit_code == 1
    assert result.stdout == "", "nothing is exported from a file that is not there"


def test_the_file_defaults_to_the_variable_then_the_stack(monkeypatch, tmp_path):
    from uqs import paths
    from uqs.cli import activity as cli_activity

    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_DB", str(tmp_path / "named.sqlite3"))
    assert cli_activity._db(None) == tmp_path / "named.sqlite3"
    monkeypatch.delenv("UQF_FRONTEND_ACTIVITY_DB")
    expected = activity_log.history_file(paths.default_paths().torqdata)
    assert cli_activity._db(None) == expected
    assert cli_activity._db(tmp_path / "x") == tmp_path / "x"


def test_level_and_format_offer_the_one_tuple_each():
    """The values --level and --format take and complete are activity_log's."""
    import typer.main

    group = typer.main.get_command(app).commands["activity"]  # ty: ignore[unresolved-attribute]
    for command, option, values in (
        ("list", "level", activity_log.EVENT_LEVELS),
        ("export", "level", activity_log.EVENT_LEVELS),
        ("export", "fmt", activity_log.EXPORT_FORMATS),
    ):
        (param,) = [p for p in group.commands[command].params if p.name == option]
        assert tuple(param.type.choices) == values


#: A filter value that, written raw into the header, ends the comment line and
#: forges an audited event after it (#2024).
_FORGED = (
    "view\n2026-11-04T09:00:00.000Z · deadbeef · actions · server PUT process.stop"
    " · all · succeeded · 3 ms\r\x1b[2J x"
)


@pytest.mark.parametrize(
    ("argv", "params"),
    [
        (["--action", _FORGED], {"action": _FORGED}),
        (["--subject", _FORGED], {"subject": _FORGED}),
        (["--session", _FORGED], {"session_id": _FORGED}),
        (["--request", _FORGED], {"request_id": _FORGED}),
    ],
)
def test_a_filter_value_can_never_start_a_line_of_the_text_export(history, argv, params):
    """No event matches, so every line is a header line - and the value is
    still shown, escaped, rather than dropped."""
    db, client = history
    cli = _run("activity", "export", "--db", str(db), "--format", "text", *argv)
    api = client.get("/activity/export", params={**params, "format": "text"}).text
    for text in (cli, api):
        lines = text.split("\n")
        assert lines[-1] == "", "the export ends with a newline"
        assert all(line.startswith("# ") for line in lines[:-1]), text
        for forged in ("\r", "\x1b", " "):
            assert forged not in text
        assert "process.stop" in lines[1], "the value is shown, escaped"


def test_list_prints_a_path_holding_markup_literally(history, tmp_path):
    """The file named by --db is printed as written, not read as rich
    markup (#2025)."""
    db, _ = history
    odd = tmp_path / "logs[" / "x]" / "activity.sqlite3"
    odd.parent.mkdir(parents=True)
    with sqlite3.connect(db) as src, sqlite3.connect(odd) as dst:
        src.backup(dst)  # the WAL too, which a file copy would miss
    out = _run("activity", "list", "--db", str(odd), "--outcome", "accepted")
    assert "x]" in " ".join(out.split()), out
