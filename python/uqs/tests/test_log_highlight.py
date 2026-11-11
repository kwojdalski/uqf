"""The shared console highlighter: vectors, symbols, ids and timestamps (#2048).

`uqs.logger.highlight` is what colours every console log line - `uqs logs`, `start -f`,
and through them a `job backfill --trace` run's query lines. It used to colour
only the first word after `=`, so the rest of a vector took the level's colour.

Two kinds of test. The markup tests say which colour a token gets. The
rendering tests push a message through a real loguru sink, with colour on and
with colour off, and require the text a reader sees to be the message itself,
byte for byte: a highlighter that drops, adds or reorders a single character
is wrong however pretty it is, and one whose markup loguru cannot parse drops
the whole line.
"""

from __future__ import annotations

import io
import json

import pytest
from loguru import logger
from rich.text import Text

# Aliased `logcore`, as in test_logger.py: test_module_split.py reads `core.<name>`
# in a test file as a use of the orchestrator's `core` facade.
from uqs.logger import core as logcore
from uqs.logger.highlight import highlight

hl = highlight


def _render(message: str, *, colorize: bool, template: str = "<level>{message}</level>") -> str:
    """`message` as one loguru sink writes it through the shared format."""
    sink = io.StringIO()
    hid = logger.add(
        sink,
        format=logcore._make_kv_format(template),
        colorize=colorize,
        filter=lambda r: r["extra"].get("_hl_test") is True,
    )
    try:
        logger.bind(_hl_test=True).info(message)
    finally:
        logger.remove(hid)
    return sink.getvalue()


# ------------------------------------------------------------------ vectors


def test_every_element_of_a_numeric_vector_is_coloured():
    assert hl("sizes=1000000 2e+07 -2.5e-07") == (
        "sizes=<magenta>1000000</magenta> <magenta>2e+07</magenta> <red>-2.5e-07</red>"
    )


def test_a_vector_of_timestamps_is_coloured_element_by_element():
    out = hl("window=2026.11.02D10:00:00.000000000 2026.11.02D11:00:00.000000000")
    assert out.count("<green>") == 2


def test_a_vector_stops_at_the_next_key_and_at_prose():
    # `rows` is a word, not a vector element: prose after a value stays plain.
    assert hl("n=5 rows written") == "n=<magenta>5</magenta> rows written"
    assert hl("a=1 2 b=3") == ("a=<magenta>1</magenta> <magenta>2</magenta> b=<magenta>3</magenta>")


def test_a_string_value_does_not_swallow_the_words_after_it():
    assert hl("sym=EURUSD 5 rows") == "sym=<light-cyan>EURUSD</light-cyan> 5 rows"


def test_every_element_of_a_nested_list_is_coloured():
    out = hl("x=((`a;1);(`b;-2))")
    assert out == (
        "x=((<yellow>`a</yellow>;<magenta>1</magenta>);(<yellow>`b</yellow>;<red>-2</red>))"
    )


def test_a_list_of_vectors_colours_each_element():
    assert hl("m=(1 2;3 4)") == (
        "m=(<magenta>1</magenta> <magenta>2</magenta>;<magenta>3</magenta> <magenta>4</magenta>)"
    )


def test_an_unbalanced_parenthesis_is_not_a_list():
    # Read as the old single value would be; nothing is lost.
    assert "(1;2" in hl("x=(1;2 and more")


# ------------------------------------------------------------------ symbols


def test_a_symbol_value_is_yellow():
    assert hl("worker=`piggybank_spot_markouts_backfill") == (
        "worker=<yellow>`piggybank_spot_markouts_backfill</yellow>"
    )


def test_each_element_of_an_adjacent_symbol_vector_is_yellow():
    assert hl("symbols=`EURUSD`GBPUSD") == (
        "symbols=<yellow>`EURUSD</yellow><yellow>`GBPUSD</yellow>"
    )


def test_a_symbol_in_running_text_is_yellow():
    assert hl("starting `rdb1 now") == "starting <yellow>`rdb1</yellow> now"


def test_a_backticked_command_is_prose_not_a_symbol():
    # uqs's own messages quote commands markdown-style; those are not q symbols.
    for message in ("run `uqs start` first", "see `uqs logs rdb1` for its log", "use `uqs`."):
        assert hl(message) == message


