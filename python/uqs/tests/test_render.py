"""stack/render.py: choosing how `uqs query` shows a result, and the q that
asks the process to print it. What that q returns is tested against a live
process in test_runtime.py."""

from __future__ import annotations

import os

import pytest

from uqs.paths import UqsError
from uqs.stack import render


@pytest.mark.parametrize(
    ("flag", "env", "chosen"),
    [
        (None, {}, "q"),
        (None, {"UQS_QUERY_RENDER": "kola"}, "kola"),
        (None, {"UQS_QUERY_RENDER": " KOLA "}, "kola"),
        ("q", {"UQS_QUERY_RENDER": "kola"}, "q"),
        ("kola", {}, "kola"),
    ],
)
def test_the_flag_wins_over_the_variable_which_wins_over_q(flag, env, chosen):
    assert render.renderer(flag, env) == chosen


@pytest.mark.parametrize(
    ("flag", "env", "named"),
    [("python", {}, "--render"), (None, {"UQS_QUERY_RENDER": "pykx"}, "UQS_QUERY_RENDER")],
)
def test_an_unknown_renderer_is_refused_naming_where_it_came_from(flag, env, named):
    with pytest.raises(UqsError, match=f"{named}=.*q, kola"):
        render.renderer(flag, env)


@pytest.mark.parametrize(
    ("terminal", "size"),
    [((120, 40), (40, 120)), ((3, 2), (10, 10)), ((9000, 9000), (2000, 2000))],
)
def test_the_console_size_is_the_terminals_inside_qs_range(monkeypatch, terminal, size):
    monkeypatch.setattr(
        render.shutil, "get_terminal_size", lambda fallback: os.terminal_size(terminal)
    )
    assert render.console_size() == size


def test_with_no_terminal_the_whole_result_is_laid_out(monkeypatch):
    monkeypatch.setattr(
        render.shutil, "get_terminal_size", lambda fallback: os.terminal_size(fallback)
    )
    assert render.console_size() == (2000, 2000)


def test_the_wrapper_sets_the_size_and_carries_the_expression_as_a_string():
    q = render.wrap('select from t where s like "EUR*"', (40, 120))
    assert 'system"c 40 120"' in q
    assert q.endswith('"select from t where s like \\"EUR*\\""')
    assert ".Q.s value x" in q


def test_a_backslash_survives_quoting():
    assert render.quoted("\\t 1") == "\\\\t 1"


@pytest.mark.parametrize(("raw", "text"), [(b"2\n", "2\n"), ("2\n", "2\n")])
def test_kola_bytes_or_str_both_come_back_as_text(raw, text):
    assert render.text(raw) == text
