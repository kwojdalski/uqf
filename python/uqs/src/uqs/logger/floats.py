"""Float precision in log lines: one global cutoff, LOG_FLOAT_DECIMALS.

get_logger (core.py) returns a CutFloatsLogger, so every `log = get_logger(...)`
in this tree writes a float with at most LOG_FLOAT_DECIMALS places. The
helpers that build their text before logging (utils.py, decorators.py) call
format_float / cut_floats themselves.
"""

from __future__ import annotations

import math
from typing import Any

#: The most decimal places a float in a log line is written with. One global
#: cutoff, so every module's lines round the same way, and a cutoff rather
#: than a fixed width: trailing zeros are dropped, so 2.5 stays 2.5 while
#: 1.0850000000000002 - float noise - becomes 1.085. Six places keeps an FX
#: quote exact (EURUSD quotes to five); a crypto price quoted finer than that
#: is rounded in the log, never in the data. Read on every call, so assigning
#: it takes effect at once.
LOG_FLOAT_DECIMALS = 6

#: At or above this magnitude a float has no fractional digits worth
#: showing, and fixed-point notation would print every one of its integer
#: digits - 1e300 as a 301-character number. Those keep Python's own form.
_FIXED_POINT_LIMIT = 1e16


def format_float(value: float, decimals: int | None = None) -> str:
    """*value* with at most *decimals* places (default LOG_FLOAT_DECIMALS),
    trailing zeros dropped: 0.123456789 -> "0.123457", 3.0 -> "3"."""
    places = LOG_FLOAT_DECIMALS if decimals is None else decimals
    # A plain float, so the formatting below cannot recurse into _LogFloat's.
    number = float(value)
    if not math.isfinite(number) or abs(number) >= _FIXED_POINT_LIMIT:
        return repr(number)
    text = f"{number:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    # -0.0000001 rounds to "-0", which says a sign the value no longer has.
    return "0" if text == "-0" else text


class _LogFloat(float):
    """A float that prints through format_float.

    A subclass rather than a string, because a message may format the value
    itself: `"{:.3f}"` must still work, and wins - an explicit format spec is
    the caller asking for exactly that. Only a bare `{}` (or str/repr) gets
    the cutoff.
    """

    __slots__ = ()

    def __format__(self, spec: str) -> str:
        return float.__format__(self, spec) if spec else format_float(self)

    def __str__(self) -> str:
        return format_float(self)

    __repr__ = __str__


def cut_floats(value: Any) -> Any:
    """*value* with every float in it - including inside a list, tuple or
    dict - printing at most LOG_FLOAT_DECIMALS places.

    Only exact list/tuple/dict are walked: a subclass (a namedtuple, an
    OrderedDict) is passed through untouched rather than rebuilt as its base
    type, which would change how it prints. Strings are never touched, so a
    q timestamp in a message - 11:21:28.123456789 - keeps every digit.
    """
    if isinstance(value, float):
        return value if type(value) is _LogFloat else _LogFloat(value)
    kind = type(value)
    if kind is list:
        return [cut_floats(v) for v in value]
    if kind is tuple:
        return tuple(cut_floats(v) for v in value)
    if kind is dict:
        return {k: cut_floats(v) for k, v in value.items()}
    return value


def _level_method(name: str) -> Any:
    def emit(self: CutFloatsLogger, message: Any, *args: Any, **kwargs: Any) -> None:
        getattr(self._at_caller(), name)(message, *self._cut(args), **self._cut_kwargs(kwargs))

    emit.__name__ = name
    return emit


class CutFloatsLogger:
    """The loguru logger, with float arguments cut to LOG_FLOAT_DECIMALS.

    loguru formats a message's arguments BEFORE any patcher or sink sees the
    record, so by then a float is already text. Rounding that text instead -
    a regex over the finished message - would also round the fraction of a
    q timestamp. So the cut happens here, on the arguments, and only on the
    ones that are floats.

    loguru records the location of whoever called it; called from here, that
    would be this wrapper. Every call therefore adds one to `depth`, so the
    location shown under --debug is still the caller's. opt() options are
    kept and applied together at call time, because loguru's own opt()
    resets whatever it is not given.
    """

    __slots__ = ("_inner", "_options")

    def __init__(self, inner: Any, options: dict[str, Any] | None = None) -> None:
        self._inner = inner
        self._options = options or {}

    def _at_caller(self) -> Any:
        options = dict(self._options)
        options["depth"] = options.get("depth", 0) + 1
        return self._inner.opt(**options)

    def _cut(self, args: tuple) -> list:
        if self._options.get("lazy"):
            # A lazy argument is a callable loguru calls only if the line is
            # emitted; its float is cut when that happens, not before.
            return [(lambda f=a: cut_floats(f())) if callable(a) else cut_floats(a) for a in args]
        return [cut_floats(a) for a in args]

    def _cut_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {k: cut_floats(v) for k, v in kwargs.items()}

    trace = _level_method("trace")
    debug = _level_method("debug")
    info = _level_method("info")
    success = _level_method("success")
    warning = _level_method("warning")
    error = _level_method("error")
    critical = _level_method("critical")
    exception = _level_method("exception")

    def log(self, level: str | int, message: Any, *args: Any, **kwargs: Any) -> None:
        self._at_caller().log(level, message, *self._cut(args), **self._cut_kwargs(kwargs))

    def opt(self, **options: Any) -> CutFloatsLogger:
        return CutFloatsLogger(self._inner, {**self._options, **options})

    def bind(self, **extra: Any) -> CutFloatsLogger:
        return CutFloatsLogger(self._inner.bind(**extra), self._options)

    def patch(self, patcher: Any) -> CutFloatsLogger:
        return CutFloatsLogger(self._inner.patch(patcher), self._options)

    def __getattr__(self, name: str) -> Any:
        # add, remove, level, catch, contextualize, ... - nothing that formats
        # a message, so nothing to cut.
        return getattr(self._inner, name)
