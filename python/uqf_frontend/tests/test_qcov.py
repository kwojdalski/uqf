"""Tests for `scripts/qcov.py` - statement-level coverage for q.

TWO THINGS HAVE TO BE TRUE for this tool to be worth running, and they pull
in opposite directions:

  1. It must instrument ENOUGH. A probe skipped is a statement that reports
     as covered forever, which is the failure mode that makes a coverage
     number worse than none.
  2. It must instrument ONLY statement positions. q's `$[c;a;b]` is a
     conditional EXPRESSION; injecting a probe into an arm either fails to
     parse or silently changes what the expression returns. `if[c;a;b]` is a
     control statement and its arms DO take probes. The two look alike.

So most of the file is pairs: a construct that must be instrumented, and its
look-alike that must not.

The lexer gets its own tests because everything rests on it - a `;` inside a
string or a comment, mistaken for syntax, moves every probe after it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "qcov", Path(__file__).resolve().parents[3] / "scripts" / "qcov.py"
)
assert _SPEC and _SPEC.loader
qcov = importlib.util.module_from_spec(_SPEC)
sys.modules["qcov"] = qcov
_SPEC.loader.exec_module(qcov)


def _kinds(src: str) -> list[tuple[str, str]]:
    return [(t.kind, t.text) for t in qcov.lex(src) if t.kind not in ("ws", "newline")]


def _probe_lines(src: str) -> list[int]:
    return [p.line for p in qcov.instrument(src)[1]]


def _instrumented(src: str) -> str:
    return qcov.instrument(src)[0]


# ------------------------------------------------------------------- lexing


def test_a_semicolon_inside_a_string_is_not_syntax():
    # The whole tool rests on this: a string's `;` counted as a statement
    # separator shifts every probe after it onto the wrong line.
    assert ("string", '"a;b"') in _kinds('x:"a;b"')


def test_a_bracket_inside_a_string_is_not_syntax():
    assert ("string", '"]}"') in _kinds('x:"]}"')


def test_an_escaped_quote_does_not_end_a_string():
    assert ("string", '"a\\"b"') in _kinds('x:"a\\"b"')


def test_a_trailing_comment_runs_to_end_of_line():
    kinds = _kinds("a:1 / and ; this ] is prose\nb:2")
    assert ("comment", "/ and ; this ] is prose") in kinds
    assert ("name", "b") in kinds


def test_slash_after_a_value_is_the_over_adverb_not_a_comment():
    """`a/b` is `over`, not a comment. Treating it as one would silently
    delete the rest of the line - and q would still parse what was left."""
    assert not any(k == "comment" for k, _ in _kinds("x:+/y"))


def test_a_lone_slash_line_opens_a_block_comment():
    """The trap this repository has been bitten by: a line containing only
    `/` comments out everything until a line containing only `\\`."""
    kinds = _kinds("a:1\n/\nb:2\nc:3\n\\\nd:4")
    names = [t for k, t in kinds if k == "name"]
    assert "b" not in names and "c" not in names, "the block's contents are not code"
    assert "a" in names and "d" in names, "code either side of the block survives"


def test_an_unterminated_block_comment_runs_to_end_of_file():
    # q's own behaviour, not an error.
    names = [t for k, t in _kinds("a:1\n/\nb:2") if k == "name"]
    assert names == ["a"]


def test_a_system_command_line_is_not_parsed_as_code():
    """`\\d .qfwd` and `\\l file.q` are directives. Their contents must not
    be tokenised, or a path's `/` looks like an adverb."""
    kinds = _kinds("\\d .qfwd\nf:{1}")
    assert ("system", "\\d .qfwd") in kinds
    assert ("name", "f") in kinds


def test_a_symbol_swallows_its_name():
    assert ("symbol", "`a`b") not in _kinds("x:`a`b")  # two symbols, not one
    assert _kinds("x:`abc")[-1] == ("symbol", "`abc")


def test_a_file_path_symbol_keeps_its_slashes():
    assert ("symbol", "`:/tmp/x") in _kinds("h:`:/tmp/x")


def test_an_unterminated_string_is_an_error_not_a_guess():
    with pytest.raises(qcov.LexError):
        qcov.lex('x:"abc\ny:1')


# ------------------------------------------------- instrumenting: must fire


def test_a_lambda_body_gets_a_probe():
    assert len(_probe_lines("f:{[a] a}")) == 1


def test_every_statement_in_a_body_gets_its_own_probe():
    assert len(_probe_lines("f:{[] a:1; b:2; a+b}")) == 3


def test_a_probe_records_the_line_the_statement_is_on():
    src = "f:{[]\n  a:1;\n  b:2;\n  a+b}"
    assert _probe_lines(src) == [2, 3, 4]


def test_if_arms_are_statements_and_are_instrumented():
    # The branch coverage that function-level counting cannot see.
    assert len(_probe_lines("f:{[x] if[x>0; :1]; 0}")) == 3


