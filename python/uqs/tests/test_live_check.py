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
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, default_paths
from uqs.stack import live_check, redact

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
    """Two languages mask secrets; this holds them to one list."""
    text = (REPO / "src/etl/core/live_check.q").read_text()
    m = re.search(r"^secret_keys:(.*)$", text, re.M)
    assert m, "live_check.q declares secret_keys"
    assert m.group(1).strip().strip("`").split("`") == list(redact.SECRET_KEYS)


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
    assert all(c["env"]["UQS_REQUIRE_LIVE_SOURCES"] == "1" for c in calls)
    assert all(c["env"]["ODBCSYSINI"] == "/odbc/etc" for c in calls)
    assert live_check.passed(results)


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
    (r,) = live_check.check(default_paths(), ["hdb_transfer"], timeout=60, window_minutes=5)
    assert (r["status"], r["rows"]) == ("empty", 0), "nothing in the last five minutes"
