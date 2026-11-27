"""`uqs data sample` (#2812): a row-limited rehearsal copy of HDB tables.

The q-backed tests build a real source HDB and a destination whose sym file
enumerates in another order - the case a file copy gets silently wrong - and
run scripts/hdb/sample.q on whatever q is configured (QCMD), skipping when
none is.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli.data import sample as cli_sample
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_sample

ROOT = Path(__file__).resolve().parents[3]
D19, D20, D21 = date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 21)

#: The source: quote on both days (5000 rows, then 20), sorted `p# by sym with
#: a second enumerated column and a nested one; trade on the 19th only. The
#: destination's sym file already holds ZZZ USDJPY ebs EURUSD, so the source's
#: indices mean other symbols there.
MAKE = r"""
src:hsym `$"SRC"; dst:hsym `$"DST";
n:5000;
quote:`sym xasc ([] time:2026.11.19D09:00:00+til n; sym:n#`EURUSD`GBPUSD`USDJPY`AUDUSD;
    bid:`float$til n; ask:1+`float$til n; venue:n#`ebs`rtm`ebs; tags:n#(enlist "a";"bc";"def"));
.Q.dpft[src;2026.11.19;`sym;`quote];
quote:`sym xasc ([] time:2026.11.20D09:00:00+til 20; sym:20#`NZDUSD`EURUSD;
    bid:`float$til 20; ask:`float$til 20; venue:20#`rtm; tags:20#(enlist "z";"yy"));
