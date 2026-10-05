"""A polling feed's and a backfill source's queries go through a path
`--trace` can see: a file that applies its handle itself runs, and its query
never appears in the trace. `test_install.py` holds `uqs job install` to
refusing one; `test_source_queries.py` additionally requires each source's
`query` to send through a traced path.
"""

from __future__ import annotations

import pytest

from uqs.checks.traced_queries import untraced_lines
from uqs.paths import repo_root

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


def test_a_file_that_declares_neither_a_poll_nor_a_source_is_not_checked():
    """A subscriber or a worker has no query of its own."""
    assert untraced_lines("f:{[h;x] h(x)}\n") == []


def test_a_backfill_source_is_checked_too():
    source = "query:{[h;a;b] h({[x;y] x};a;b)}\n.qetl.source.define[`s;`query!enlist query];\n"
    assert untraced_lines(source) == [(1, "query:{[h;a;b] h({[x;y] x};a;b)}")]
    traced = source.replace("h({[x;y] x};a;b)", ".qetl.source.ipc[h;{[x;y] x};a;b]")
    assert untraced_lines(traced) == []


def test_the_line_is_named():
    text = POLL.format(name="fetch", body="h({[c] c};cursor)")
    assert untraced_lines(text) == [(1, "fetch:{[cursor] h({[c] c};cursor)};")]


@pytest.mark.parametrize(
    "path",
    sorted(
        [
            *(repo_root() / "src" / "etl" / "streaming").glob("*.q"),
            *(repo_root() / "src" / "etl" / "sources").glob("*.q"),
        ]
    ),
    ids=lambda p: p.stem,
)
def test_no_feed_or_source_in_the_tree_sends_an_untraced_query(path):
    assert untraced_lines(path.read_text()) == []