# ------------------------------------------------------- uuids and hashes


@pytest.mark.parametrize(
    "value",
    [
        "ceff48fe-c862-1bcf-e2e2-4d5b8fb8256f",
        "CEFF48FE-C862-1BCF-E2E2-4D5B8FB8256F",
        "d41d8cd98f00b204e9800998ecf8427e",  # md5, 32
        "da39a3ee5e6b4b0d3255bfef95601890afd80709",  # sha1, 40
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # sha256, 64
    ],
)
def test_a_uuid_or_digest_is_light_blue(value):
    assert hl(f"run={value}") == f"run=<light-blue>{value}</light-blue>"
    assert hl(f"run {value} done") == f"run <light-blue>{value}</light-blue> done"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # A long all-digit value is a number, whatever its length.
        (
            "id=12345678901234567890123456789012",
            "id=<magenta>12345678901234567890123456789012</magenta>",
        ),
        # Hex-looking words are words.
        ("tag=deadbeef", "tag=<light-cyan>deadbeef</light-cyan>"),
        ("tag=cafebabe", "tag=<light-cyan>cafebabe</light-cyan>"),
        # 32 letters a-f and no digit: a word, not a digest.
        (
            "w=abcdefabcdefabcdefabcdefabcdefab",
            "w=<light-cyan>abcdefabcdefabcdefabcdefabcdefab</light-cyan>",
        ),
        # Wrong lengths.
        (
            "h=d41d8cd98f00b204e9800998ecf8427",
            "h=<light-cyan>d41d8cd98f00b204e9800998ecf8427</light-cyan>",
        ),
        (
            "h=d41d8cd98f00b204e9800998ecf8427e0",
            "h=<light-cyan>d41d8cd98f00b204e9800998ecf8427e0</light-cyan>",
        ),
        # Mixed case is not how a digest is printed.
        (
            "h=D41d8cd98f00b204e9800998ecf8427e",
            "h=<light-cyan>D41d8cd98f00b204e9800998ecf8427e</light-cyan>",
        ),
    ],
)
def test_what_is_not_a_digest_keeps_its_old_colour(message, expected):
    assert hl(message) == expected


def test_a_digest_inside_a_path_or_a_longer_word_is_not_coloured_on_its_own():
    digest = "d41d8cd98f00b204e9800998ecf8427e"
    assert hl(f"wrote /tmp/{digest}.log ok") == f"wrote /tmp/{digest}.log ok"
    assert hl(f"key x{digest} ok") == f"key x{digest} ok"


def test_an_all_digit_word_in_running_text_is_not_coloured():
    assert hl("rows 12345678901234567890123456789012 in") == (
        "rows 12345678901234567890123456789012 in"
    )


# ------------------------------------------------------- dates and times


@pytest.mark.parametrize(
    "value",
    [
        "2026.11.04D13:14:56.078",
        "2026.11.02D10:00:00.000000000",
        "2026.11.04D13:14:56.078123456",
        "2026.11.04D13:14:56",
        "2026.11.04",
        "2026.11.04T13:14:56.078",
        "2026-11-04T13:14:56.078Z",
        "2026-11-04",
        "0D00:00:01.500000000",
        "13:14:56.078",
        "2026.11m",
    ],
)
def test_a_date_or_timestamp_is_green_with_its_precision_kept(value):
    assert hl(f"t={value}") == f"t=<green>{value}</green>"
    assert hl(f"at {value} ok") == f"at <green>{value}</green> ok"


def test_the_issues_example_reads_as_described():
    message = (
        "worker=`piggybank_spot_markouts_backfill "
        "range_from=2026.11.02D10:00:00.000000000 "
        "run=ceff48fe-c862-1bcf-e2e2-4d5b8fb8256f "
        "symbols=`EURUSD`GBPUSD "
        "sizes=1000000 2e+07 -2.5e-07"
    )
    assert hl(message) == (
        "worker=<yellow>`piggybank_spot_markouts_backfill</yellow> "
        "range_from=<green>2026.11.02D10:00:00.000000000</green> "
        "run=<light-blue>ceff48fe-c862-1bcf-e2e2-4d5b8fb8256f</light-blue> "
        "symbols=<yellow>`EURUSD</yellow><yellow>`GBPUSD</yellow> "
        "sizes=<magenta>1000000</magenta> <magenta>2e+07</magenta> <red>-2.5e-07</red>"
    )