.Q.dpft[src;2026.11.20;`sym;`quote];
trade:([] time:2026.11.19D10:00:00+til 300; sym:300#`EURUSD`USDJPY; px:`float$til 300; sz:til 300);
.Q.dpft[src;2026.11.19;`sym;`trade];
(` sv dst,`sym) set `ZZZ`USDJPY`ebs`EURUSD;
exit 0
"""

#: Every table partition under ROOT as JSON lines: day, table, attribute of
#: sym, and the rows decoded against ROOT's own domain files.
READ = r"""
root:hsym `$"ROOT";
dec:{[root;v] $[(type v) within 20 76; (get ` sv root,key v) `long$v; v]};
parts:asc "D"$string k where (k:key root) like "[0-9]*";
{[root;d]
    {[root;d;t]
        dir:.Q.par[root;d;t];
        if[()~key ` sv dir,`.d; :()];
        c:get ` sv dir,`.d;
        raw:{[dir;c] get ` sv dir,c}[dir] each c;
        v:dec[root] each raw;
        a:$[`sym in c; attr raw c?`sym; `];
        -1 .j.j `day`table`attr`rows!(string d;t;a;flip c!v);
        }[root;d] each key ` sv root,`$string d;
    }[root] each parts;
exit 0
"""


def _q() -> Path:
    q = q_interpreter()
    if q is None:
        pytest.skip("no q interpreter (QCMD) to build and read an HDB with")
    return q


def _run(q: Path, script: str, tmp: Path) -> str:
    f = tmp / "script.q"
    f.write_text(script)
    out = subprocess.run([str(q), str(f)], capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def _paths(tmp: Path) -> UqsPaths:
    return UqsPaths(
        repo_root=ROOT,
        torqhome=ROOT / "lib" / "torq",
        torqapphome=ROOT / "lib" / "torq-finance-starter-pack",
        torqdata=tmp / "output" / "uqs-uqf",
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=ROOT / "python" / "uqs",
        runtime="uqf",
    )


@pytest.fixture
def hdbs(tmp_path: Path) -> tuple[Path, Path, Path]:
    q = _q()
    src, dst = tmp_path / "prod-hdb", tmp_path / "rehearsal-hdb"
    src.mkdir()
    dst.mkdir()
    _run(q, MAKE.replace("SRC", str(src)).replace("DST", str(dst)), tmp_path)
    return src, dst, q


def _read(root: Path, q: Path, tmp: Path) -> dict[tuple[str, str], dict]:
    out = _run(q, READ.replace("ROOT", str(root)), tmp)
    docs = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return {(d["day"], d["table"]): d for d in docs}


def _tree(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(root)).encode() + f.read_bytes())
    return h.hexdigest()


def _sample(tmp, src, dst, tables=("quote", "trade"), days=(D19, D20), limit=1000, **kw):
    return hdb_sample.sample(_paths(tmp), src, dst, list(tables), list(days), limit, **kw)


def test_symbols_decode_in_the_destination_as_in_the_source(hdbs, tmp_path):
    src, dst, q = hdbs
    doc = _sample(tmp_path, src, dst, apply=True)
    have, want = _read(dst, q, tmp_path), _read(src, q, tmp_path)
    got = have[("2026.11.19", "quote")]
    rows = want[("2026.11.19", "quote")]["rows"]
    assert got["rows"] == rows[::5], "every fifth row, spread over the day, decoded the same"
    assert got["attr"] == "p", "the source's `p# on sym is kept on the sample"
    assert have[("2026.11.19", "trade")]["rows"] == want[("2026.11.19", "trade")]["rows"]
    assert doc["copied_rows"] == 1000 + 20 + 300
    sym = _run(q, f'-1 .j.j get hsym `$"{dst}/sym"; exit 0', tmp_path).strip()
    assert json.loads(sym)[:4] == ["ZZZ", "USDJPY", "ebs", "EURUSD"], (
        "the destination's sym file was extended by enumeration, not replaced"
    )


def test_a_dry_run_changes_no_file(hdbs, tmp_path):
    src, dst, _ = hdbs
    before = (_tree(src), _tree(dst), sorted(p.name for p in tmp_path.iterdir()))
    doc = _sample(tmp_path, src, dst)
    assert doc["applied"] is False and len(doc["partitions"]) == 4
    assert (_tree(src), _tree(dst), sorted(p.name for p in tmp_path.iterdir())) == before


def test_the_limit_bounds_every_table_on_every_day(hdbs, tmp_path):
    src, dst, q = hdbs
    _sample(tmp_path, src, dst, limit=7, apply=True)
    counts = {k: len(v["rows"]) for k, v in _read(dst, q, tmp_path).items()}
    assert counts == {
        ("2026.11.19", "quote"): 7,
        ("2026.11.19", "trade"): 7,
        ("2026.11.20", "quote"): 7,
        ("2026.11.20", "trade"): 0,
    }


def test_a_day_without_the_table_gets_an_empty_one_and_a_day_without_a_partition_none(
    hdbs, tmp_path
):
    src, dst, q = hdbs
    doc = _sample(tmp_path, src, dst, days=(D19, D20, D21), apply=True)
    status = {(r["day"], r["tbl"]): r["status"] for r in doc["partitions"]}
    assert status[("2026.11.20", "trade")] == "fill"
    assert status[("2026.11.21", "quote")] == status[("2026.11.21", "trade")] == "none"
    assert not (dst / "2026.11.21").exists()
    assert (dst / "2026.11.20" / "trade" / ".d").is_file()
    load = _run(
        q,
        f'system "l {dst}"; -1 string count select from trade where date=2026.11.20; exit 0',
        tmp_path,
    )
    assert load.strip() == "0", "the destination loads and every sampled table is queryable"


def test_a_schema_mismatch_stops_before_any_write(hdbs, tmp_path):
    src, dst, q = hdbs
    _run(
        q,
        f'dst:hsym `$"{dst}"; trade:([] time:enlist 2026.11.18D10:00; sym:enlist `EURUSD; '
        "px:enlist 1f; sz:enlist 1i); .Q.dpft[dst;2026.11.18;`sym;`trade]; exit 0",
        tmp_path,
    )
    before = _tree(dst)
    with pytest.raises(UqsError, match=r"trade on 2026\.11\.19: column sz is type j, not i"):
        _sample(tmp_path, src, dst, apply=True)
    assert _tree(dst) == before
    assert not list(tmp_path.glob("rehearsal-hdb.sample-*"))


def test_a_table_already_there_is_refused_unless_told(hdbs, tmp_path):
    src, dst, q = hdbs
    _sample(tmp_path, src, dst, tables=("trade",), days=(D19,), limit=3, apply=True)
    with pytest.raises(UqsError, match="already holds trade on 2026.11.19 - pass --on-conflict"):
        _sample(tmp_path, src, dst, tables=("trade",), days=(D19,), apply=True)
    kept = _tree(dst / "2026.11.19" / "trade")
    doc = _sample(tmp_path, src, dst, days=(D19,), on_conflict="ignore", apply=True)
    assert doc["skipped_conflicts"] == 1
    assert _tree(dst / "2026.11.19" / "trade") == kept
    assert len(_read(dst, q, tmp_path)[("2026.11.19", "quote")]["rows"]) == 1000
    _sample(tmp_path, src, dst, tables=("trade",), days=(D19,), on_conflict="replace", apply=True)
    assert len(_read(dst, q, tmp_path)[("2026.11.19", "trade")]["rows"]) == 300
    assert not list(tmp_path.glob("rehearsal-hdb.sample-*")), "the replaced table is gone"


def test_a_failed_move_leaves_no_partial_table_visible(hdbs, tmp_path, monkeypatch):
    src, dst, _ = hdbs

    def fail(*_a):
        raise UqsError("disk full")

    monkeypatch.setattr(hdb_sample, "_move_in", fail)
    with pytest.raises(UqsError, match="disk full"):
        _sample(tmp_path, src, dst, apply=True)
    assert sorted(p.name for p in dst.iterdir()) == ["sym"], "no table was exposed"
    assert not list(tmp_path.glob("rehearsal-hdb.sample-*")), "the stage was removed"


def test_an_interrupted_runs_stage_is_reported_then_removed(hdbs, tmp_path):
    src, dst, _ = hdbs
    stale = tmp_path / f"rehearsal-hdb.sample-{socket.gethostname()}-999999-abc"
    (stale / "2026.11.19" / "quote").mkdir(parents=True)
    (stale / "2026.11.19" / "quote" / "bid").write_bytes(b"partial")
    assert _sample(tmp_path, src, dst)["stale_stages"] == [str(stale)]
    assert stale.is_dir(), "a dry run removes nothing"
    doc = _sample(tmp_path, src, dst, apply=True)
    assert doc["recovered"] == [str(stale)] and not stale.exists()


def _stage(root: Path, host: str, pid: int, written: float | None = None, tag="k3_x9") -> Path:
    stage = root.parent / f"{root.name}.sample-{host}-{pid}-{tag}"
    stage.mkdir()
    if written is not None:
        os.utime(stage, (written, written))
    return stage


def test_a_stage_is_stale_only_when_its_process_is_provably_gone(tmp_path):
    """#2985: uqs.liveness's rule - this host's dead pid, or a live pid that
    started after the stage was last written. Another host's is never judged."""
    dst = tmp_path / "rehearsal-hdb"
    host = socket.gethostname()
    gone = subprocess.Popen(["true"])
    gone.wait()
    dead = _stage(dst, host.upper(), gone.pid)  # host compared without case
    recycled = _stage(dst, host, os.getpid(), written=946684800.0)  # 2000-01-01
    _stage(dst, host, os.getpid(), tag="live")  # this run: alive, and started before it wrote
    _stage(dst, "elsewhere.example", gone.pid)
    assert hdb_sample.stale_stages(dst) == sorted([dead, recycled])


def test_a_symlinked_destination_is_locked_where_its_writers_lock_it(tmp_path, monkeypatch):
    """#2985: the lock is named after the root as given, as .qetl.io.hdb_lock names
    it, not after the symlink's target; only the stage sits beside the target."""
    (tmp_path / "volume" / "hdb").mkdir(parents=True)
    (tmp_path / "hdb").symlink_to(tmp_path / "volume" / "hdb")
    (tmp_path / "src" / "2026.11.19").mkdir(parents=True)
    locked: list[tuple[str, Path]] = []
    staged_in: list[Path] = []

    def run(_paths, _q, _env, argv, tag):
        if tag == "STAGED|":
            staged_in.append(Path(argv[-1]))
            return {"staged": []}
        return {"partitions": []}

    monkeypatch.setattr(hdb_sample, "with_interpreter", lambda _p: {})
    monkeypatch.setattr(hdb_sample, "q_interpreter", lambda _e: Path("q"))
    monkeypatch.setattr(hdb_sample, "_run", run)
    monkeypatch.setattr(hdb_sample, "_write_lock", lambda *a: locked.append((a[3], a[4])))
    hdb_sample.sample(
        _paths(tmp_path), tmp_path / "src", tmp_path / "hdb", ["quote"], [D19], 10, apply=True
    )
    assert locked == [("acquire", tmp_path / "hdb"), ("release", tmp_path / "hdb")]
    assert staged_in[0].parent == (tmp_path / "volume").resolve(), "staged beside the target"


def test_the_source_is_only_read(hdbs, tmp_path):
    src, dst, _ = hdbs
    before = _tree(src)
    _sample(tmp_path, src, dst, apply=True)
    assert _tree(src) == before


def test_a_fresh_destination_is_created(hdbs, tmp_path):
    src, _, q = hdbs
    fresh = tmp_path / "fresh"
    _sample(tmp_path, src, fresh, tables=("trade",), days=(D19,), limit=10, apply=True)
    assert len(_read(fresh, q, tmp_path)[("2026.11.19", "trade")]["rows"]) == 10


@pytest.mark.parametrize(
    ("into", "message"),
    [
        (".", "are the same directory"),
        ("2026.11.19", "is inside the source HDB"),
    ],
)
def test_overlapping_roots_are_refused(tmp_path, into, message):
    src = tmp_path / "hdb"
    (src / "2026.11.19").mkdir(parents=True)
    with pytest.raises(UqsError, match=message):
        hdb_sample.resolve_roots(src, src / into)


def test_a_source_inside_the_destination_is_refused(tmp_path):
    (tmp_path / "out" / "hdb").mkdir(parents=True)
    with pytest.raises(UqsError, match="is inside --into-hdb"):
        hdb_sample.resolve_roots(tmp_path / "out" / "hdb", tmp_path / "out")


def test_a_segmented_hdb_is_refused(tmp_path):
    (tmp_path / "hdb").mkdir()
    (tmp_path / "hdb" / "par.txt").write_text("/seg1\n")
    with pytest.raises(UqsError, match="segmented HDB"):
        hdb_sample.resolve_roots(tmp_path / "hdb", tmp_path / "out")


# ------------------------------------------------------------------- the CLI


@pytest.fixture
def calls(monkeypatch, tmp_path):
    seen: list = []
    monkeypatch.setattr(cli_sample, "_paths", lambda: _paths(tmp_path))

    def sample(paths, src, dst, tables, days, limit, on_conflict=None, *, apply=False):
        seen.append({"tables": tables, "days": days, "limit": limit, "apply": apply})
        return {"source": str(src), "destination": str(dst), "limit": limit,
                "days": [], "tables": tables, "partitions": [], "applied": apply,
                "moved": [], "copied_rows": 0, "recovered": []}  # fmt: skip

    monkeypatch.setattr(cli_sample.hdb_sample, "sample", sample)
    return seen


ARGV = ["data", "sample", "--hdb", "/prod/hdb", "--into-hdb", "/tmp/rehearsal", "--date",
        "2026-11-19", "--table", "quote", "--table", "trade"]  # fmt: skip


def test_the_cli_plans_by_default(calls):
    result = CliRunner().invoke(cli.app, ARGV)
    assert result.exit_code == 0, result.output
    assert calls == [{"tables": ["quote", "trade"], "days": [D19], "limit": 1000, "apply": False}]
    assert "nothing was changed" in result.output


def test_an_applied_sample_needs_the_sources_state(calls, log_text):
    result = CliRunner().invoke(cli.app, [*ARGV, "--apply", "--yes"])
    said = log_text()
    assert result.exit_code == 2 and "--quiesced" in said and "--point-in-time" in said
    assert calls == []


def test_quiesced_is_refused_while_the_sources_lock_is_held(calls, tmp_path, log_text):
    src = tmp_path / "hdb"
    src.mkdir()
    (tmp_path / "hdb.write.lock").write_text("pid 1")
    argv = [*ARGV, "--apply", "--yes", "--quiesced"]
    argv[argv.index("/prod/hdb")] = str(src)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 1 and "write lock" in log_text()
    assert calls == []


def test_quiesced_reads_a_symlinked_sources_lock_beside_the_link(calls, tmp_path, log_text):
    """#2985: a writer given the link locks beside the link, not beside its target."""
    (tmp_path / "volume" / "hdb").mkdir(parents=True)
    (tmp_path / "hdb").symlink_to(tmp_path / "volume" / "hdb")
    (tmp_path / "hdb.write.lock").write_text("pid 1")
    argv = [*ARGV, "--apply", "--yes", "--quiesced"]
    argv[argv.index("/prod/hdb")] = str(tmp_path / "hdb")
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 1 and "write lock" in log_text()
    assert calls == []


def test_a_range_samples_each_whole_day(calls):
    argv = [a for a in ARGV if a not in ("--date", "2026-11-19")]
    result = CliRunner().invoke(cli.app, [*argv, "--from", "2026-11-19", "--to", "2026-11-22"])
    assert result.exit_code == 0, result.output
    assert calls[0]["days"] == [D19, D20, D21]


def test_json_without_yes_apply_is_refused(calls, monkeypatch):
    """#2229: --json never asks, even at a terminal that would say y."""
    argv = [*ARGV, "--apply", "--point-in-time", "--json"]
    monkeypatch.setattr("sys.argv", ["uqs", *argv])  # json_requested reads it
    result = CliRunner().invoke(cli.app, argv, input="y\n")
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is False and "cannot ask for confirmation: add --yes to sample" in doc["error"]
    assert [c["apply"] for c in calls] == [False]


def test_the_sample_is_an_hdb_data_alter_can_rehearse_on(hdbs, tmp_path):
    """Criterion 8: the point of a sample - a migration rehearsed on it."""
    from uqs.stack import hdb_alter

    src, dst, q = hdbs
    _sample(tmp_path, src, dst, apply=True)
    paths = _paths(tmp_path)
    check = hdb_alter.run_local(
        paths, hdb_alter.Request(hdb=str(dst), table="quote", operation="check"), apply=False
    )
    assert check["exit_code"] == 0 and check["partitions_lacking"] == []
    assert len(check["shapes"]) == 1, "every sampled partition holds the one schema"
    done = hdb_alter.run_local(
        paths,
        hdb_alter.Request(hdb=str(dst), table="quote", operation="rename", value="bid=bid2"),
        apply=True,
    )
    assert done["applied"] is True and done["exit_code"] == 0
    row = _read(dst, q, tmp_path)[("2026.11.19", "quote")]["rows"][0]
    assert "bid2" in row and "bid" not in row and row["sym"] == "AUDUSD"
