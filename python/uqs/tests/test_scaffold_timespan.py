"""The q timespan literals the scaffolder writes into declarations (#1584,
#1598): the nanoseconds q reads from each, so a check runs on the value q
will hold, not on the spelling."""

from __future__ import annotations

import pytest

from uqs.scaffold.timespan import timespan_ns

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
