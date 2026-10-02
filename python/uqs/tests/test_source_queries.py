"""Every source sends its query through a path `--trace` can see.

`uqs backfill --trace` logs each query a source is sent, at TRC, from two
places: `.qetl.source.ipc` for a q lambda over IPC, and `.qetl.io.odbc.run_sql`
for SQL (`window_query` sends through it too). A source whose `query` calls
the handle itself - `h({...};from;to)` - still works, and its queries are
invisible to the switch that exists to show them. Nothing would fail: the
trace would just be missing the one query someone turned it on to see.

Read as text, so it needs no q.
"""

from __future__ import annotations

import re

import pytest

from uqs.paths import repo_root

SOURCES = sorted((repo_root() / "src" / "etl" / "sources").glob("*.q"))

#: The calls that log a query at TRC before sending it.
TRACED = (
    ".qetl.source.ipc[",
    ".qetl.source.local[",
    ".qetl.io.odbc.run_sql[",
    ".qetl.io.odbc.window_query[",
)

#: A handle applied directly: `h(` or `h@`, as a word - not `.qetl.source.ipc[h;`.
DIRECT = re.compile(r"(?<![\w.])h\s*[(@]")


def query_body(text: str) -> str:
    """The `query:{...}` definition: from its line to the next blank line."""
    match = re.search(r"(?ms)^query:\{.*?(?=^\s*$)", text)
    assert match, "no `query:{` definition found"
    return match.group(0)


def test_the_sources_are_found():
    assert len(SOURCES) >= 6


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.stem)
def test_a_source_sends_its_query_through_a_traced_path(path):
    body = query_body(path.read_text())
    assert any(call in body for call in TRACED), (
        f"{path.name}: `query` sends through none of {', '.join(TRACED)} - "
        "an IPC source uses .qetl.source.ipc[h;{[from_ts;to_ts] ...};range_from;range_to]"
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.stem)
def test_a_source_never_calls_the_handle_itself(path):
    body = query_body(path.read_text())
    assert not DIRECT.search(body), (
        f"{path.name}: `query` applies the handle directly, so --trace cannot show "
        "what it sends - use .qetl.source.ipc"
    )


@pytest.mark.parametrize(
    ("body", "direct"),
    [
        ("query:{[h;a;b]\n    h({[x;y] select from t};a;b)}\n", True),
        ("query:{[h;a;b] h@({[x;y] x};a;b)}\n", True),
        ("query:{[h;a;b]\n    .qetl.source.ipc[h;{[x;y] select from t};a;b]}\n", False),
        ("query:{[h;a;b] adapt .qetl.io.odbc.run_sql[h;sql_for[a;b]]}\n", False),
    ],
)
def test_the_check_tells_a_direct_call_from_a_traced_one(body, direct):
    assert bool(DIRECT.search(query_body(body + "\n"))) is direct
