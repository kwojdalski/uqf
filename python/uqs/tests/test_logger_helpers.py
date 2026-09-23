"""Tests for the logger's helper modules: decorators.py and utils.py.

Nothing in this repository calls these yet - they came in with a logger ported
from another project, and were kept deliberately, to be used. That makes the
tests the only statement of what they do, so they pin behaviour a future
caller will rely on rather than behaviour a current one already exercises.

Writing them found one bug, fixed alongside: LogContext logged
"complete operation=..." even when the operation raised, straight after its
own error line.

Messages are captured through a real loguru sink rather than a fake logger.
These helpers pass loguru-style `{}` format arguments and call `logger.opt`,
and a fake would accept a call loguru itself would reject.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from uqs.logger import decorators, utils


@pytest.fixture(autouse=True)
def _restore_loguru():
    yield
    logger.remove()
    logger.add(sys.stderr)


def capture(level: str = "DEBUG") -> list[tuple[str, str]]:
    """Every sink replaced by one recording (level, message) at *level* and up.

    The level matters beyond filtering: is_level_enabled reads loguru's minimum
    level across sinks, so a sink at INFO is how a test says "not debugging".
    """
    seen: list[tuple[str, str]] = []
    logger.remove()
    logger.add(lambda m: seen.append((m.record["level"].name, m.record["message"])), level=level)
    return seen


def messages(seen: list[tuple[str, str]]) -> list[str]:
    return [m for _, m in seen]


# ================================================================ utils.py


def test_a_banner_is_centred_in_a_fixed_width():
    seen = capture()
    utils.log_banner(logger, "Backfill")
    (line,) = messages(seen)
    assert len(line) == utils._BANNER_WIDTH
    assert line.startswith("=") and line.endswith("=")
    assert "  Backfill  " in line
    left, right = line.split("  Backfill  ")
    assert abs(len(left) - len(right)) <= 1, "centred, to within one character"


def test_a_message_longer_than_the_banner_is_not_truncated():
    """Losing the end of the message would be worse than an uneven banner."""
    seen = capture()
    text = "x" * (utils._BANNER_WIDTH + 20)
    utils.log_banner(logger, text)
    (line,) = messages(seen)
    assert text in line and "=" not in line


@pytest.mark.parametrize(
    ("details", "extra", "expected"),
    [
        (None, None, "Processing step: load"),
        ("from disk", None, "Processing step: load - from disk"),
        (None, {"rows": 3}, "Processing step: load | {'rows': 3}"),
        ("from disk", {"rows": 3}, "Processing step: load - from disk | {'rows': 3}"),
    ],
)
def test_a_processing_step_appends_only_what_it_was_given(details, extra, expected):
    seen = capture()
    utils.log_processing_step(logger, "load", details, extra)
    assert seen == [("INFO", expected)]


def test_an_error_is_logged_with_its_type_context_and_data():
    seen = capture()
    try:
        raise KeyError("sym")
    except KeyError as exc:
        utils.log_error_with_context(logger, exc, "window 3", {"from": "09-11"})
    level, text = seen[0]
    assert level == "ERROR"
    assert text == "Error in window 3: KeyError: 'sym' | {'from': '09-11'}"
    assert seen[1] == ("DEBUG", "error details context=window 3"), "and the traceback at DEBUG"


def test_a_braced_value_in_an_error_does_not_break_formatting():
    """The message is passed as an ARGUMENT to "{}", never as the format
    string itself - so a q dictionary or a JSON value in an error cannot be
    read as format fields and raise inside the logging call."""
    seen = capture()
    try:
        raise ValueError("bad {key} in {x}")
    except ValueError as exc:
        utils.log_error_with_context(logger, exc, "parse")
    assert "bad {key} in {x}" in seen[0][1]


def test_a_function_call_is_logged_at_its_level():
    seen = capture()
    utils.log_function_call(logger, "fetch", (1, 2), {"k": "v"}, level="info")
    assert seen == [("INFO", "Calling function: fetch with args: (1, 2) with kwargs: {'k': 'v'}")]


def test_long_arguments_are_truncated_at_200_characters():
    seen = capture()
    utils.log_function_call(logger, "fetch", ("a" * 500,), {"k": "b" * 500})
    (text,) = messages(seen)
    args_part = text.split(" with args: ")[1].split(" with kwargs: ")[0]
    kwargs_part = text.split(" with kwargs: ")[1]
    assert len(args_part) == 203 and args_part.endswith("...")
    assert len(kwargs_part) == 203 and kwargs_part.endswith("...")


def test_a_function_call_below_the_enabled_level_is_skipped_entirely():
    """Skipped before the arguments are stringified: str() of a large
    argument is the expensive part, and it must not be paid when nothing
    would show it."""

    class Expensive:
        def __str__(self) -> str:
            raise AssertionError("stringified although DEBUG is off")

    seen = capture(level="INFO")
    utils.log_function_call(logger, "fetch", (Expensive(),))
    assert seen == []


def test_performance_is_logged_to_the_millisecond():
    seen = capture()
    utils.log_performance_metrics(logger, "window", 1.23456, {"rows": 10})
    assert seen == [("INFO", "Performance - window: 1.235s | Metrics: {'rows': 10}")]


def test_performance_without_extra_metrics_has_no_trailing_separator():
    seen = capture()
    utils.log_performance_metrics(logger, "window", 0.5)
    assert seen == [("INFO", "Performance - window: 0.500s")]


# ---------------------------------------------------------------- LogContext


def test_a_context_logs_start_complete_and_duration():
    seen = capture()
    with utils.LogContext(logger, "backfill"):
        pass
    text = messages(seen)
    assert text[0] == "start operation=backfill"
    assert text[1] == "complete operation=backfill"
    assert text[2].startswith("Performance - backfill: ")


def test_a_failing_context_does_not_claim_it_completed():
    """The bug this file found. The error line marks where a failed operation
    ended; a "complete" line after it was a success report for a failure."""
    seen = capture()
    with pytest.raises(RuntimeError), utils.LogContext(logger, "backfill"):
        raise RuntimeError("source down")
    text = messages(seen)
    assert "complete operation=backfill" not in text
    assert any(t.startswith("Error in backfill: RuntimeError: source down") for t in text)
    assert any(t.startswith("Performance - backfill: ") for t in text), (
        "duration is still recorded - how long a failure took is worth knowing"
    )


def test_a_context_re_raises_the_original_exception():
    capture()
    with pytest.raises(KeyError, match="sym"), utils.LogContext(logger, "op"):
        raise KeyError("sym")


def test_each_part_of_a_context_can_be_switched_off():
    seen = capture()
    with utils.LogContext(logger, "quiet", log_start=False, log_end=False, log_performance=False):
        pass
    assert seen == []


def test_a_context_below_the_enabled_level_logs_only_performance():
    seen = capture(level="INFO")
    with utils.LogContext(logger, "op", level="DEBUG"):
        pass
    assert [t for t in messages(seen) if "operation=" in t] == []
    assert len(seen) == 1 and seen[0][1].startswith("Performance - op")


# ------------------------------------------------------------ logged_function


def test_a_logged_function_returns_its_result_and_logs_the_call():
    seen = capture()

    @utils.logged_function(log_args=True, log_result=True)
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, b=3) == 5
    text = messages(seen)
    assert text[0] == "Calling function: add with args: (2,) with kwargs: {'b': 3}"
    assert text[1].startswith("Performance - Function add: ")
    assert text[2] == "Function add returned: 5"


def test_without_log_args_the_arguments_are_not_logged():
    """Arguments can hold credentials or a whole batch; logging them is opt-in."""
    seen = capture()

    @utils.logged_function()
    def connect(password: str) -> None:
        return None

    connect("s3cret")
    assert all("s3cret" not in t for t in messages(seen))


def test_a_long_result_is_truncated_at_100_characters():
    seen = capture()

    @utils.logged_function(log_result=True, log_performance=False)
    def big() -> str:
        return "r" * 500

    big()
    returned = [t for t in messages(seen) if t.startswith("Function big returned: ")][0]
    assert returned.endswith("...")
    assert len(returned.split("returned: ")[1]) == 103


def test_a_logged_function_that_raises_logs_it_and_re_raises():
    seen = capture()

    @utils.logged_function()
    def boom() -> None:
        raise ValueError("no quote")

    with pytest.raises(ValueError, match="no quote"):
        boom()
    (error,) = [t for lvl, t in seen if lvl == "ERROR"]
    assert error.startswith("Error in Function boom: ValueError: no quote | ")
    assert "'function': 'boom'" in error and "'duration_seconds':" in error


def test_a_logged_function_uses_a_given_logger():
    calls: list[str] = []

    class Recording:
        def __getattr__(self, name: str) -> Any:
            if name == "opt":
                return lambda **kw: self
            return lambda *a, **k: calls.append(name)

    capture()

    @utils.logged_function(logger=Recording(), level="INFO", log_performance=True)
    def f() -> int:
        return 1

    f()
    assert "log" in calls and "info" in calls, "the call and the timing both go to that logger"


def test_a_callable_without_a_name_can_be_decorated():
    """A functools.partial or a callable instance has no __name__. The
    decorator must not raise AttributeError for exactly the targets its own
    Callable annotation permits."""
    seen = capture()
    wrapped = utils.logged_function(log_performance=False)(functools.partial(pow, 2))
    assert wrapped(3) == 8
    assert "Calling function: functools.partial" in messages(seen)[0]


def test_the_decorator_preserves_the_function_s_identity():
    @utils.logged_function()
    def documented() -> None:
        """Docs."""

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Docs."


# =========================================================== decorators.py


def test_trace_calls_is_silent_and_transparent_when_not_debugging(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    seen = capture(level="INFO")

    @decorators.trace_calls()
    def f(x: int) -> int:
        return x * 2

    assert f(4) == 8
    assert seen == []


def test_trace_calls_logs_entry_with_named_arguments_and_completion():
    seen = capture()

    @decorators.trace_calls()
    def price(sym: str, qty: int, side: int = 1) -> float:
        return 1.1

    assert price("EURUSD", 1000000, side=-1) == 1.1
    entry, done = messages(seen)
    assert entry == ("→ [TRACE] test_logger_helpers.price(sym='EURUSD', qty=1000000, side=-1)")
    assert done.startswith("← [TRACE] test_logger_helpers.price completed (")


def test_log_level_debug_in_the_environment_turns_tracing_on(monkeypatch):
    """Tracing can be switched on for one run without reconfiguring sinks."""
    monkeypatch.setenv("LOG_LEVEL", "debug")
    seen: list[str] = []
    logger.remove()
    logger.add(lambda m: seen.append(m.record["message"]), level="DEBUG")
    monkeypatch.setattr(decorators, "is_level_enabled", lambda level: False)

    @decorators.trace_calls()
    def f() -> None:
        return None

    f()
    assert any("[TRACE]" in m for m in seen)


def test_show_return_logs_the_value():
    seen = capture()

    @decorators.trace_calls(show_return=True)
    def f() -> str:
        return "done"

    f()
    assert "returned 'done' (" in messages(seen)[1]


def test_nested_calls_are_indented_and_the_depth_is_restored():
    seen = capture()

    @decorators.trace_calls()
    def inner() -> int:
        return 1

    @decorators.trace_calls()
    def outer() -> int:
        return inner()

    outer()
    text = messages(seen)
    assert text[0].startswith("→ [TRACE]") and "outer" in text[0]
    assert text[1].startswith("  ↳ [TRACE]") and "inner" in text[1], "a nested call is indented"
    assert decorators._get_call_depth() == 0, "depth is back to zero afterwards"


def test_a_raising_call_is_traced_and_the_depth_still_restored():
    """The depth reset is in a finally. Without it one exception would leave
    every later trace in the thread indented one level too deep."""
    seen = capture()

    @decorators.trace_calls()
    def fail() -> None:
        raise LookupError("no chain")

    with pytest.raises(LookupError):
        fail()
    assert "raised LookupError: no chain (" in messages(seen)[1]
    assert decorators._get_call_depth() == 0


def test_variadic_arguments_are_not_labelled_with_the_star_args_name():
    """This found a bug. Every parameter name was used as a label, so the
    first variadic argument took the *args name and `f(1, 2)` traced as
    `f(values=1, 2)` - reading as though `values` were 1."""
    seen = capture()

    @decorators.trace_calls()
    def f(*values: int) -> None:
        return None

    f(1, 2)
    assert "f(1, 2)" in messages(seen)[0]


def test_named_then_variadic_arguments_are_labelled_only_where_named():
    seen = capture()

    @decorators.trace_calls()
    def f(sym: str, *levels: float, depth: int = 0) -> None:
        return None

    f("EURUSD", 1.1, 1.2, depth=2)
    assert "f(sym='EURUSD', 1.1, 1.2, depth=2)" in messages(seen)[0]


def test_a_callable_whose_signature_cannot_be_read_is_still_traced():
    """The trace falls back to unnamed arguments rather than failing the call.

    An earlier version of this test used `len`, on the belief that builtins
    have no inspectable signature - but on this Python `len` does, so the
    fallback it claimed to cover never ran. An object whose __signature__ is
    not a Signature is what makes inspect.signature actually raise.
    """

    class Opaque:
        __signature__ = "not a Signature"

        def __call__(self, *args: Any) -> int:
            return len(args)

    import inspect

    with pytest.raises(TypeError):
        inspect.signature(Opaque())
    seen = capture()
    traced = decorators.trace_calls()(Opaque())
    assert traced(1, 2) == 2
    assert "(1, 2)" in messages(seen)[0], "arguments shown, unnamed"


def test_the_call_depth_is_per_thread():
    import threading

    decorators._set_call_depth(5)
    seen: list[int] = []
    t = threading.Thread(target=lambda: seen.append(decorators._get_call_depth()))
    t.start()
    t.join()
    decorators._set_call_depth(0)
    assert seen == [0], "a new thread starts at depth zero, whatever this one is at"


# ------------------------------------------------------------ _format_arg_value


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("EURUSD", "'EURUSD'"),
        (42, "42"),
        (1.5, "1.5"),
        (True, "True"),
        (None, "None"),
        (Path("/tmp/x.q"), "Path('/tmp/x.q')"),
        ([1, 2, 3], "list(...)"),
        ({"a": 1}, "dict(...)"),
    ],
)
def test_arguments_are_summarised_by_kind(value, shown):
    assert decorators._format_arg_value(value) == shown


def test_a_long_string_is_truncated():
    assert decorators._format_arg_value("x" * 100) == f"'{'x' * 60}...'"


def test_a_long_path_keeps_its_end_where_the_file_name_is():
    p = Path("/very/long/" + "d" * 80 + "/file.q")
    shown = decorators._format_arg_value(p)
    assert shown.startswith("Path('...") and shown.endswith("file.q')")


class _Shaped:
    def __init__(self, shape: Any) -> None:
        self.shape = shape


def test_an_array_like_value_is_shown_by_shape_not_contents():
    """Printing a million-row frame in a trace line would be the trace."""
    frame = type("DataFrame", (_Shaped,), {})((1000000, 5))
    assert decorators._format_arg_value(frame) == "DataFrame(shape=(1000000, 5))"


def test_an_array_like_value_without_a_shape_is_elided():
    empty = type("ndarray", (_Shaped,), {})(None)
    assert decorators._format_arg_value(empty) == "ndarray(...)"


class _NoClass:
    """An object whose __class__ lookup raises - the only way to reach the
    plain-string fallback, since every ordinary object has a __class__."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def __class__(self):  # type: ignore[override]
        raise AttributeError("no class")

    def __str__(self) -> str:
        return self._text


def test_the_string_fallback_is_used_when_the_class_cannot_be_read():
    assert decorators._format_arg_value(_NoClass("short")) == "short"
    assert decorators._format_arg_value(_NoClass("z" * 100)) == "z" * 60 + "..."


def test_a_value_that_cannot_be_summarised_at_all_is_a_placeholder():
    """A trace line must never raise from inside the call it is tracing."""

    class Hostile(_NoClass):
        def __str__(self) -> str:
            raise RuntimeError("no str either")

    assert decorators._format_arg_value(Hostile("")) == "<value>"
