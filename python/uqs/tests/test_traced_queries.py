"""A polling feed's queries go through a path `--trace` can see.

The rule `test_source_queries.py` holds for bounded sources, for the feeds
`uqs stream preview --trace` reads: a fetch that applies its handle itself
runs, and its query never appears in the trace.
"""

from __future__ import annotations

import pytest

from uqs.checks.traced_queries import untraced_lines
from uqs.cli import install
from uqs.paths import repo_root
from uqs.stack.install import Item, Kind, Status

POLL = (
    "{name}:{{[cursor] {body}}};\n"
    ".qetl.job.stream.define[`f;`period`poll!(1s;`fetch`normalize`next_cursor!(fetch;n;c))];\n"
)


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        ("h({[c] select from t where time>c};cursor)", True),
        ("h@({[c] c};cursor)", True),
        ('h"select from t"', True),
        (".qetl.source.ipc_call[h;{[c] select from t where time>c};enlist cursor]", False),
        ("h({[c] c};cursor)  / untraced: the source logs its own queries", False),
    ],
)
def test_a_direct_call_is_told_from_a_traced_one(body, flagged):
    text = POLL.format(name="fetch", body=body)
    assert bool(untraced_lines(text)) is flagged


def test_a_comment_mentioning_a_direct_call_is_not_one():
    text = "/ never write h(f;x) here\n" + POLL.format(name="fetch", body="1")
    assert untraced_lines(text) == []


def test_a_file_that_declares_no_poll_is_not_checked():
    """A subscriber has no query of its own; bounded sources have their own gate."""
    assert untraced_lines("f:{[h;x] h(x)}\n") == []


def test_the_line_is_named():
    text = POLL.format(name="fetch", body="h({[c] c};cursor)")
    assert untraced_lines(text) == [(1, "fetch:{[cursor] h({[c] c};cursor)};")]


@pytest.mark.parametrize(
    "path",
    sorted((repo_root() / "src" / "etl" / "streaming").glob("*.q")),
    ids=lambda p: p.stem,
)
def test_no_feed_in_the_tree_sends_an_untraced_query(path):
    assert untraced_lines(path.read_text()) == []


def test_install_jobs_says_where_a_sidecar_sends_an_untraced_query(tmp_path, monkeypatch):
    feed = tmp_path / "vectorize2.q"
    feed.write_text(POLL.format(name="fetch", body="h({[c] c};cursor)"))
    said: list[str] = []
    monkeypatch.setattr(install.console, "print", lambda *a, **k: said.append(str(a[0])))
    item = Item(feed, Kind.STREAMING, ("vectorize2",), feed, Status.NEW)
    install._report_untraced_fetches([item])
    assert any("vectorize2.q:1" in s and ".qetl.source.ipc_call" in s for s in said)
