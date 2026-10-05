"""`uqs stream preview`: the q it runs, and what it prints of the answer.

The preview itself - nothing published, no cursor written - is held in q by
tests/q/test_stream_poll.q. These hold the Python half against a fake q: the
script it sends, the result line it reads, and the exit code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.paths import UqsError, default_paths
from uqs.stack import stream_preview

runner = CliRunner()

PAGE = {
    "job": "vectorize2",
    "state": "previewed",
    "live": "fixture",
    "cursor": "2026-09-13T00:00:00.000000000",
    "fetched": 3,
    "next_cursor": "2026-09-14T00:00:00.000000000",
    "advances": True,
    "rows": {"wide_book": 3},
    "sample": {"wide_book": [{"sym": "EURUSD", "mid": 1.08}]},
    "failures": [],
}


def fake_q(monkeypatch, answer: dict | None, *, stdout: str | None = None, returncode: int = 0):
    """Stand in for q; record the script it was given."""
    sent: dict = {}
    monkeypatch.setattr(stream_preview, "q_interpreter", lambda: Path("/usr/bin/q"))

    def run(cmd, **kw):
        sent["script"] = Path(cmd[1]).read_text()
        sent["env"] = kw["env"]
        out = stdout if stdout is not None else f"loading...\nUQS_PREVIEW {json.dumps(answer)}\n"
        return subprocess.CompletedProcess(cmd, returncode, stdout=out, stderr="")

    monkeypatch.setattr(stream_preview.subprocess, "run", run)
    return sent


def test_the_script_previews_the_job_once(monkeypatch):
    sent = fake_q(monkeypatch, PAGE)
    stream_preview.preview(default_paths(), "vectorize2", 7)
    assert ".qetl.job.stream.preview[`vectorize2;7]" in sent["script"]
    assert "UQF_STATUS_DIR" in sent["env"], "it reads the cursor the running feed writes"


def test_the_answer_is_the_marked_line(monkeypatch):
    fake_q(monkeypatch, PAGE)
    assert stream_preview.preview(default_paths(), "vectorize2") == PAGE


def test_a_name_that_is_not_a_job_is_refused_before_q():
    with pytest.raises(UqsError, match="not a streaming job name"):
        stream_preview.preview(default_paths(), "x;exit 1")


def test_a_q_side_refusal_is_raised_with_its_message(monkeypatch):
    fake_q(monkeypatch, {"error": "preview: markout subscribes to trades, quote"})
    with pytest.raises(UqsError, match="preview vectorize2: preview: markout subscribes"):
        stream_preview.preview(default_paths(), "vectorize2")


def test_a_q_that_never_answers_is_an_error(monkeypatch):
    fake_q(monkeypatch, None, stdout="'src/etl/init.q: some load error\n", returncode=0)
    with pytest.raises(UqsError, match="did not run"):
        stream_preview.preview(default_paths(), "vectorize2")


def test_no_q_is_said_plainly(monkeypatch):
    monkeypatch.setattr(stream_preview, "q_interpreter", lambda: None)
    with pytest.raises(UqsError, match="no q interpreter"):
        stream_preview.preview(default_paths(), "vectorize2")


def invoke(monkeypatch, answer: dict):
    monkeypatch.setattr(stream_preview, "preview", lambda paths, job, sample, trace=False: answer)
    return runner.invoke(cli.app, ["stream", "preview", "vectorize2"], env={"COLUMNS": "200"})


def test_the_command_prints_the_page(monkeypatch):
    result = invoke(monkeypatch, PAGE)
    assert result.exit_code == 0, result.output
    for said in ("nothing published", "fixture", "advances", "wide_book: 3 row(s)", "EURUSD"):
        assert said in result.output


def test_an_idle_feed_says_so_and_succeeds(monkeypatch):
    result = invoke(monkeypatch, {**PAGE, "state": "idle", "fetched": 0, "rows": {}, "sample": {}})
    assert result.exit_code == 0
    assert "nothing after the cursor" in result.output


def test_invalid_output_fails_the_command_and_says_why(monkeypatch):
    bad = {**PAGE, "state": "invalid", "failures": ['wide_book has columns sym typed "s"']}
    result = invoke(monkeypatch, bad)
    assert result.exit_code == 1
    assert "wide_book has columns sym" in result.output


def test_a_compound_cursor_prints_as_its_fields(monkeypatch):
    compound = {
        **PAGE,
        "cursor": {"time": "2026-09-11T00:00:00.000000000", "securityId": "a"},
        "next_cursor": {"time": "2026-09-11T00:00:00.000000000", "securityId": "b"},
    }
    result = invoke(monkeypatch, compound)
    assert "securityId=a" in result.output
    assert "securityId=b" in result.output


# --- --trace --------------------------------------------------------------

#: What .qetl.source.ipc_call logs outside TorQ, as log.q's emit writes it.
SENT = (
    '2026.10.05D12:00:00.000000000|TRC|ipc|query sent call="{[c] select from t where '
    'time>c}" transport=`ipc request=1 args=,2026.09.13D00:00:00.000000000 job=`vectorize2'
)
RETURNED = "2026.10.05D12:00:00.100000000|TRC|ipc|query returned transport=`ipc request=1 rows=3"
FAILED = '2026.10.05D12:00:00.100000000|TRC|ipc|query failed request=1 error="source down"'


def test_tracing_is_switched_on_only_when_asked(monkeypatch):
    sent = fake_q(monkeypatch, PAGE)
    stream_preview.preview(default_paths(), "vectorize2")
    assert ".qetl.log.trace" not in sent["script"], "a plain preview is unchanged"
    stream_preview.preview(default_paths(), "vectorize2", trace=True)
    assert ".qetl.log.trace 1b;" in sent["script"]
    assert ".qetl.log.debug 1b;" in sent["script"], "DBG with it, as backfill --trace does"


def test_the_traced_lines_come_back_with_the_answer(monkeypatch):
    out = f"loading...\n{SENT}\n{RETURNED}\nUQS_PREVIEW {json.dumps(PAGE)}\n"
    fake_q(monkeypatch, PAGE, stdout=out)
    r = stream_preview.preview(default_paths(), "vectorize2", trace=True)
    assert [(lv, src) for lv, src, _ in r["trace"]] == [("TRC", "ipc"), ("TRC", "ipc")]
    assert r["trace"][0][2].startswith('query sent call="{[c] select')


def test_a_failed_fetch_keeps_its_trace(monkeypatch):
    """The query that failed is what someone turned --trace on to see."""
    out = f"{SENT}\n{FAILED}\nUQS_PREVIEW {json.dumps({'error': 'source down'})}\n"
    fake_q(monkeypatch, None, stdout=out)
    with pytest.raises(stream_preview.PreviewFailed, match="source down") as raised:
        stream_preview.preview(default_paths(), "vectorize2", trace=True)
    assert [m.split(" ")[1] for _, _, m in raised.value.trace] == ["sent", "failed"]


def test_a_fetch_that_hangs_shows_the_query_it_hung_on(monkeypatch):
    monkeypatch.setattr(stream_preview, "q_interpreter", lambda: Path("/usr/bin/q"))

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"], output=f"loading...\n{SENT}\n")

    monkeypatch.setattr(stream_preview.subprocess, "run", run)
    with pytest.raises(stream_preview.PreviewFailed, match="never returned") as raised:
        stream_preview.preview(default_paths(), "vectorize2", timeout=5, trace=True)
    assert raised.value.trace[0][2].startswith("query sent")


def test_the_command_prints_a_traced_query_as_a_block(monkeypatch):
    traced = {**PAGE, "trace": stream_preview.trace_lines(f"{SENT}\n{RETURNED}\n")}
    monkeypatch.setattr(stream_preview, "preview", lambda paths, job, sample, trace=False: traced)
    result = runner.invoke(
        cli.app, ["stream", "preview", "vectorize2", "--trace"], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.output
    assert "    {[c] select from t where time>c}" in result.output, "the query as a block"
    assert "query returned" in result.output


def test_the_command_prints_the_trace_of_a_failed_preview(monkeypatch):
    def fail(paths, job, sample, trace=False):
        raise stream_preview.PreviewFailed(
            "preview vectorize2: source down", stream_preview.trace_lines(f"{SENT}\n{FAILED}\n")
        )

    monkeypatch.setattr(stream_preview, "preview", fail)
    result = runner.invoke(cli.app, ["stream", "preview", "vectorize2", "--trace"])
    assert result.exit_code == 1
    assert "query failed" in result.output and "source down" in result.output
