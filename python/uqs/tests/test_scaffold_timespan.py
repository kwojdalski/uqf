"""The q timespan literals the scaffolder writes into declarations (#1584,
#1598): the nanoseconds q reads from each, so a check runs on the value q
will hold, not on the spelling."""

from __future__ import annotations

import pytest

from uqs.paths import UqsError
from uqs.scaffold import jobs, worker
from uqs.scaffold.timespan import q_literal, q_timespan, render, timespan_ns

DAY = 86_400_000_000_000


@pytest.mark.parametrize(
    ("literal", "ns"),
    [
        ("0D00:00:10", 10_000_000_000),
        ("0D00:01", 60_000_000_000),
        ("1D00:00", DAY),
        ("00D00:00:01", 1_000_000_000),
        ("0D00:00:00.5", 500_000_000),
        ("0D00:00:00.000000001", 1),
        # q keeps nanoseconds and drops what is finer: these are zero.
        ("0D00:00:00.0000000001", 0),
        ("0D00:00:00.1234567896", 123_456_789),
        ("0D23:59:59", DAY - 1_000_000_000),
        # the forms --width took, now read by the one check (#2121)
        ("1D", DAY),
        ("0D01", 3_600_000_000_000),
        ("0D01:30", 5_400_000_000_000),
    ],
)
def test_it_reads_the_nanoseconds_q_reads(literal, ns):
    assert timespan_ns(literal) == ns


@pytest.mark.parametrize(
    "literal",
    ["10s", "0D0:00:01", "0D00:00:00.", "0D00:00:00 ", "0D00:00:01\n", "D00:00:01", ""],
)
def test_a_literal_q_would_not_parse_as_written_is_none(literal):
    assert timespan_ns(literal) is None


# The table above is what KDB-X does; this proves it against the interpreter,
# so a q upgrade that changes it fails here and not in a generated file.
AGREED = [
    "0D00:00:10",
    "0D00:01",
    "1D00:00",
    "00D00:00:01",
    "0D00:00:00.5",
    "0D00:00:00.000000001",
    "0D00:00:00.0000000001",
    "0D00:00:00.1234567896",
    "0D23:59:59",
    "106751D23:47:16.854775806",
    "1D",
    "0D01",
]


def test_q_reads_each_literal_as_the_same_nanoseconds(tmp_path):
    import subprocess

    from uqs.interpreter import identify, q_interpreter

    q = q_interpreter()
    if q is None or identify(q) != "kdbx":
        pytest.skip("needs KDB-X: set $QCMD, or put q on PATH")
    script = tmp_path / "ns.q"
    script.write_text("".join(f'-1 string "j"$value "{lit}";\n' for lit in AGREED) + "exit 0\n")
    out = subprocess.run(
        [str(q), str(script), "-q"], capture_output=True, text=True, timeout=60, check=True
    ).stdout.split()
    assert [int(n) for n in out] == [timespan_ns(lit) for lit in AGREED]


# --- a DURATION, rendered as the q timespan it is (#2121) -------------------

RENDERED = [
    ("5s", "0D00:00:05"),
    ("500ms", "0D00:00:00.500"),
    ("1.5m", "0D00:01:30"),
    ("30", "0D00:00:30"),
    ("1d", "1D00:00:00"),
    ("25h", "1D01:00:00"),
    ("0.000001s", "0D00:00:00.000001"),
    ("0.000000001", "0D00:00:00.000000001"),
]


@pytest.mark.parametrize(("value", "literal"), RENDERED)
def test_a_duration_becomes_the_exact_q_timespan(value, literal):
    assert q_literal(value) == literal


@pytest.mark.parametrize("literal", ["1D", "0D01", "0D00:00:05", "0D00:00:00.500"])
def test_a_q_timespan_is_kept_as_written(literal):
    assert q_literal(literal) == literal


@pytest.mark.parametrize("value", ["1D", "5s", "0D00:00:05"])
def test_period_and_width_take_the_same_values(value):
    """--width took 1D and --period refused it; neither took 5s (#2121)."""
    assert jobs.check_period(value) == worker.check_width(value) == q_literal(value)


@pytest.mark.parametrize(
    ("value", "said"),
    [
        ("0s", "must be positive"),
        ("0D00:00:00", "must be positive"),
        ("soon", "a DURATION"),
        ("5 s", "a DURATION"),
        ("0D24:00", "a DURATION"),
        ("106752d", "no longer than q's largest timespan"),
    ],
)
def test_each_option_refuses_naming_itself_and_the_forms(value, said):
    for option in ("--period", "--horizon", "--width"):
        with pytest.raises(UqsError, match=f"^{option} {value!r}.*{said}"):
            q_timespan(value, option)


def test_q_reads_each_rendered_literal_as_the_duration(tmp_path):
    import subprocess

    from uqs import duration
    from uqs.interpreter import identify, q_interpreter

    q = q_interpreter()
    if q is None or identify(q) != "kdbx":
        pytest.skip("needs KDB-X: set $QCMD, or put q on PATH")
    script = tmp_path / "ns.q"
    script.write_text(
        "".join(f'-1 string "j"$value "{lit}";\n' for _, lit in RENDERED) + "exit 0\n"
    )
    out = subprocess.run(
        [str(q), str(script), "-q"], capture_output=True, text=True, timeout=60, check=True
    ).stdout.split()
    assert [int(n) for n in out] == [duration.nanoseconds(v) for v, _ in RENDERED]
    assert all(render(timespan_ns(lit) or 0) == lit for _, lit in RENDERED)
