"""Query traces in `uqs logs`: shown as code, read from one-line log records."""

from __future__ import annotations

import subprocess

import pytest

from uqs.interpreter import q_interpreter
from uqs.paths import repo_root
from uqs.stack import logs as stack_logs
from uqs.stack.trace_render import decode_q_string, render_query_trace

IPC = (
    'query sent call="{[from_ts;to_ts]\\n        select deal_id from `demo_deals\\n'
    '            where deal_time>=from_ts, deal_time<to_ts\\n      }"'
    " range_from=2026.09.10D00:00:00.000000000 range_to=2026.09.11D00:00:00.000000000"
)


def test_an_ipc_trace_is_a_header_and_an_indented_block():
    assert render_query_trace(IPC) == (
        "query sent range_from=2026.09.10D00:00:00.000000000 "
        "range_to=2026.09.11D00:00:00.000000000\n"
        "    {[from_ts;to_ts]\n"
        "            select deal_id from `demo_deals\n"
        "                where deal_time>=from_ts, deal_time<to_ts\n"
        "          }"
    )


def test_a_sql_trace_is_rendered_the_same_way():
    msg = 'sql sent statement="SELECT a, b\\nFROM t\\nWHERE ts >= 1"'
    assert render_query_trace(msg) == "sql sent\n    SELECT a, b\n    FROM t\n    WHERE ts >= 1"


@pytest.mark.parametrize(
    ("literal", "text"),
    [
        ('"say \\"hi\\""', 'say "hi"'),
        ('"a\\\\b"', "a\\b"),
        ('"a\\\\nb"', "a\\nb"),  # a literal backslash-n in the code stays two characters
        ('"x|y"', "x|y"),
        ('"tab\\there"', "tab\there"),
        ('"\\001\\377"', "\x01\xff"),
    ],
)
def test_quotes_backslashes_literal_backslash_n_and_pipes_survive(literal, text):
    assert decode_q_string(literal, 0) == (text, len(literal))


@pytest.mark.parametrize(
    "message",
    [
        "run finished state=`completed",
        'window start note="a\\nb"',  # \n in an ordinary message is NOT decoded
        "query sent rows=3 ms=12",  # the returned line has no code field
        'query sent call="unterminated',
        'sql sent statement="bad \\q escape"',
    ],
)
def test_anything_else_is_shown_exactly_as_it_was(message):
    assert render_query_trace(message) == message


def test_the_follow_path_renders_through_the_same_function(monkeypatch):
    """Recent and follow both print through _emit, so they cannot disagree."""
    seen = []

    class _Log:
        def bind(self, **_):
            return self

        def log(self, level, message):
            seen.append((level, message))

    rec = {"time": "2026.08.22D14:21:10.0", "procname": "p", "proctype": "t"}
    stack_logs._emit(_Log(), {**rec, "loglevel": "TRC", "message": IPC}, None)
    assert seen == [("TRACE", render_query_trace(IPC))]


def test_what_q_writes_decodes_back_to_the_original():
    """Round trip against the real writer, .qetl.log.quoted, on every byte."""
    q = q_interpreter()
    if q is None:
        pytest.skip("no q interpreter")
    script = (
        f"\\l {repo_root() / 'src' / 'etl' / 'core' / 'log.q'}\n"
        '-1 .qetl.log.quoted "{[a;b]\\n  select from t where s=\\"x|y\\", '
        'p like \\"a\\\\\\\\nb\\"\\n}";\n'
        "-1 .qetl.log.quoted `char$til 256;\nexit 0\n"
    )
    out = subprocess.run(
        [str(q), "-q"],
        input=script,
        capture_output=True,
        text=True,
        timeout=60,
        encoding="latin-1",
        check=False,
    ).stdout.splitlines()
    code, everything = (decode_q_string(line, 0) for line in out[:2])
    want = '{[a;b]\n  select from t where s="x|y", p like "a\\\\nb"\n}'
    assert code is not None and code[0] == want
    assert everything is not None and everything[0] == "".join(map(chr, range(256)))
