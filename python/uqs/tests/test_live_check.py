"""uqs.stack.live_check (#840): each source checked live, in a q of its own,
under one budget, with everything printed redacted.

Most of this replaces the q process with a fake, to drive the timeouts and
crashes a real one rarely produces on demand. The last test runs the real
thing end to end - q writes a small HDB and the `local` source hdb_transfer
is checked against it - and skips without q.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, default_paths
from uqs.stack import live_check, redact
from uqs.timerange import TimeRange

REPO = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _answer(**fields) -> str:
    base = {
        "source": "s1",
        "transport": "odbc",
        "status": "ok",
        "stage": "done",
        "rows": 3,
        "elapsed_ms": 5,
        "diagnostic": "",
    }
    return json.dumps({**base, **fields})


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(live_check, "q_interpreter", lambda env: Path("/bin/q"))


def _run(sources, runner, **kw):
    return live_check.check(
        default_paths(), sources, timeout=kw.pop("timeout", 30), runner=runner, **kw
    )


def test_the_q_and_python_secret_lists_are_the_same():
    """Two languages mask secrets; this holds them to one list - q's single
    .qetl.source.secret_keys, which the live check also redacts with (#2334)."""
    text = (REPO / "src/etl/core/source_credentials.q").read_text()
    m = re.search(r"^(?:\.[A-Za-z_][\w.]*\.)?secret_keys:(.*)$", text, re.M)
    assert m, "source_credentials.q declares secret_keys"
    assert re.findall(r'"([^"]*)"', m.group(1)) == list(redact.SECRET_KEYS)
    live = (REPO / "src/etl/core/live_check.q").read_text()
    assert not re.search(r"^\S*secret_keys:", live, re.M), "live_check.q keeps no list of its own"


def test_each_source_runs_in_its_own_q_with_live_sources_required(monkeypatch):
    calls = []

    def runner(argv, **kw):
        calls.append(kw)
        script = Path(argv[1]).read_text()
        m = re.search(r"check\[`(\w+);", script)
        assert m, script
        source = m.group(1)
        return subprocess.CompletedProcess(argv, 0, stdout=_answer(source=source) + "\n", stderr="")

    results = _run(["s1", "s2"], runner, odbc_env={"ODBCSYSINI": "/odbc/etc"})
    assert [r["source"] for r in results] == ["s1", "s2"]
    assert len(calls) == 2, "one process per source"
    assert all(c["env"]["UQF_LIVE_SOURCES_REQUIRED"] == "1" for c in calls)
    assert all(c["env"]["ODBCSYSINI"] == "/odbc/etc" for c in calls)
    assert live_check.passed(results)


def test_the_check_loads_only_the_source_s_own_declarations():
    """An unrelated job whose plant table this site lacks must not stop the
    check: the q selects the source's closure before the ETL tree loads."""
    script = live_check._script("duckdb_deals", TimeRange(None, None, timedelta(hours=1)), None)
    select = script.index(".qetl.load.only_sources:enlist `duckdb_deals;")
    assert script.index("\\l src/init.q") < select < script.index("\\l src/etl/init.q")


def test_the_window_reaches_q_as_a_span_or_as_its_bounds():
    """#2249: `--last` is a timespan back from q's own now; a past window,
    --from/--to or --date, its (start;end)."""
    assert live_check.q_window(TimeRange(None, None, timedelta(minutes=90))) == (
        "(`timespan$5400000000000j)"
    )
    day = TimeRange(datetime(2026, 9, 13, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC))
    assert live_check.q_window(day) == (
        "(2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000)"
    )
    assert ".qetl.livecheck.check[`deals;(2026.09.13D" in live_check._script("deals", day, None)


def test_an_empty_read_passes_and_a_failure_does_not():
    assert live_check.passed([{"status": "ok"}, {"status": "empty"}])
    assert not live_check.passed([{"status": "ok"}, {"status": "failed"}])


def test_a_diagnostic_is_redacted_again_on_the_way_out():
    def runner(argv, **kw):
        out = _answer(
            status="failed", stage="connect", diagnostic="login failed: PWD=hunter2;UID=me"
        )
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    (r,) = _run(["s1"], runner)
    assert "hunter2" not in r["diagnostic"] and "PWD=<redacted>" in r["diagnostic"]


@pytest.mark.parametrize(
    ("text", "secrets", "want"),
    [
        # the ordinary shapes, which already worked
        ("login failed: PWD=hunter2;UID=me", (), "login failed: PWD=<redacted>"),
        ("password: s3cret, again", (), "password=<redacted> again"),
        ("denied for h:5010:me:s3cret", ("s3cret",), "denied for h:5010:me:<redacted>"),
        # a known secret is replaced as the literal it is, glob characters and all
        ("denied for db:5010:app:Xk[9]q", ("Xk[9]q",), "denied for db:5010:app:<redacted>"),
        ("bad a?b and c*d", ("a?b", "c*d"), "bad <redacted> and <redacted>"),
        # an ODBC {}-quoted value is masked whole: ; and spaces are data inside
        # it, and }} is an escaped }
        ("login failed: PWD={s3;cret};UID=me", (), "login failed: PWD=<redacted>;UID=me"),
        ("login failed: PWD={s3 cret};UID=me", (), "login failed: PWD=<redacted>;UID=me"),
        ("login failed: PWD={a}} b};UID=me", (), "login failed: PWD=<redacted>;UID=me"),
        ("login failed: PWD={never closed", (), "login failed: PWD=<redacted>"),
        # every key sources.csv refuses inline is masked, KEY and ACCESSKEY too (#2334)
        ("login failed: KEY=fake;UID=me", (), "login failed: KEY=<redacted>"),
        ("login failed: ACCESSKEY=fake;UID=me", (), "login failed: ACCESSKEY=<redacted>"),
        # a prefixed snake_case key, a JSON key and a quoted value (#2516)
        ("db_password=hunter2", (), "db_password=<redacted>"),
        ("UQF_FRONTEND_DATA_PASSWORD=hunter2", (), "UQF_FRONTEND_DATA_PASSWORD=<redacted>"),
        ("export AWS_SECRET_ACCESS_KEY=abc123", (), "export AWS_SECRET_ACCESS_KEY=<redacted>"),
        ("SECRET_KEY=abc", (), "SECRET_KEY=<redacted>"),
        ('{"password": "hunter2"}', (), '{"password"=<redacted>}'),
        ('password = "hunter 2" then', (), "password=<redacted> then"),
        ("password='hunter 2' then", (), "password=<redacted> then"),
        # a key inside a longer word is not one
        ("monkey=1 keyboard=2", (), "monkey=1 keyboard=2"),
    ],
)
def test_redaction_masks_every_shape_of_secret(text, secrets, want):
    assert redact.redact(text, *secrets) == want


def test_a_source_that_overruns_is_a_timeout_and_the_rest_are_not_checked():
    clock = Clock()

    def runner(argv, **kw):
        clock.now += kw["timeout"]
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    results = _run(["slow", "next"], runner, timeout=10, clock=clock)
    assert [(r["source"], r["stage"]) for r in results] == [
        ("slow", "timeout"),
        ("next", "timeout"),
    ]
    assert "no answer within 10.0s" in results[0]["diagnostic"]
    assert "not checked: the 10s overall timeout ran out" in results[1]["diagnostic"]
    assert not live_check.passed(results)


def test_a_q_that_dies_without_a_result_is_a_process_failure_redacted():
    def runner(argv, **kw):
        return subprocess.CompletedProcess(
            argv, 139, stdout="", stderr="segfault in Token=abc123\n"
        )

    (r,) = _run(["s1"], runner)
    assert r["status"] == "failed" and r["stage"] == "process"
    assert "exited 139" in r["diagnostic"] and "abc123" not in r["diagnostic"]


def test_a_name_that_is_not_a_source_is_refused_before_q_runs():
    with pytest.raises(UqsError, match="is not a source name"):
        _run(["x;exit 0"], lambda *a, **k: pytest.fail("q must not run"))


def test_a_real_source_is_checked_end_to_end(tmp_path, monkeypatch):
    """q writes an HDB with one recent trade; the `local` source reads it."""
    monkeypatch.undo()
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    hdb = tmp_path / "hdb"
    write = tmp_path / "write.q"
    write.write_text(
        "t:([] time:enlist .z.p-0D00:10; trade_id:enlist 7; sym:enlist `EURUSD;"
        " price:enlist 1.08; size:enlist 1000000; side:enlist `buy);\n"
        f'{{(hsym `$"{hdb}/",string[.z.d],"/",string[x],"/") set .Q.en[hsym `$"{hdb}"] t}}'
        " each `trades`trades_copy;\nexit 0\n"
    )
    subprocess.run([str(q), str(write), "-q"], check=True, stdin=subprocess.DEVNULL, timeout=60)
    monkeypatch.setenv("UQF_SOURCE_CRED_HDB_TRANSFER", str(hdb))
    (r,) = live_check.check(default_paths(), ["hdb_transfer"], timeout=60)
    assert (r["status"], r["stage"], r["rows"]) == ("ok", "done", 1), r
    last5 = TimeRange(None, None, timedelta(minutes=5))
    (r,) = live_check.check(default_paths(), ["hdb_transfer"], timeout=60, window=last5)
    assert (r["status"], r["rows"]) == ("empty", 0), "nothing in the last five minutes"
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    past = TimeRange(today - timedelta(days=1), today)
    (r,) = live_check.check(default_paths(), ["hdb_transfer"], timeout=60, window=past)
    assert (r["status"], r["rows"]) == ("empty", 0), "nothing yesterday: the window is read"


# --- the shared time range (#2249) -------------------------------------------


@pytest.fixture
def window_given(monkeypatch):
    """The window `config sources check` hands the check, for an argv."""
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli import source_check

    seen: dict = {}
    monkeypatch.setattr(source_check.live_check, "check", lambda *a, **k: seen.update(k) or [])

    def run(*argv: str) -> TimeRange:
        result = CliRunner().invoke(cli.app, ["config", "sources", "check", "src1", *argv])
        assert result.exit_code == 0, result.output
        return seen["window"]

    return run


def test_with_no_range_the_check_reads_the_last_hour(window_given):
    assert window_given().last == timedelta(hours=1)


def test_the_check_takes_the_whole_shared_range(window_given):
    """A source quiet now - a weekend, out of hours - is checked over a window
    that had data, as every range-taking command names one."""
    day = (datetime(2026, 9, 13, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC))
    assert window_given("--date", "2026-09-13").bounds == day
    assert window_given("--from", "2026-09-13", "--to", "2026-09-14").bounds == day
    assert window_given("--last", "90m").last == timedelta(minutes=90)
