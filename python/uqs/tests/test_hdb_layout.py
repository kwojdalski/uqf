"""`uqs data check` judges the partitions q discovers, segmented or not (#1783).

The check used to find partitions by listing the HDB root's date-named
directories. A segmented database keeps none there: its root holds `par.txt`,
which names the segment directories, and the partitions live in those. So the
listing saw nothing and the check reported every partition whole - whatever
the segments held - and an invalid segment was never mentioned.

Each fixture here is built by q, then loaded by q, and the check is held to
what q reports (`.Q.pv`, `.Q.pd`, `tables[]`): q is the authority on which
partitions a database has, so the check may not have its own opinion.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import hdb_check
from uqs.paths import UqsPaths

ROOT = Path(__file__).resolve().parents[3]

#: What every fixture's runtime declares: quote everywhere, trade only on
#: some dates and segments, so a table gap is something real to find.
SCHEMA = "quote:([] sym:`symbol$(); bid:`float$())\ntrade:([] sym:`symbol$(); px:`float$())\n"

#: Builds every fixture under argv[0], one directory each, the HDB root at
#: <fixture>/hdb and any segment beside it at <fixture>/s1, s2 ...
BUILD = r"""
base:first .z.x;
q1:([] sym:`a`b; bid:1 2f);
t1:([] sym:enlist `a; px:enlist 3f);
path:{[x] hsym `$base,"/",x};
/ table t for date d into dir (the root or a segment), enumerated against the root
put:{[fx;dir;d;t;data] (` sv (path fx,"/",dir),(`$string d),t,`) set .Q.en[path fx,"/hdb"] data};
put["conv";"hdb";2026.01.01;`quote;q1];
put["conv";"hdb";2026.01.02;`quote;q1];
put["conv";"hdb";2026.01.02;`trade;t1];
/ two segments: trade only in s2's 2026.01.02, so s1's 2026.01.01 and s2's 2026.01.03 lack it
segmented:{[fx;extra]
    put[fx;"s1";2026.01.01;`quote;q1];
    put[fx;"s2";2026.01.02;`quote;q1];
    put[fx;"s2";2026.01.02;`trade;t1];
    put[fx;"s2";2026.01.03;`quote;q1];
    (` sv (path fx,"/hdb"),`par.txt) 0: (base,"/",fx,"/s1";base,"/",fx,"/s2"),extra};
