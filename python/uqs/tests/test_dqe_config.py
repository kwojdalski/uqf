"""DQE's generated query list (stack/dqe.py): the vendored rows kept, this
tree's metatables appended, and each appended row runnable as DQE runs it."""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import pytest

from uqs.paths import UqsPaths
from uqs.stack import dqe


@pytest.fixture
def paths(tmp_path: Path) -> UqsPaths:
    """Just the directories write_dqe_config reads and writes."""
    (tmp_path / "app" / "appconfig").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "uqs",
    )


def _rows(paths: UqsPaths) -> list[dict[str, str]]:
    with paths.generated_dqe_config.open(newline="") as f:
        return list(csv.DictReader(f))


def test_the_vendored_rows_are_kept_and_the_metatables_appended(paths: UqsPaths):
    vendored = paths.torqapphome / "appconfig" / "dqengineconfig.csv"
    vendored.write_text(
        "query,params,proc,querytype,starttime\ndatecheck,`quote,`hdb1,table,04:30:00.000000000\n"
    )

    dqe.write_dqe_config(paths)

    rows = _rows(paths)
    assert rows[0]["query"] == "datecheck", "the vendored row comes through first, unchanged"
    assert [r["query"] for r in rows[1:]] == [r["query"] for r in dqe.UQF_DQE_ROWS]
    assert "meta_quotes_by_sym" in rows[1]["params"]
    assert vendored.read_text().count("\n") == 2, "the vendored file is never edited"


def test_no_vendored_file_still_writes_the_metatables(paths: UqsPaths):
    dqe.write_dqe_config(paths)
    assert [r["query"] for r in _rows(paths)] == [r["query"] for r in dqe.UQF_DQE_ROWS]


@pytest.mark.parametrize("row", dqe.UQF_DQE_ROWS, ids=lambda r: r["query"])
def test_a_row_holds_no_comma_dqe_would_split_on(row):
    """DQE reads the file as plain comma-separated text, with no quoting."""
    assert all("," not in value for value in row.values())


def test_the_generated_rows_run_as_dqe_runs_them(paths: UqsPaths, q_binary, repo_root):
    """Read the generated file the way dqe.q does (`readdqeconfig`, "S**SN"),
    `value` each appended row's params as `loadtimer` does, and apply
    `.dqe.uqf_metatable` to them as `runquery` does on the target - against an
    in-memory `quotes` with yesterday's and today's rows. Only yesterday's are
    counted, one row per sym."""
    dqe.write_dqe_config(paths)
    q, env = q_binary
    script = f"""
\\l {repo_root}/src/metadata/metatables.q
\\l {repo_root}/scripts/processes/torq_metatables.q
t:("S**SN";enlist ",") 0: hsym `$"{paths.generated_dqe_config}";
quotes:([] date:(.z.d-1),(.z.d-1),(.z.d-1),.z.d; sym:`EURUSD`EURUSD`GBPUSD`EURUSD;
    time:0D01 0D02 0D03 0D04+(.z.d-1),(.z.d-1),(.z.d-1),.z.d);
r:.dqe.uqf_metatable . value first exec params from t where query=`uqf_metatable;
m:first value r;
-1 string first key r;
-1 "," sv string exec sym from m;
-1 "," sv string exec rows from m;
-1 first exec proc from t where query=`uqf_metatable;
exit 0
"""
    path = paths.torqdata / "run_dqe_rows.q"
    path.write_text(script)
    out = subprocess.run(
        [q, str(path)], capture_output=True, text=True, env=env, cwd=repo_root, timeout=60
    )
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    assert lines[-4:] == ["meta_quotes_by_sym", "EURUSD,GBPUSD", "2,1", "`hdb1"], (
        out.stdout + out.stderr
    )