# ------------------------------------------------------------------ quotes


def test_a_quoted_value_is_one_string_token():
    assert hl('msg="a b `c d=1"') == 'msg=<light-cyan>"a b `c d=1"</light-cyan>'


def test_text_inside_quotes_in_running_text_is_left_alone():
    message = 'query "select from t where sym=`EURUSD, d=2026.11.04" sent n=1'
    assert hl(message) == (
        'query "select from t where sym=`EURUSD, d=2026.11.04" sent n=<magenta>1</magenta>'
    )


def test_an_escaped_quote_does_not_end_the_string():
    message = r'say "a \" b=`c" x=`d'
    assert hl(message) == r'say "a \" b=`c" x=<yellow>`d</yellow>'


def test_a_quoted_list_element_is_one_token():
    assert hl('x=(1;"a b";`c)') == (
        'x=(<magenta>1</magenta>;<light-cyan>"a b"</light-cyan>;<yellow>`c</yellow>)'
    )


# ------------------------------------------------- old colours unchanged


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ok=True", "ok=<green>True</green>"),
        ("ok=False", "ok=<red>False</red>"),
        ("value=None", "value=<dim>None</dim>"),
        ("rows=11,643", "rows=<magenta>11,643</magenta>"),
        ("step=96921/969218", "step=<magenta>96921/969218</magenta>"),
        ("latency=45ms", "latency=<magenta>45ms</magenta>"),
        ("fill=98.5%", "fill=<magenta>98.5%</magenta>"),
        ("file=/tmp/x.log", "file=<bold>/tmp/x.log</bold>"),
        ("plain message, no pairs", "plain message, no pairs"),
    ],
)
def test_scalar_colours_are_as_before(message, expected):
    assert hl(message) == expected


# --------------------------------------- q-typed values from .Q.s1 (#2079)
#
# `.qetl.log.render` writes every value with `.Q.s1`, which spells many atoms
# with a type letter. Each spelling below is what KDB-X's .Q.s1 prints.


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("code=-187i", "code=<red>-187i</red>"),
        ("n=5j", "n=<magenta>5j</magenta>"),
        ("w=100h", "w=<magenta>100h</magenta>"),
        ("px=1.5e", "px=<magenta>1.5e</magenta>"),
        ("px=1e", "px=<magenta>1e</magenta>"),
        ("px=1.5e+10e", "px=<magenta>1.5e+10e</magenta>"),
        ("q=2f", "q=<magenta>2f</magenta>"),
        ("q=-2.5f", "q=<red>-2.5f</red>"),
    ],
)
def test_a_number_with_a_q_type_letter_is_a_number(message, expected):
    assert hl(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("x=0N", "x=<magenta>0N</magenta>"),
        ("x=0n", "x=<magenta>0n</magenta>"),
        ("x=0Ni", "x=<magenta>0Ni</magenta>"),
        ("x=0Ne", "x=<magenta>0Ne</magenta>"),
        ("x=0Np", "x=<magenta>0Np</magenta>"),
        ("x=0Nn", "x=<magenta>0Nn</magenta>"),
        ("x=0Nd", "x=<magenta>0Nd</magenta>"),
        ("x=0w", "x=<magenta>0w</magenta>"),
        ("x=0W", "x=<magenta>0W</magenta>"),
        ("x=0we", "x=<magenta>0we</magenta>"),
        ("x=0Wp", "x=<magenta>0Wp</magenta>"),
        ("x=-0w", "x=<red>-0w</red>"),
        ("x=-0Wi", "x=<red>-0Wi</red>"),
    ],
)
def test_a_q_null_or_infinity_is_a_number(message, expected):
    assert hl(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ok=1b", "ok=<green>1b</green>"),
        ("ok=0b", "ok=<red>0b</red>"),
        ("flags=111b", "flags=<green>111b</green>"),
        ("flags=000b", "flags=<red>000b</red>"),
        ("flags=101b", "flags=<magenta>101b</magenta>"),
    ],
)
def test_a_q_boolean_is_coloured_like_true_and_false(message, expected):
    assert hl(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("sizes=1 2 3i", "sizes=<magenta>1</magenta> <magenta>2</magenta> <magenta>3i</magenta>"),
        ("w=1 2 3h", "w=<magenta>1</magenta> <magenta>2</magenta> <magenta>3h</magenta>"),
        ("px=1.5 2e", "px=<magenta>1.5</magenta> <magenta>2e</magenta>"),
        ("x=1 2 0Ni", "x=<magenta>1</magenta> <magenta>2</magenta> <magenta>0Ni</magenta>"),
        ("x=-0w 1 2e", "x=<red>-0w</red> <magenta>1</magenta> <magenta>2e</magenta>"),
    ],
)
def test_the_last_element_of_a_typed_q_vector_is_coloured(message, expected):
    assert hl(message) == expected


