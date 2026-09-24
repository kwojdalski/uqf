"""Tests for the logger core and the `uqs logs` readers.

There were none. `logger/core.py` sat at 46% and `stack/logs.py` at 73%, and the
untested parts are the ones a reader actually meets: the colouring that tells
a negative P&L from a positive one at a glance, the LOG_REGEX filter, the
per-component log file, and `follow_logs` - the live tail behind
`uqs logs -f`, whose subprocess-and-thread plumbing had never run.

loguru is a process-wide singleton, so every test here restores a default
sink afterwards rather than leaving the next test logging into a closed list.

`logger/decorators.py` and `logger/utils.py` are tested separately, in
test_logger_helpers.py.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from loguru import logger

# Aliased `logcore`, not `core`: test_module_split.py reads every test file
# for `core.<name>` as a use of the orchestrator's `core` facade, and cannot
# tell the logger's module of the same name from it.
from uqs.logger import core as logcore
from uqs.logger import floats
from uqs.stack import logs

if TYPE_CHECKING:
    # Not exported by loguru at runtime; see logger/core.py.
    from loguru import Record


@pytest.fixture(autouse=True)
def _restore_loguru():
    yield
    logger.remove()
    logger.add(sys.stderr)


def _capture() -> list[str]:
    """Replace every sink with one that records formatted messages."""
    seen: list[str] = []
    logger.remove()
    logger.add(lambda m: seen.append(str(m)), format="{level}|{message}", level="DEBUG")
    return seen


# ------------------------------------------------------ key=value colouring


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ok=True", "ok=<green>True</green>"),
        ("ok=False", "ok=<red>False</red>"),
        ("value=None", "value=<dim>None</dim>"),
        ("pnl=-1250.5", "pnl=<red>-1250.5</red>"),
        ("pnl=1250.5", "pnl=<magenta>1250.5</magenta>"),
        ("rows=11,643", "rows=<magenta>11,643</magenta>"),
        ("step=96921/969218", "step=<magenta>96921/969218</magenta>"),
        ("rate=12.5/s", "rate=<magenta>12.5/s</magenta>"),
        ("latency=45ms", "latency=<magenta>45ms</magenta>"),
        ("fill=98.5%", "fill=<magenta>98.5%</magenta>"),
        ("sym=EURUSD", "sym=<light-cyan>EURUSD</light-cyan>"),
    ],
)
def test_a_value_is_coloured_by_what_it_is(message, expected):
    """The point of the colouring is reading a line at a glance - a negative
    number in red is how a loss stands out from a gain."""
    assert logcore._highlight_kv(message) == expected


@pytest.mark.parametrize(
    "path", ["/tmp/x.log", "./out", "../up", "config.yaml", "tests/run_tests.q"]
)
def test_a_path_is_bold_rather_than_read_as_a_number_or_word(path):
    assert logcore._highlight_kv(f"file={path}") == f"file=<bold>{path}</bold>"


def test_a_dotted_word_that_is_not_a_known_extension_is_not_a_path():
    # `.qfwd` is a namespace, not a file; only the listed extensions count.
    assert "<bold>" not in logcore._highlight_kv("ns=a.qfwd")


def test_text_without_key_value_pairs_is_untouched():
    assert logcore._highlight_kv("plain message, no pairs") == "plain message, no pairs"


def test_the_format_callable_escapes_what_loguru_would_misread():
    """A `<` in a message would be taken for a colour tag, and a brace for a
    format field - either would garble or break the line."""
    fmt = logcore._make_kv_format("{level} - {message}")
    out = fmt({"message": "a<b {not_a_field} n=1"})
    assert r"a\<b" in out
    assert "{{not_a_field}}" in out
    assert out.endswith("\n")


# ------------------------------------------------------------ setup_logging


def test_log_regex_keeps_only_matching_messages(capsys):
    logcore.setup_logging(level="DEBUG", colored_output=False, log_regex="keep")
    logger.info("keep this")
    logger.info("drop this")
    out = capsys.readouterr().out
    assert "keep this" in out
    assert "drop this" not in out


def test_log_regex_is_read_from_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("LOG_REGEX", "only")
    logcore.setup_logging(level="DEBUG", colored_output=False)
    logger.info("only me")
    logger.info("not me")
    out = capsys.readouterr().out
    assert "only me" in out and "not me" not in out


def test_an_invalid_log_regex_is_reported_and_ignored(capsys):
    """An unparseable filter must not silence everything - that would look
    exactly like a process with nothing to say."""
    logcore.setup_logging(level="DEBUG", colored_output=False, log_regex="([unclosed")
    logger.info("still shown")
    captured = capsys.readouterr()
    assert "Invalid LOG_REGEX ignored" in captured.err
    assert "still shown" in captured.out


def test_a_log_file_is_created_with_its_directory(tmp_path):
    target = tmp_path / "nested" / "dir" / "app.log"
    logcore.setup_logging(level="INFO", console_output=False, log_file=str(target))
    logger.info("to the file")
    logger.remove()  # flush and close before reading
    assert "to the file" in target.read_text()


def test_structured_logging_writes_json(capsys):
    logcore.setup_logging(level="INFO", structured_logging=True)
    logger.info("structured")
    out = capsys.readouterr().out
    assert '"text"' in out and "structured" in out


def test_the_level_threshold_is_applied(capsys):
    logcore.setup_logging(level="warning", colored_output=False)
    logger.info("too quiet")
    logger.warning("loud enough")
    out = capsys.readouterr().out
    assert "loud enough" in out and "too quiet" not in out


# -------------------------------------------------------- configure_logging


def test_configure_logging_writes_a_dated_per_component_file(tmp_path):
    logcore.configure_logging(component="uqs", log_dir=str(tmp_path), include_console=False)
    logger.info("hello")
    logger.remove()
    files = list(tmp_path.glob("uqs_*.log"))
    assert len(files) == 1, "one file, named for the component and the day"
    assert "hello" in files[0].read_text()


def test_debug_overrides_the_level(capsys):
    logcore.configure_logging(component="c", debug=True, level="ERROR")
    # No `key=value` in the text: that would be colourised, and the
    # assertion would then be about the colouring rather than the level.
    logger.debug("visible at debug level")
    assert "visible at debug level" in capsys.readouterr().out


def test_the_full_format_names_the_component(capsys):
    logcore.configure_logging(component="rdb_probe", simplified=False)
    logger.warning("x")
    assert "rdb_probe" in capsys.readouterr().out


def test_the_cli_format_shows_the_level(capsys, monkeypatch):
    """The simplified format used to be "{function}:{line} - {message}": no
    level, and no colour markup for a terminal to render."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    logcore.configure_logging(component="uqs")
    logger.error("it broke")
    out = capsys.readouterr().out
    assert out.startswith("ERROR")
    assert "it broke" in out


