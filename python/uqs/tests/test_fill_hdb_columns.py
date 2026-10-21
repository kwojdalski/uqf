"""The repair itself, against a real q and a real HDB on disk.

`test_hdb_shape.py` covers the DETECTION, over directories it makes with
mkdir - which is fast, runs anywhere, and proves nothing about whether the
bytes the filler writes can actually be read back. These drive
`scripts/gates/fill_hdb_partitions.q` against a database q wrote, and then
ask q to run the query that was failing.

That distinction matters for two things a directory of empty files cannot
show: a symbol column has to be enumerated against the HDB's sym file, and
a nested column has to end up nested. Both are silent when wrong - the
database maps and the column reads as garbage or as the wrong type.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uqs.paths import UqsPaths
from uqs.stack import hdb_fill

#: A table whose shape grew: `venue` (a symbol, so enumerated) and `bids`
#: (a list of lists, so nested) were added after the first partition.
SCHEMA = (
    "book:([] time:`timestamp$(); sym:`g#`symbol$(); px:`float$(); venue:`symbol$(); bids:())\n"
)

BUILD = """\
root:hsym `$.z.x 0;
old:([] time:3#.z.p; sym:`EUR`USD`GBP; px:3?100f);
new:([] time:2#.z.p; sym:`EUR`JPY; px:2?100f; venue:`LMAX`EBS; bids:(1 2f;3 4f));
(` sv .Q.par[root;2026.01.01;`book],`) set .Q.en[root;old];
(` sv .Q.par[root;2026.01.02;`book],`) set .Q.en[root;new];
exit 0
"""

READ = """\
system"l ",.z.x 0;
t:select from book;
-1 "rows=",string count t;
-1 "venue_type=",string type exec venue from select venue from book;
-1 "venue_vals=",.Q.s1 `$string asc exec venue from select venue from book where not null venue;
-1 "bids_old=",.Q.s1 first exec bids from select bids from book where date=2026.01.01;
exit 0
"""


def _run(
    q_binary: tuple[str, dict[str, str]], root: Path, script: str, *args: str
) -> subprocess.CompletedProcess[str]:
    q, env = q_binary
    return subprocess.run(
        [q, script, *args, "-q"],
        # closed stdin and a bound: a q that signals while loading a script
        # waits on stdin, and an inherited one would hang the test (#1513)
        stdin=subprocess.DEVNULL,
        timeout=120,
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        check=False,
    )


@pytest.fixture
def drifted_hdb(tmp_path: Path, q_binary, repo_root) -> tuple[Path, Path]:
    """An HDB whose older partition predates two columns, and the schema
    that declares them."""
    build = tmp_path / "build.q"
    build.write_text(BUILD)
    hdb = tmp_path / "hdb"
    schema = tmp_path / "database.q"
    schema.write_text(SCHEMA)
    result = _run(q_binary, repo_root, str(build), str(hdb))
    assert result.returncode == 0, result.stderr
    return hdb, schema


def test_the_query_fails_before_the_repair(drifted_hdb, q_binary, repo_root):
    """The fault this exists for, reproduced rather than assumed: kdb+
    names the missing column in the partition that lacks it."""
    hdb, _ = drifted_hdb
    read = hdb.parent / "read.q"
    read.write_text(READ)
    result = _run(q_binary, repo_root, str(read), str(hdb))
    assert "book/venue" in (result.stderr + result.stdout)


def test_the_repair_makes_the_query_run(drifted_hdb, q_binary, repo_root):
    hdb, schema = drifted_hdb
    filler = repo_root / "scripts" / "gates" / "fill_hdb_partitions.q"
    fixed = _run(q_binary, repo_root, str(filler), str(hdb), str(schema))
    assert fixed.returncode == 0, fixed.stderr
    assert "added 2 column(s)" in fixed.stdout

    read = hdb.parent / "read.q"
    read.write_text(READ)
    result = _run(q_binary, repo_root, str(read), str(hdb))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "rows=5" in out
    # 20h is an ENUMERATED symbol - a raw symbol vector would be 11h, map
    # fine, and be unreadable by anything else that opens the database.
    assert "venue_type=20" in out
    # cast back through string: `asc` stamps an `s#` attribute on its result,
    # which is about the query, not about what was written.
    assert "venue_vals=`EBS`LMAX" in out
    # The old partition's nested column must be an empty LIST, not a null.
    assert "bids_old=()" in out


def test_the_repair_is_idempotent(drifted_hdb, q_binary, repo_root):
    """Every uqs command bootstraps, so this runs constantly. A second
    run must find nothing rather than appending a column a second time."""
    hdb, schema = drifted_hdb
    filler = repo_root / "scripts" / "gates" / "fill_hdb_partitions.q"
    assert _run(q_binary, repo_root, str(filler), str(hdb), str(schema)).returncode == 0
    again = _run(q_binary, repo_root, str(filler), str(hdb), str(schema))
    assert again.returncode == 0, again.stderr
    assert "already rectangular - nothing to do" in again.stdout


def test_an_empty_hdb_is_a_no_op(tmp_path: Path, q_binary, repo_root):
    """A fresh install's HDB exists and holds no partition. That is already
    whole: exit 0, say so, write nothing. It used to signal 'type in the
    final report, and q then sat on stdin, hanging the bootstrap (#1513)."""
    hdb = tmp_path / "hdb"
    hdb.mkdir()
    schema = tmp_path / "database.q"
    schema.write_text(SCHEMA)
    filler = repo_root / "scripts" / "gates" / "fill_hdb_partitions.q"
    result = _run(q_binary, repo_root, str(filler), str(hdb), str(schema))
    assert result.returncode == 0, result.stderr
    assert "already rectangular - nothing to do" in result.stdout
    assert result.stderr == ""
    assert list(hdb.iterdir()) == []


def _wrapper_paths(root: Path) -> UqsPaths:
    """A path bundle with the HDB and the filler present, so the wrapper
    reaches its subprocess call."""
    paths = UqsPaths(
        repo_root=root,
        torqhome=root / "lib" / "torq",
        torqapphome=root / "lib" / "torq-finance-starter-pack",
        torqdata=root / "output" / "uqs",
        scripts_dir=root / "scripts",
        orchestrator_dir=root / "python" / "uqs",
    )
    paths.hdb_dir.mkdir(parents=True)
    script = paths.scripts_dir / hdb_fill.FILL_HDB_SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text("exit 0\n")
    return paths


def test_the_wrapper_closes_stdin_and_survives_a_hang(tmp_path: Path, monkeypatch):
    """Every uqs command bootstraps through this, so a filler that never
    exits must neither inherit a terminal to wait on nor block forever: it
    is run with stdin closed and a timeout, and a timeout is a warning."""
    paths = _wrapper_paths(tmp_path)
    seen: dict[str, object] = {}

    def hangs(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(hdb_fill, "with_interpreter", lambda _paths: {})
    monkeypatch.setattr(hdb_fill, "q_interpreter", lambda _env: Path("/usr/bin/q"))
    monkeypatch.setattr(hdb_fill.subprocess, "run", hangs)

    assert hdb_fill.fill_hdb_partitions(paths) is False
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["timeout"] == hdb_fill.FILL_HDB_TIMEOUT_S
