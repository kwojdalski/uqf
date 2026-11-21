"""`uqs job preview`: the q it runs, and what it prints of the answer.

The preview itself - nothing published, no cursor written - is held in q by
tests/q/test_stream_poll.q. These hold the Python half against a fake q: the
script it sends, the result line it reads, and the exit code.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli, timerange
from uqs.interpreter import q_interpreter
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
    "rows": {"wide_orderbook": 3},
    # q at `\P 0` sends every digit: a rate 3e-8 from its neighbour stays itself
    "sample": {"wide_orderbook": [{"sym": "EURUSD", "mid": 1.26310001}]},
    "failures": [],
}


@pytest.fixture(autouse=True)
def _declared(monkeypatch):
    """The feed these cases preview, declared - checked before q is asked (#1885)."""
    monkeypatch.setattr(stream_preview, "streaming_jobs", lambda _paths: {"vectorize2"})


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


def test_a_job_the_tree_does_not_declare_is_refused_before_q(monkeypatch):
    """q's own refusal blamed "a wiring bug rather than a lookup miss" (#1885)."""
    monkeypatch.setattr(stream_preview, "q_interpreter", lambda: pytest.fail("asked q"))
    with pytest.raises(UqsError, match="no streaming job 'nosuchjob' - `uqs job list` names"):
        stream_preview.preview(default_paths(), "nosuchjob")


def test_the_help_s_example_names_a_streaming_job_the_tree_declares():
    """It named the hypothetical vectorize2 from docs/scaffolding/feed.md (#2617)."""
    from uqs.cli.job import stream
    from uqs.model.jobs import job_rows

    (job,) = re.findall(r"uqs job preview (\w+)", stream.preview.__doc__ or "")
    # job_rows itself: an autouse fixture here stubs streaming_jobs
    kinds = {r["job"]: r["kind"] for r in job_rows(default_paths().repo_root)}
    assert kinds.get(job) not in (None, *stream_preview._NOT_STREAMING)


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
    monkeypatch.setattr(
        stream_preview, "preview", lambda paths, job, sample, trace=False, window=None: answer
    )
    return runner.invoke(cli.app, ["job", "preview", "vectorize2"], env={"COLUMNS": "200"})


def test_the_command_prints_the_page(monkeypatch):
    result = invoke(monkeypatch, PAGE)
    assert result.exit_code == 0, result.output
    for said in ("nothing published", "fixture", "advances", "wide_orderbook: 3 row(s)", "EURUSD"):
        assert said in result.output


def test_an_idle_feed_says_so_and_succeeds(monkeypatch):
    result = invoke(monkeypatch, {**PAGE, "state": "idle", "fetched": 0, "rows": {}, "sample": {}})
    assert result.exit_code == 0
    assert "nothing after the cursor" in result.output


def test_invalid_output_fails_the_command_and_says_why(monkeypatch):
    bad = {**PAGE, "state": "invalid", "failures": ['wide_orderbook has columns sym typed "s"']}
    result = invoke(monkeypatch, bad)
    assert result.exit_code == 1
    assert "wide_orderbook has columns sym" in result.output


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
    '2026.10.05D12:00:00.000000000|TRACE|ipc|query sent call="{[c] select from t where '
    'time>c}" transport=`ipc request=1 args=,2026.09.13D00:00:00.000000000 job=`vectorize2'
)
RETURNED = "2026.10.05D12:00:00.100000000|TRACE|ipc|query returned transport=`ipc request=1 rows=3"
FAILED = '2026.10.05D12:00:00.100000000|TRACE|ipc|query failed request=1 error="source down"'


def test_tracing_is_switched_on_only_when_asked(monkeypatch):
    sent = fake_q(monkeypatch, PAGE)
    stream_preview.preview(default_paths(), "vectorize2")
    assert ".qetl.log.trace" not in sent["script"], "a plain preview is unchanged"
    stream_preview.preview(default_paths(), "vectorize2", trace=True)
    assert ".qetl.log.trace 1b;" in sent["script"]
    assert ".qetl.log.debug 1b;" in sent["script"], "DEBUG with it, as backfill --trace does"


