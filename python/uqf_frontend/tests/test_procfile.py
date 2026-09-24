"""Reading and resolving TorQ's generated process.csv."""

from __future__ import annotations

import pytest

from uqf_frontend.procfile import DeclaredProcess, read, resolve_port

HEADER = "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd\n"


def write_csv(tmp_path, *rows: str):
    p = tmp_path / "process.csv"
    p.write_text(HEADER + "".join(r + "\n" for r in rows))
    return p


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{KDBBASEPORT}", 6050),
        ("{KDBBASEPORT}+1", 6051),
        ("{KDBBASEPORT}+31", 6081),
        ("{KDBBASEPORT}-2", 6048),
        ("7000", 7000),
        ("", None),
        ("{SOMETHING_ELSE}", 6050),
        ("garbage", None),
        ("{KDBBASEPORT}+notanumber", None),
    ],
)
def test_port_placeholder_resolution(raw, expected):
    assert resolve_port(raw, 6050) == expected


def test_an_unresolvable_port_returns_none_rather_than_raising():
    """One bad row must not make the whole fleet view unavailable."""
    assert resolve_port("!!!", 6050) is None


def test_rows_are_parsed_with_ports_resolved(tmp_path):
    path = write_csv(
        tmp_path,
        "localhost,{KDBBASEPORT}+1,discovery,discovery1,,1,0,,,x.q,1,,q",
        "localhost,{KDBBASEPORT}+31,metrics,markout1,,0,0,,,y.q,1,,q",
    )
    procs = read(path, 6050)
    assert [(p.procname, p.port, p.proctype) for p in procs] == [
        ("discovery1", 6051, "discovery"),
        ("markout1", 6081, "metrics"),
    ]


def test_startwithall_is_parsed(tmp_path):
    path = write_csv(
        tmp_path,
        "localhost,{KDBBASEPORT}+28,metrics,tap1,,1,0,,,x.q,0,,q",
        "localhost,{KDBBASEPORT}+1,metrics,cross1,,1,0,,,y.q,1,,q",
    )
    by_name = {p.procname: p for p in read(path, 6050)}
    assert by_name["tap1"].start_with_all is False
    assert by_name["cross1"].start_with_all is True


def test_rows_without_a_procname_are_skipped(tmp_path):
    path = write_csv(tmp_path, "localhost,{KDBBASEPORT},hdb,,,1,0,,,x.q,1,,q")
    assert read(path, 6050) == []


def test_group_is_the_proctype():
    p = DeclaredProcess("rdb1", "rdb", "localhost", 6052, True)
    assert p.group == "rdb"


def test_a_missing_file_raises_with_the_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="process.csv"):
        read(tmp_path / "nope.csv", 6050)


def test_the_real_generated_process_csv_parses():
    """Parse the file this repo actually generates, not just a fixture - a
    schema change upstream should fail here rather than in production.
    """
    from pathlib import Path

    real = Path(__file__).resolve().parents[3] / "output" / "uqs" / "process.csv"
    if not real.is_file():
        pytest.skip(f"no generated process.csv at {real}; run uqs bootstrap")
    procs = read(real, 6050)
    assert len(procs) > 10
    assert all(p.procname for p in procs)
    assert {"metrics", "hdb"} <= {p.proctype for p in procs}
    assert all(p.port is not None for p in procs), "every generated row should resolve"