def test_do_and_while_bodies_are_instrumented():
    assert len(_probe_lines("f:{[x] r:0; do[x; r+:1]; r}")) == 4
    assert len(_probe_lines("f:{[x] r:0; while[r<x; r+:1]; r}")) == 4


def test_a_nested_lambda_is_instrumented_too():
    assert len(_probe_lines("f:{[x] g:{[y] y*2}; g x}")) == 3


# --------------------------------------------- instrumenting: must NOT fire


def test_a_conditional_expression_is_not_a_statement_context():
    """`$[c;a;b]` returns a VALUE. A probe in an arm changes what it
    returns, or stops it parsing. This is the distinction the whole
    instrumenter turns on."""
    assert len(_probe_lines("f:{[x] $[x>0;1;2]}")) == 1


def test_a_function_call_argument_is_not_a_statement():
    assert len(_probe_lines("f:{[x] g[x;1;2]}")) == 1


def test_a_list_is_not_a_statement_context():
    assert len(_probe_lines("f:{[x] (1;2;3)}")) == 1


def test_an_index_is_not_a_statement_context():
    assert len(_probe_lines("f:{[x] x[1;2]}")) == 1


def test_a_parameter_list_is_not_instrumented():
    """`{[a;b]` is a declaration. Its semicolons separate parameters, and a
    probe among them is a syntax error."""
    out = _instrumented("f:{[a;b;c] a}")
    assert "[a;b;c]" in out, out


def test_a_trailing_semicolon_separates_nothing():
    assert len(_probe_lines("f:{[] a;}")) == 1


def test_top_level_statements_are_not_instrumented():
    """Everything at file top level runs on load, so it would report as
    covered unconditionally and only pad the denominator."""
    assert _probe_lines("a:1;\nb:2;\n") == []


# ------------------------------------------------------------- the reports


def _cov(path: str, probes: list[tuple[int, int]], hit: set[int]):
    c = qcov.FileCoverage(path, [qcov.Probe(i, line) for i, line in probes])
    c.hits = {i: 1 for i in hit}
    return c


def test_missing_lines_are_reported_as_ranges():
    """`12-15` rather than four separate numbers: an uncovered block is one
    fact, and the compressed form is what coverage.py's readers expect."""
    c = _cov("f.q", [(0, 12), (1, 13), (2, 14), (3, 19)], hit=set())
    assert qcov._ranges(c.missing_lines) == "12-14, 19"


def test_a_line_with_one_covered_probe_is_not_reported_missing():
    c = _cov("f.q", [(0, 5), (1, 6)], hit={0})
    assert c.missing_lines == [6]


def test_the_terminal_report_totals_across_files():
    files = {
        "a.q": _cov("a.q", [(0, 1), (1, 2)], hit={0}),
        "b.q": _cov("b.q", [(2, 1)], hit={2}),
    }
    text = qcov.report_terminal(files)
    assert "TOTAL" in text
    assert "3" in text and "67%" in text, text


def test_lcov_takes_the_maximum_count_for_a_shared_line():
    """Several q statements can share a line. The line RAN if any of them
    ran, so reporting the minimum would mark executed lines as missed."""
    c = qcov.FileCoverage("a.q", [qcov.Probe(0, 7), qcov.Probe(1, 7)])
    c.hits = {0: 0, 1: 4}
    text = qcov.report_lcov({"a.q": c})
    assert "DA:7,4" in text
    assert "LH:1" in text and "LF:1" in text


def test_json_carries_the_missing_lines_not_just_a_number():
    import json as _json

    files = {"a.q": _cov("a.q", [(0, 3), (1, 9)], hit={0})}
    payload = _json.loads(qcov.report_json(files))
    assert payload["files"]["a.q"]["missing_lines"] == [9]
    assert payload["total"]["percent"] == 50.0


def test_a_file_with_no_statements_is_left_out_of_the_report():
    # A declarations-only file has nothing to cover; a 0/0 row reading 100%
    # is noise that makes the real rows harder to find.
    files = {"empty.q": qcov.FileCoverage("empty.q", [])}
    assert "empty.q" not in qcov.report_terminal(files)


# --------------------------------------------------- it never edits the tree


def test_writing_replaces_a_symlink_instead_of_following_it(tmp_path):
    """The bug this tool shipped with for exactly one run.

    The shadow tree symlinks everything it does not instrument, so writing
    to a path inside it wrote THROUGH the link and edited the real
    repository. A coverage tool that modifies the tree it measures is worse
    than no coverage tool.
    """
    real = tmp_path / "real.q"
    real.write_text("original")
    link = tmp_path / "link.q"
    link.symlink_to(real)

    qcov._write_real(link, "instrumented")

    assert real.read_text() == "original", "the target must not be touched"
    assert link.read_text() == "instrumented"
    assert not link.is_symlink()