def test_the_location_is_shown_only_when_debugging(capsys, monkeypatch):
    """Every refusal went through one helper, so an always-on location read
    `_die:120` for all of them. It earns its width only at DEBUG."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    logcore.configure_logging(component="uqs", level="INFO")
    logger.warning("quiet")
    assert "test_the_location_is_shown" not in capsys.readouterr().out
    logcore.configure_logging(component="uqs", level="DEBUG")
    logger.warning("loud")
    assert "test_the_location_is_shown_only_when_debugging" in capsys.readouterr().out


def test_colour_follows_the_terminal_not_a_hardcoded_true(capsys, monkeypatch):
    """captured output is not a terminal, so it gets no escape codes - the
    same as `uqs ... | grep` - unless FORCE_COLOR asks for them."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    logcore.configure_logging(component="uqs")
    logger.error("plain")
    assert "\x1b[" not in capsys.readouterr().out

    monkeypatch.setenv("FORCE_COLOR", "1")
    logcore.configure_logging(component="uqs")
    logger.error("coloured")
    assert "\x1b[" in capsys.readouterr().out

    monkeypatch.setenv("NO_COLOR", "1")
    logcore.configure_logging(component="uqs")
    logger.error("NO_COLOR wins over FORCE_COLOR")
    assert "\x1b[" not in capsys.readouterr().out


# --------------------------------------------------------- float precision


def _records() -> list[Record]:
    """Every sink replaced by one keeping each record's message and location."""
    seen: list[Record] = []
    logger.remove()
    logger.add(lambda m: seen.append(m.record), level="DEBUG")
    return seen


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0850000000000002, "1.085"),
        (0.123456789, "0.123457"),
        (2.5, "2.5"),
        (3.0, "3"),
        (-1250.5, "-1250.5"),
        (-0.0000001, "0"),
        (1e20, "1e+20"),
        (float("nan"), "nan"),
        (float("-inf"), "-inf"),
    ],
)
def test_a_float_is_written_with_at_most_six_places(value, expected):
    assert floats.format_float(value) == expected


def test_a_float_argument_is_cut_and_nothing_else_is():
    seen = _records()
    log = logcore.get_logger("t")
    log.info("mid={} rows={} ok={} id={}", 1.0850000000000002, 7, True, "a.1234567891")
    assert seen[0]["message"] == "mid=1.085 rows=7 ok=True id=a.1234567891"


def test_a_q_timestamp_in_a_message_keeps_every_digit():
    """The reason the cut is on arguments, not on the finished text: a regex
    over the message would round the fraction of this timestamp too."""
    seen = _records()
    logcore.get_logger("t").info("{}", "2026.09.24D11:21:28.123456789")
    assert seen[0]["message"] == "2026.09.24D11:21:28.123456789"