segmented["seg";()];
/ one date in both segments: s1's copy of 2026.01.02 lacks trade
segmented["split";()];
put["split";"s1";2026.01.02;`quote;q1];
/ par.txt names a segment that does not exist: q loads the others, silently
segmented["missing";enlist base,"/missing/s3"];
/ par.txt names a segment q may not read (made unreadable by the test)
segmented["locked";enlist base,"/locked/s3"];
put["locked";"s3";2026.01.04;`quote;q1];
/ partitions in the root AND par.txt: q refuses to load it ('part)
segmented["stale";()];
put["stale";"hdb";2025.12.31;`quote;q1];
system "mkdir -p ",base,"/empty/hdb";
exit 0
"""

#: q's own view of a database: load it, then .Q.pv, .Q.pd and tables[], as
#: JSON on one line - or the load's error.
ORACLE = r"""
r:first .z.x;
err:@[{system "l ",x; ""};r;{[e] e}];
if[count err; -1 .j.j enlist[`error]!enlist err; exit 0];
pv:@[value;`.Q.pv;{[e] ()}];
pd:@[value;`.Q.pd;{[e] ()}];
-1 .j.j `pv`pd`tables!(string pv;{[d] 1_string d} each pd;string tables[]);
exit 0
"""


def _q(q_binary, script: str, *args: str, tmp: Path) -> subprocess.CompletedProcess[str]:
    q, env = q_binary
    file = tmp / "script.q"
    file.write_text(script)
    return subprocess.run(
        [q, str(file), *args, "-q"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )


@pytest.fixture
def fixtures(tmp_path: Path, q_binary) -> Iterator[Path]:
    base = tmp_path / "fx"
    base.mkdir()
    built = _q(q_binary, BUILD, str(base), tmp=tmp_path)
    assert built.returncode == 0, built.stdout + built.stderr
    locked = base / "locked" / "s3"
    locked.chmod(0)
    yield base
    locked.chmod(0o755)


def q_view(q_binary, hdb: Path, tmp: Path) -> dict:
    """What q discovers in `hdb`: pv, pd and tables, or the load's error."""
    run = _q(q_binary, ORACLE, str(hdb), tmp=tmp)
    return json.loads(run.stdout.strip().splitlines()[-1])


def _paths(base: Path, fixture: str, tmp: Path, schema: str) -> UqsPaths:
    app = tmp / "app"
    app.mkdir(exist_ok=True)
    (app / "database.q").write_text(schema)
    return UqsPaths(
        repo_root=ROOT,
        torqhome=tmp / "torq",
        torqapphome=app,
        torqdata=base / fixture,
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=tmp / "orchestrator",
        runtime="torq",
    )


def data_check(
    monkeypatch, base: Path, fixture: str, tmp: Path, q_binary, schema: str = SCHEMA
) -> tuple[int, dict]:
    """`uqs data check --json` against one fixture, as this runtime's HDB."""
    paths = _paths(base, fixture, tmp, schema)
    monkeypatch.setattr(hdb_check, "_paths", lambda: paths)
    q, env = q_binary
    monkeypatch.setenv("QCMD", q)
    monkeypatch.setenv("QHOME", env["QHOME"])
    result = CliRunner().invoke(cli.app, ["data", "check", "--json"], env={"COLUMNS": "300"})
    out = result.stdout
    return result.exit_code, json.loads(out[out.index("{") :])


def _snapshot(base: Path) -> dict[str, tuple[int, int]]:
    """Every readable file and directory under `base`: its size and mtime."""
    seen = {}
    for dirpath, dirnames, filenames in os.walk(base):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = p.lstat()
            seen[str(p.relative_to(base))] = (st.st_size, st.st_mtime_ns)
    return seen


# ---------------------------------------------------------------- q's view


def test_the_fixtures_are_what_q_says_they_are(fixtures, q_binary, tmp_path):
    """The oracle itself: each layout loads, or is refused, as described."""
    conv = q_view(q_binary, fixtures / "conv" / "hdb", tmp_path)
    assert conv["pv"] == ["2026.01.01", "2026.01.02"]
    assert conv["tables"] == ["quote", "trade"]
    seg = q_view(q_binary, fixtures / "seg" / "hdb", tmp_path)
    assert seg["pv"] == ["2026.01.01", "2026.01.02", "2026.01.03"]
    assert [Path(d).name for d in seg["pd"]] == ["s1", "s2", "s2"]
    # the latest partition lacks trade, so q's table list does too
    assert seg["tables"] == ["quote"]
    split = q_view(q_binary, fixtures / "split" / "hdb", tmp_path)
    assert split["pv"] == ["2026.01.01", "2026.01.02", "2026.01.02", "2026.01.03"]
    # q loads past a segment that does not exist, saying nothing
    missing = q_view(q_binary, fixtures / "missing" / "hdb", tmp_path)
    assert missing["pv"] == ["2026.01.01", "2026.01.02", "2026.01.03"]
    assert "Permission denied" in q_view(q_binary, fixtures / "locked" / "hdb", tmp_path)["error"]
    assert q_view(q_binary, fixtures / "stale" / "hdb", tmp_path)["error"] == "part"
    empty = q_view(q_binary, fixtures / "empty" / "hdb", tmp_path)
    assert empty["pv"] == [] and empty["tables"] == []


# ------------------------------------------------------ the check, against it


def test_a_conventional_hdb_names_the_partition_lacking_a_table(
    fixtures, q_binary, tmp_path, monkeypatch
):
    code, doc = data_check(monkeypatch, fixtures, "conv", tmp_path, q_binary)
    assert doc["missing_tables"] == {"2026.01.01": ["trade"]}
    assert code == 1


def test_an_empty_hdb_has_no_partition_to_be_short(fixtures, q_binary, tmp_path, monkeypatch):
    code, doc = data_check(monkeypatch, fixtures, "empty", tmp_path, q_binary)
    assert doc["present"] is True
    assert doc["missing_tables"] == {} and doc["missing_columns"] == {}
    assert code == 0


@pytest.mark.xfail(strict=True, reason="#1783: the check lists only the root, so sees no partition")
def test_a_segmented_hdb_is_judged_in_its_segments(fixtures, q_binary, tmp_path, monkeypatch):
    """Every partition q discovers is judged, in the segment that holds it."""
    code, doc = data_check(monkeypatch, fixtures, "seg", tmp_path, q_binary)
    seg = fixtures / "seg"
    assert doc["missing_tables"] == {
        f"{seg / 's1'}/2026.01.01": ["trade"],
        f"{seg / 's2'}/2026.01.03": ["trade"],
    }
    assert code == 1


@pytest.mark.xfail(strict=True, reason="#1783: the type check reads only the root's partitions")
def test_a_segmented_hdb_has_its_column_types_checked(fixtures, q_binary, tmp_path, monkeypatch):
    """The stored types are read where the partitions are, too: trade's px is
    a float in s2, and a runtime declaring it a long is told so."""
    schema = SCHEMA.replace("px:`float$()", "px:`long$()")
    code, doc = data_check(monkeypatch, fixtures, "seg", tmp_path, q_binary, schema)
    assert doc["types_checked"] is True
    assert [(c["partition"], c["table"], c["column"], c["on_disk"], c["declared"])
            for c in doc["type_changes"]] == [
        (f"{fixtures / 'seg' / 's2'}/2026.01.02", "trade", "px", "f", "j")
    ]  # fmt: skip
    assert code == 1


@pytest.mark.xfail(strict=True, reason="#1783: the check lists only the root, so sees no partition")
def test_a_date_in_two_segments_is_judged_in_each(fixtures, q_binary, tmp_path, monkeypatch):
    """q reads a date from every segment holding it, so each copy must be
    whole: the s1 copy of 2026.01.02 lacking trade fails a query on that day."""
    code, doc = data_check(monkeypatch, fixtures, "split", tmp_path, q_binary)
    split = fixtures / "split"
    assert doc["missing_tables"] == {
        f"{split / 's1'}/2026.01.01": ["trade"],
        f"{split / 's1'}/2026.01.02": ["trade"],
        f"{split / 's2'}/2026.01.03": ["trade"],
    }
    assert code == 1


@pytest.mark.xfail(strict=True, reason="#1783: a segment that does not exist passes unmentioned")
def test_a_segment_that_does_not_exist_is_named(fixtures, q_binary, tmp_path, monkeypatch):
    """q loads past it without a word, which is how data goes missing quietly."""
    code, doc = data_check(monkeypatch, fixtures, "missing", tmp_path, q_binary)
    assert code == 1
    assert doc["ok"] is False
    assert str(fixtures / "missing" / "s3") in doc["error"]
    assert "does not exist" in doc["error"]


@pytest.mark.xfail(strict=True, reason="#1783: an unreadable segment passes unmentioned")
def test_a_segment_q_cannot_read_is_named(fixtures, q_binary, tmp_path, monkeypatch):
    code, doc = data_check(monkeypatch, fixtures, "locked", tmp_path, q_binary)
    assert code == 1
    assert doc["ok"] is False
    assert str(fixtures / "locked" / "s3") in doc["error"]
    assert "Permission denied" in doc["error"]


@pytest.mark.xfail(strict=True, reason="#1783: root partitions q ignores are judged instead")
def test_partitions_beside_par_txt_are_refused_as_q_refuses_them(
    fixtures, q_binary, tmp_path, monkeypatch
):
    """q will not load a root holding both, so no partition of it is judged."""
    code, doc = data_check(monkeypatch, fixtures, "stale", tmp_path, q_binary)
    assert code == 1
    assert doc["ok"] is False
    assert "2025.12.31" in doc["error"] and "par.txt" in doc["error"]


@pytest.mark.parametrize("fixture", ["conv", "seg", "split", "missing", "locked", "stale", "empty"])
def test_the_check_writes_nothing(fixtures, q_binary, tmp_path, monkeypatch, fixture):
    """Inspection only: not a file created, resized or touched - a journal,
    a sym file, a repaired table - whatever the layout."""
    before = _snapshot(fixtures)
    data_check(monkeypatch, fixtures, fixture, tmp_path, q_binary)
    assert _snapshot(fixtures) == before