def test_the_kafka_feed_line_from_the_issue():
    line = 'stage=`bootstrap kind=`all_down endpoint="" code=-187i error="1/1 brokers are down"'
    assert "code=<red>-187i</red>" in hl(line)


@pytest.mark.parametrize(
    "value",
    # Words that merely end in a type letter, q bytes, letters q does not
    # put after a number, and nulls of a type .Q.s1 never prints that way.
    [
        "fib",
        "ab",
        "b",
        "i",
        "0x01",
        "0x0102",
        "2b",
        "1bb",
        "12b3",
        "1d",
        "5x",
        "12abc",
        "1ii",
        "0Nx",
        "0Ng",
        "0N1",
        "N",
        "e",
        "1e+",
        "-i",
        "--1i",
        "0bb",
    ],
)
def test_what_only_looks_q_typed_stays_a_string(value):
    assert hl(f"v={value}") == f"v=<light-cyan>{value}</light-cyan>"


def test_a_q_typed_atom_inside_a_list_is_coloured():
    assert hl("x=(0N;1.5e;1b)") == (
        "x=(<magenta>0N</magenta>;<magenta>1.5e</magenta>;<green>1b</green>)"
    )


def test_q_types_are_not_coloured_in_running_text():
    # As with other numbers, only a value after `=` is a number.
    assert hl("took 5h and 1b") == "took 5h and 1b"


# --------------------------------------------- the text survives exactly

_EXACT = [
    "code=-187i sizes=1 2 3i x=-0w ok=1b f=101b q=(0N;1.5e;`a) v=0x01 fib=ab",
    "worker=`piggybank range_from=2026.11.02D10:00:00.000000000 symbols=`EURUSD`GBPUSD",
    "sizes=1000000 2e+07 -2.5e-07 run=ceff48fe-c862-1bcf-e2e2-4d5b8fb8256f",
    'msg="a b `c d=1" x=((`a;1);(`b;-2))',
    r'say "a \" b=`c" x=`d',
    "markup [/x] [bold]not bold[/bold] [red]x[/] y=[1]",
    "loguru-like </x> <red>red</red> <level> a<b c>d",
    "escaped \\<red> and \\\\<b> v=\\",
    "ends in a backslash \\",
    "a backslash before a newline \\\n",
    "k=v\\",
    "path=C:\\tmp\\x.q n=1\\",
    'example="DRIVER=x;SERVER=<host>;PORT=3306"',
    "braces {not_a_field} {0} n={1}",
    'unterminated "quote x=`a',
    "tabs\tand\nnewlines x=1\n    {[a;b] select from t where sym=`EURUSD}",
    "run `uqs logs rdb1` for its log, hash=d41d8cd98f00b204e9800998ecf8427e",
    "x=(1;2 unbalanced ( and ) parens) y=`a`b`",
    "unicode café=€1 sym=`naïve",
    "",
]


@pytest.mark.parametrize("message", _EXACT)
@pytest.mark.parametrize("colorize", [True, False])
def test_the_rendered_text_is_the_message_byte_for_byte(message, colorize):
    out = _render(message, colorize=colorize)
    assert "Logging error" not in out
    assert Text.from_ansi(out).plain == message + "\n"
    if not colorize:
        assert out == message + "\n"