def test_an_explicit_format_spec_wins_over_the_cutoff():
    seen = _records()
    logcore.get_logger("t").info("{:.2f} {:.9f}", 1.23456, 0.123456789123)
    assert seen[0]["message"] == "1.23 0.123456789"


def test_floats_inside_a_list_tuple_or_dict_are_cut():
    seen = _records()
    logcore.get_logger("t").info("{} {} {}", [0.1234567891], (2.0,), {"mid": 1.0850000000000002})
    assert seen[0]["message"] == "[0.123457] (2,) {'mid': 1.085}"


def test_the_cutoff_is_one_global_setting(monkeypatch):
    monkeypatch.setattr(floats, "LOG_FLOAT_DECIMALS", 3)
    seen = _records()
    logcore.get_logger("t").info("{}", 0.123456789)
    assert seen[0]["message"] == "0.123"


def test_the_location_is_still_the_callers():
    """loguru records whoever called it; through the wrapper that would be the
    wrapper, and --debug would point every line at logger/core.py."""
    seen = _records()
    logcore.get_logger("t").warning("{}", 1.5)
    logcore.get_logger("t").log("INFO", "{}", 1.5)
    assert [r["function"] for r in seen] == ["test_the_location_is_still_the_callers"] * 2
    assert all(r["file"].name == "test_logger.py" for r in seen)


def test_bind_and_opt_still_work_through_the_wrapper():
    seen = _records()
    log = logcore.get_logger("t")
    log.bind(procname="rdb1").info("{}", 0.123456789)
    log.opt(lazy=True).info("{}", lambda: 0.123456789)
    assert [r["message"] for r in seen] == ["0.123457", "0.123457"]
    assert seen[0]["extra"]["procname"] == "rdb1"
    assert seen[1]["function"] == "test_bind_and_opt_still_work_through_the_wrapper"


# ------------------------------------------------------------ logs readers


def _paths_with_logs(tmp_path: Path, monkeypatch, lines: dict[str, list[str]]):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for name, content in lines.items():
        (log_dir / name).write_text("".join(f"{line}\n" for line in content))
    monkeypatch.setattr(logs, "list_process_names", lambda paths: ["rdb1", "hdb1"])
    return SimpleNamespace(torqdata=tmp_path)


LINE_INF = "2026.08.22D14:21:10.644413000|host|rdb|rdb1|INF|init|started"
LINE_ERR = "2026.08.22D14:21:11.000000000|host|rdb|rdb1|ERR|conn|lost the tickerplant"


def test_print_recent_logs_emits_through_the_kdb_format(tmp_path, monkeypatch, capsys):
    paths = _paths_with_logs(tmp_path, monkeypatch, {"out_rdb1.log": [LINE_INF, LINE_ERR]})
    logs.print_recent_logs(paths, "rdb1")
    out = capsys.readouterr().out
    assert "started" in out and "lost the tickerplant" in out
    # The KDB process's own millisecond timestamp, not Python's.
    assert "2026.08.22D14:21:10.644" in out
    assert "644413000" not in out


def test_follow_logs_streams_a_line_appended_after_it_starts(tmp_path, monkeypatch):
    """The live tail behind `uqs logs -f`: one `tail -F` per file,
    fanned into a queue by a thread each. None of it had ever run."""
    paths = _paths_with_logs(tmp_path, monkeypatch, {"out_rdb1.log": [LINE_INF]})
    target = tmp_path / "logs" / "out_rdb1.log"
    seen: list[dict[str, str]] = []

    def stop_after_first(log, rec, min_level):
        seen.append(rec)
        raise KeyboardInterrupt  # how a user stops it; must end cleanly

    monkeypatch.setattr(logs, "_emit", stop_after_first)

    def append_later():
        time.sleep(0.5)
        with target.open("a") as f:
            f.write(LINE_ERR + "\n")

    threading.Thread(target=append_later, daemon=True).start()
    logs.follow_logs(paths, "rdb1")

    assert len(seen) == 1
    assert seen[0]["message"] == "lost the tickerplant", (
        "tail -n 0 starts at the end: the line already in the file is not replayed"
    )


def _stop_after_first(seen: list[dict[str, str]]):
    def emit(log, rec, min_level):
        seen.append(rec)
        raise KeyboardInterrupt  # how a user stops it; must end cleanly

    return emit


def test_follow_during_reads_a_log_the_start_creates_from_its_first_line(tmp_path, monkeypatch):
    """A first run, or one after `uqs clean`: no log exists until the process
    starts, and what it prints while it loads is the part worth seeing."""
    paths = _paths_with_logs(tmp_path, monkeypatch, {})
    target = tmp_path / "logs" / "out_rdb1.log"
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(logs, "_emit", _stop_after_first(seen))

    logs.follow_during(paths, ["rdb1"], lambda: target.write_text(LINE_INF + "\n"))

    assert [rec["message"] for rec in seen] == ["started"]


