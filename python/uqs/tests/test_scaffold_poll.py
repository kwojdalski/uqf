"""`uqs job new --poll`: a feed scaffolded as polling steps (#668).

What the generated q says is checked here, as text and through the
registry's own reader; that it LOADS, registers, previews without publishing
and ticks is test_scaffold_loads.py's, under a real q.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.model.declarations import declaration_calls
from uqs.paths import STREAM_DIR, UqsError
from uqs.scaffold import jobs

runner = CliRunner()
COLUMNS = "sym:symbol, px:float"


def job_file(plan) -> str:
    return next(a.body for a in plan.actions if a.path == STREAM_DIR / f"{plan.name}.q")


def declared(plan) -> dict[str, str]:
    ((_, _, keys),) = declaration_calls(job_file(plan))
    return keys


def test_a_plain_feed_is_unchanged():
    keys = declared(jobs.streaming_job("tickfeed", [], "ticks", COLUMNS))
    assert "on_timer" in keys
    assert "poll" not in keys


def test_a_polling_feed_declares_poll_and_no_timer():
    keys = declared(jobs.streaming_job("ratefeed", [], "rates", COLUMNS, poll=True))
    assert keys["period"] == "0D00:00:01"
    assert "on_timer" not in keys, "the framework builds the timer from poll"
    assert keys["poll"].startswith("`fetch`normalize`next_cursor!(")
    for step in ("fetch", "normalize", "next_cursor"):
        assert f".qpipe.job.ratefeed.{step}" in keys["poll"]


def test_the_steps_take_the_polling_contracts_arguments():
    text = job_file(jobs.streaming_job("ratefeed", [], "rates", COLUMNS, poll=True))
    assert "fetch:{[cursor]" in text
    assert "normalize:{[page]" in text
    assert "next_cursor:{[page]" in text


def test_the_steps_that_read_the_source_throw_until_written():
    text = job_file(jobs.streaming_job("ratefeed", [], "rates", COLUMNS, poll=True))
    for step in ("fetch", "normalize", "next_cursor"):
        assert f"ratefeed.{step}: not implemented" in text
    fetch = text.split("fetch:{[cursor]")[1].split("}")[0]
    assert "fixture" not in fetch, "no fixture stands in for the source"


def test_a_period_is_carried_into_the_poll_declaration():
    keys = declared(
        jobs.streaming_job("ratefeed", [], "rates", COLUMNS, poll=True, period="0D00:00:05")
    )
    assert keys["period"] == "0D00:00:05"


def test_a_compound_cursor_writes_the_whole_trio():
    plan = jobs.streaming_job(
        "bookfeed", [], "books", COLUMNS, poll=True, cursor="time,securityId,priceBookType"
    )
    poll = declared(plan)["poll"]
    assert poll.startswith("`fetch`normalize`next_cursor`load`save`advances`start_cursor!(")
    assert ".qetl.job.continuous.load_cursor_value" in poll
    assert ".qetl.job.continuous.save_cursor_value" in poll
    assert ".qetl.job.continuous.lexically_after[`time`securityId`priceBookType]" in poll
    assert "next_cursor:{[page] `time`securityId`priceBookType#last page}" in job_file(plan)


def test_a_compound_cursor_gets_a_start_cursor_to_write():
    plan = jobs.streaming_job(
        "bookfeed", [], "books", COLUMNS, poll=True, cursor="time,securityId,priceBookType"
    )
    assert ".qpipe.job.bookfeed.start_cursor" in declared(plan)["poll"]
    assert "bookfeed.start_cursor: not implemented" in job_file(plan)


def test_a_timestamp_cursor_needs_no_start_cursor():
    poll = declared(jobs.streaming_job("ratefeed", [], "rates", COLUMNS, poll=True))["poll"]
    assert "start_cursor" not in poll


def test_a_one_field_cursor_is_still_a_list():
    poll = declared(jobs.streaming_job("f", [], "t", COLUMNS, poll=True, cursor="id"))["poll"]
    assert "lexically_after[enlist `id]" in poll


@pytest.mark.parametrize(
    ("kwargs", "said"),
    [
        ({"subscribe_to": ["trades"], "poll": True}, "--poll is for a feed"),
        ({"subscribe_to": [], "cursor": "ts,id"}, "add --poll"),
        ({"subscribe_to": [], "poll": True, "cursor": "ts,ts"}, "names a field twice"),
        ({"subscribe_to": [], "poll": True, "cursor": "1ts"}, "not a q column name"),
    ],
)
def test_incompatible_requests_are_refused(kwargs, said):
    subs = kwargs.pop("subscribe_to")
    with pytest.raises(UqsError, match=said):
        jobs.streaming_job("ratefeed", subs, "rates", COLUMNS, known_tables={"trades"}, **kwargs)


def test_poll_needs_something_to_publish():
    with pytest.raises(UqsError, match="--poll needs --publishes"):
        jobs.streaming_job("ratefeed", [], None, None, poll=True)


@pytest.mark.parametrize("flag", [["--poll"], ["--cursor-fields", "ts"]])
def test_the_cli_refuses_poll_on_other_kinds(flag):
    result = runner.invoke(
        cli.app,
        ["job", "new", "fx", "--kind", "backfill", "--dataset", "fx", "--dry-run", *flag],
    )
    assert result.exit_code == 1