@pytest.mark.parametrize("message", _EXACT)
def test_the_text_survives_a_template_with_no_tag_after_the_message(message):
    # A trailing backslash means something different when a tag follows it.
    out = _render(message, colorize=False, template="{level} - {message}")
    assert out == f"INFO - {message}\n"


def test_colour_on_does_colour():
    out = _render("symbols=`EURUSD`GBPUSD t=2026.11.04", colorize=True)
    assert "\x1b[33m`EURUSD" in out and "\x1b[33m`GBPUSD" in out  # yellow
    assert "\x1b[32m2026.11.04" in out  # green


def test_markup_like_text_renders_literally_through_rich():
    # `uqs logs` output is plain text with ANSI codes, never Rich markup.
    out = _render("note [/x] closes nothing", colorize=True)
    assert Text.from_ansi(out).plain == "note [/x] closes nothing\n"


# ----------------------------------- sinks other than the coloured console


def test_no_color_writes_the_message_without_escape_codes(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    sink = io.StringIO()
    logcore.setup_logging(level="INFO", format_string="{message}", stream=sink)
    logger.info("symbols=`EURUSD`GBPUSD n=1 2")
    assert sink.getvalue() == "symbols=`EURUSD`GBPUSD n=1 2\n"


def test_redirected_output_is_plain(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    sink = io.StringIO()  # not a terminal
    logcore.setup_logging(level="INFO", format_string="<level>{message}</level>", stream=sink)
    logger.info("t=2026.11.04D13:14:56.078 h=d41d8cd98f00b204e9800998ecf8427e")
    assert sink.getvalue() == "t=2026.11.04D13:14:56.078 h=d41d8cd98f00b204e9800998ecf8427e\n"


def test_a_stored_log_holds_the_message_unhighlighted(tmp_path):
    log_file = tmp_path / "u.log"
    logcore.setup_logging(level="INFO", log_file=str(log_file), console_output=False)
    logger.info("symbols=`EURUSD`GBPUSD <red> [/x]")
    logger.remove()
    text = log_file.read_text()
    assert "symbols=`EURUSD`GBPUSD <red> [/x]" in text
    assert "\x1b[" not in text


def test_structured_logging_carries_the_message_unchanged():
    sink = io.StringIO()
    logcore.setup_logging(level="INFO", structured_logging=True, stream=sink)
    logger.info("symbols=`EURUSD`GBPUSD n=1 2")
    record = json.loads(sink.getvalue())
    assert record["record"]["message"] == "symbols=`EURUSD`GBPUSD n=1 2"
    assert "\x1b[" not in record["text"]


def test_uqs_logs_and_a_backfill_trace_share_the_highlighter(monkeypatch, capsys):
    """`uqs logs` (and `start -f`) print through logs.log_sink; a
    `job backfill --trace` run's query lines reach the reader that way."""
    from uqs.stack import logs

    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    sink = logs.log_sink()
    message = (
        'query sent call="{[s] select from t where sym in s}" '
        "range_from=2026.11.02D10:00:00.000000000 symbols=`EURUSD`GBPUSD"
    )
    rec = {
        "time": "2026.11.04D13:14:56.078123000",
        "procname": "bf1",
        "proctype": "backfill",
        "loglevel": "TRACE",
        "message": message,
    }
    logs.emit(sink, rec, None)
    out = capsys.readouterr().out
    assert "\x1b[33m`EURUSD" in out and "\x1b[32m2026.11.02D10:00:00.000000000" in out
    plain = Text.from_ansi(out).plain
    assert "symbols=`EURUSD`GBPUSD" in plain
    assert "    {[s] select from t where sym in s}" in plain


def test_random_messages_from_the_awkward_characters_survive_exactly():
    """Seeded, so a failure reproduces: every character the highlighter or
    loguru treats specially, in random order, colour on and off."""
    import random

    rng = random.Random(2048)
    alphabet = list('ab1-.:=`"\\<>/(); []{}!,|\t\nD') + [
        "2026.11.04D13:14:56.078",
        "d41d8cd98f00b204e9800998ecf8427e",
        "`EURUSD",
        "</level>",
        "<red>",
        " x=",
    ]
    for _ in range(400):
        message = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        for colorize in (True, False):
            out = _render(message, colorize=colorize)
            assert Text.from_ansi(out).plain == message + "\n", (message, out)