def test_follow_during_is_following_before_the_start_runs(tmp_path, monkeypatch):
    """The previous run's log is followed from its end, and from BEFORE the
    start: a line written while starting is shown, the old one is not."""
    paths = _paths_with_logs(tmp_path, monkeypatch, {"out_rdb1.log": [LINE_INF]})
    target = tmp_path / "logs" / "out_rdb1.log"
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(logs, "_emit", _stop_after_first(seen))

    def start():
        time.sleep(0.5)  # the tail has the file open by now
        with target.open("a") as f:
            f.write(LINE_ERR + "\n")

    logs.follow_during(paths, ["rdb1"], start)

    assert [rec["message"] for rec in seen] == ["lost the tickerplant"]


def test_follow_logs_refuses_when_there_is_nothing_to_follow(tmp_path, monkeypatch):
    paths = _paths_with_logs(tmp_path, monkeypatch, {})
    with pytest.raises(logs.UqsError, match="has the demo been started"):
        logs.follow_logs(paths, "rdb1")


def test_a_line_below_the_minimum_level_is_not_emitted(capsys):
    log = logcore.setup_logging(level="DEBUG", colored_output=False)
    rec = logs.parse_log_line(LINE_INF)
    assert rec is not None
    logs._emit(log, rec, "WARNING")
    assert "started" not in capsys.readouterr().out


def test_a_timestamp_without_a_fraction_is_left_alone():
    """It used to split on the LAST dot, which without fractional seconds is
    the one in the date - so the whole time of day was thrown away and
    "2026.08.22D14:21:10" displayed as "2026.08.22D"."""
    assert logs._format_kdb_time("2026.08.22D14:21:10") == "2026.08.22D14:21:10"


def test_nanoseconds_are_trimmed_to_milliseconds():
    assert logs._format_kdb_time("2026.08.22D14:21:10.644413000") == "2026.08.22D14:21:10.644"


def test_a_value_that_is_not_a_timestamp_passes_through():
    assert logs._format_kdb_time("not-a-time") == "not-a-time"


# ------------------------------------ one sink, two shapes of record (`up`)


def test_the_kdb_sink_formats_uqs_records_too(capsys):
    """`up` streams kdb log lines and logs its own progress through ONE sink.

    The kdb format reads extra[kdb_time], extra[procname] and
    extra[proctype]; a record uqs logs itself has none of them. loguru
    formats with `format_map`, so a missing key raises inside the handler -
    which prints the whole record and a traceback to stderr and DROPS the
    message.

    That was live, and it hid exactly the message worth reading: starting a
    stack whose data directory needed moving printed eight tracebacks and
    not one word of the instruction explaining what to run.
    """
    from uqs.logger import get_logger
    from uqs.stack.logs import _configure_kdb_log_sink

    _configure_kdb_log_sink()
    log = get_logger(__name__)
    try:
        log.error("{}", "the data directory has moved")
        out = capsys.readouterr()
        assert "the data directory has moved" in (out.err + out.out)
        assert "KeyError" not in (out.err + out.out)
        assert "Logging error in Loguru" not in (out.err + out.out)
    finally:
        from uqs.logger.core import setup_logging

        setup_logging()


def test_the_kdb_sink_still_formats_kdb_records_as_kdb(capsys):
    """The other half: a bound record keeps the kdb layout, timestamp and
    procname first, because that is what is informative when hundreds of
    them stream past."""
    from uqs.logger import get_logger
    from uqs.logger.core import setup_logging
    from uqs.stack.logs import _configure_kdb_log_sink

    _configure_kdb_log_sink()
    log = get_logger(__name__)
    try:
        log.bind(kdb_time="2026.09.24D13:28:55.853", procname="cross1", proctype="metrics").info(
            "creating alias"
        )
        out = capsys.readouterr()
        text = out.err + out.out
        assert "2026.09.24D13:28:55.853" in text
        assert "cross1" in text and "metrics" in text
        assert "Logging error in Loguru" not in text
    finally:
        setup_logging()


def test_a_format_chooser_is_accepted_where_a_template_is():
    """_make_kv_format takes either, and keeps the key=value highlighting
    for both - the reason the chooser returns a TEMPLATE rather than a
    finished line."""
    from uqs.logger.core import _make_kv_format

    record = {"message": "budget=14", "extra": {}}
    from_template = _make_kv_format("{level} | {message}")(record)
    from_chooser = _make_kv_format(lambda _r: "{level} | {message}")(record)
    assert from_template == from_chooser
    assert "budget=" in from_chooser