def test_the_traced_lines_come_back_with_the_answer(monkeypatch):
    out = f"loading...\n{SENT}\n{RETURNED}\nUQS_PREVIEW {json.dumps(PAGE)}\n"
    fake_q(monkeypatch, PAGE, stdout=out)
    r = stream_preview.preview(default_paths(), "vectorize2", trace=True)
    assert [(lv, src) for lv, src, _ in r["trace"]] == [("TRACE", "ipc"), ("TRACE", "ipc")]
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
    monkeypatch.setattr(
        stream_preview, "preview", lambda paths, job, sample, trace=False, window=None: traced
    )
    result = runner.invoke(
        cli.app, ["job", "preview", "vectorize2", "--trace"], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.output
    assert "    {[c] select from t where time>c}" in result.output, "the query as a block"
    assert "query returned" in result.output


def test_the_command_prints_the_trace_of_a_failed_preview(monkeypatch):
    def fail(paths, job, sample, trace=False, window=None):
        raise stream_preview.PreviewFailed(
            "preview vectorize2: source down", stream_preview.trace_lines(f"{SENT}\n{FAILED}\n")
        )

    monkeypatch.setattr(stream_preview, "preview", fail)
    result = runner.invoke(cli.app, ["job", "preview", "vectorize2", "--trace"])
    assert result.exit_code == 1
    assert "query failed" in result.output and "source down" in result.output


# --- a recent-data window (#681) ---------------------------------------------

RECENT = {
    **PAGE,
    "mode": "recent",
    "cursor": "2026-10-05T10:00:00.000000000",
    "window": {
        "from": "2026-10-05T10:00:00.000000001",
        "to": "2026-10-05T10:00:30.000000001",
        "start_cursor": "2026-10-05T10:00:00.000000000",
        "end_cursor": "2026-10-05T10:00:30.000000000",
    },
    "kept": 3,
    "page_limit": 10000,
    "limited": False,
}


def test_without_last_the_script_previews_the_next_page(monkeypatch):
    sent = fake_q(monkeypatch, PAGE)
    stream_preview.preview(default_paths(), "vectorize2", 1)
    assert ".qetl.job.stream.preview[`vectorize2;1]" in sent["script"]
    assert "preview_recent" not in sent["script"]


def test_last_asks_q_for_the_recent_window(monkeypatch):
    sent = fake_q(monkeypatch, RECENT)
    window = timerange.resolve(None, None, last="30s")
    stream_preview.preview(default_paths(), "vectorize2", 1, window=window)
    assert ".qetl.job.stream.preview_recent[`vectorize2;1;`timespan$30000000000]" in sent["script"]


def test_a_window_given_outright_asks_q_for_that_window(monkeypatch):
    sent = fake_q(monkeypatch, {**RECENT, "mode": "window"})
    window = timerange.resolve(None, None, day="2026-10-05")
    stream_preview.preview(default_paths(), "vectorize2", 1, window=window)
    assert (
        ".qetl.job.stream.preview_window[`vectorize2;1;"
        "2026.10.05D00:00:00.000000000;2026.10.06D00:00:00.000000000]"
    ) in sent["script"]


def test_the_command_hands_its_range_to_the_preview(monkeypatch):
    seen: dict = {}

    def fake(paths, job, sample, trace=False, window=None):
        seen["window"] = window
        return {**RECENT, "mode": "window"}

    monkeypatch.setattr(stream_preview, "preview", fake)
    argv = [
        "job",
        "preview",
        "vectorize2",
        "--from",
        "2026-10-05T10:00",
        "--to",
        "2026-10-05T11:00",
    ]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert seen["window"] == timerange.resolve("2026-10-05T10:00", "2026-10-05T11:00")
    assert "sample of vectorize2" in " ".join(result.output.split())


def test_the_command_says_a_recent_sample_is_not_the_next_page(monkeypatch):
    seen: dict = {}

    def fake(paths, job, sample, trace=False, window=None):
        seen["last"] = window and window.last
        return RECENT

    monkeypatch.setattr(stream_preview, "preview", fake)
    result = runner.invoke(
        cli.app, ["job", "preview", "vectorize2", "--last", "30s", "--limit", "1"]
    )
    assert result.exit_code == 0, result.output
    assert seen["last"] == timedelta(seconds=30)
    out = " ".join(result.output.split())
    assert "recent-data sample of vectorize2" in out
    assert "not the running job's next page" in out
    assert "saved cursor untouched" in out
    assert "[2026-10-05T10:00:00.000000001, 2026-10-05T10:00:30.000000001) UTC" in out
    assert "fetched 3 row(s), 3 in the window, page limit 10000" in out


def test_a_full_page_is_flagged(monkeypatch):
    monkeypatch.setattr(
        stream_preview,
        "preview",
        lambda paths, job, sample, trace=False, window=None: {**RECENT, "limited": True},
    )
    result = runner.invoke(cli.app, ["job", "preview", "vectorize2", "--last", "30s"])
    assert "the page was full" in result.output


def test_a_bad_last_is_refused_before_q(monkeypatch):
    started: list = []
    monkeypatch.setattr(stream_preview, "preview", lambda *a, **kw: started.append(1) or RECENT)
    result = runner.invoke(cli.app, ["job", "preview", "vectorize2", "--last", "0s"])
    assert result.exit_code == 2, "a usage error (#1885)"
    assert started == []


def test_an_empty_window_says_so(monkeypatch):
    monkeypatch.setattr(
        stream_preview,
        "preview",
        lambda paths, job, sample, trace=False, window=None: {
            **RECENT,
            "state": "idle",
            "kept": 0,
        },
    )
    result = runner.invoke(cli.app, ["job", "preview", "vectorize2", "--last", "30s"])
    assert result.exit_code == 0
    assert "nothing in the window" in result.output


needs_q = pytest.mark.skipif(q_interpreter() is None, reason="no q interpreter")


def _real_page(monkeypatch, sample: str) -> dict:
    """The preview's own script run by a real q, with the job's page replaced
    by `sample` - a q dict of table name to rows - so what is tested is the
    crossing: what the child prints and what Python reads of it."""
    page = f"`job`state`sample!(`vectorize2;`previewed;enlist[`t]!enlist {sample})"
    monkeypatch.setattr(stream_preview, "_call", lambda *a: page)
    return stream_preview.preview(default_paths(), "vectorize2")


@needs_q
def test_a_sampled_float_reaches_python_with_every_digit(monkeypatch):
    """q prints a float at 7 significant digits unless told otherwise, so the
    preview showed a notional of 12345678.9 as 12345680.0 and two rates 3e-8
    apart as one (#2875)."""
    page = _real_page(monkeypatch, "([] notional:12345678.9 1.0; rate:1.26310001 1.26310004)")
    rows = page["sample"]["t"]
    assert [r["notional"] for r in rows] == [12345678.9, 1.0]
    assert [r["rate"] for r in rows] == [1.26310001, 1.26310004]


@needs_q
def test_a_sampled_float_infinity_is_read_not_a_traceback(monkeypatch):
    """`.j.j` writes 0w as the bare token `inf`, which is not JSON: one
    infinite quote in the sample made the whole preview a JSONDecodeError
    (#2876). It reads back as an infinity, and a null as None."""
    page = _real_page(monkeypatch, '([] bid:1.5 0w 0n; ask:-0w 2.5 3.5; note:("inf";"x";"-inf"))')
    rows = page["sample"]["t"]
    assert [r["bid"] for r in rows] == [1.5, math.inf, None]
    assert [r["ask"] for r in rows] == [-math.inf, 2.5, 3.5]
    assert [r["note"] for r in rows] == ["inf", "x", "-inf"], "text that spells inf is still text"
