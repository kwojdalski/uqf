"""The shared HDB against a release's schema during a deployment (#870).

prepare's check is the release's own `uqs data hdb-check --json`, faked here
by what it prints. The dry run's listing (LIST_PY) runs for real, with bash
and python3, against an HDB under tmp_path. The q type check runs on PeachQ
when UQF_PEACHQ names one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from uqs.deploy import hdb
from uqs.deploy.config import DeployError, make_config
from uqs.deploy.stages import Deployment

PEACHQ = os.environ.get("UQF_PEACHQ", "")


def _done(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


class FakeRemote:
    """Answers hdb-check by its arguments; records every script."""

    def __init__(self, check: dict, fixed: dict | None = None) -> None:
        self.check, self.fixed = check, fixed
        self.scripts: list[str] = []

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        self.scripts.append(script)
        body = (self.fixed if "--fix" in script else self.check) or {}
        return _done(json.dumps(body, indent=2) + "\n", rc=1 if body.get("missing_tables") else 0)


class LocalRemote:
    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def _dep(remote, dest="/opt/uqf", fix_hdb=False, data_dir=None) -> Deployment:
    cfg = make_config(
        artifact="", host="h", dest=dest, profile="essential", fix_hdb=fix_hdb, data_dir=data_dir
    )
    return Deployment(cfg, remote)


SHORT = {
    "present": True,
    "missing_tables": {"2026.01.01": ["book"]},
    "missing_columns": {"2026.01.02": {"quote": ["venue"]}},
    "type_changes": [],
    "types_checked": True,
}
CLEAN = {**SHORT, "missing_tables": {}, "missing_columns": {}}


def test_no_hdb_yet_is_nothing_to_judge():
    assert hdb.check(_dep(FakeRemote({"present": False})), "/r") == {"hdb": "absent"}


def test_a_short_partition_is_refused_without_fix_hdb():
    remote = FakeRemote(SHORT)
    with pytest.raises(DeployError, match=r"2 HDB partition\(s\) lack .*pass --fix-hdb"):
        hdb.check(_dep(remote), "/r")
    assert not any("--fix" in s for s in remote.scripts), "nothing is written"


def test_fix_hdb_fills_and_checks_again():
    remote = FakeRemote(SHORT, fixed=CLEAN)
    got = hdb.check(_dep(remote, fix_hdb=True), "/r")
    assert got["hdb"] == "filled" and got["filled"]["tables"] == {"2026.01.01": ["book"]}
    assert "data hdb-check --fix --json" in remote.scripts[-1]


def test_fix_hdb_that_leaves_a_gap_is_refused():
    with pytest.raises(DeployError, match="left tables or columns missing"):
        hdb.check(_dep(FakeRemote(SHORT, fixed=SHORT), fix_hdb=True), "/r")


@pytest.mark.parametrize("fix_hdb", [False, True])
def test_a_type_change_is_always_refused(fix_hdb):
    changed = {**CLEAN, "type_changes": [
        {"partition": "2026.01.02", "table": "book", "column": "px", "on_disk": "j",
         "declared": "f"}]}  # fmt: skip
    with pytest.raises(DeployError, match=r"book\.px stored as j, declared f"):
        hdb.check(_dep(FakeRemote(changed), fix_hdb=fix_hdb), "/r")


def _hdb(root: Path) -> None:
    for part, tables in {"2026.01.01": {"quote": ["time", "sym"]}, "2026.01.02": {}}.items():
        (root / part).mkdir(parents=True)
        for t, cols in tables.items():
            (root / part / t).mkdir()
            (root / part / t / ".d").write_bytes(b"")
            for c in cols:
                (root / part / t / c).write_bytes(b"")
    (root / "sym").write_bytes(b"")


def test_the_dry_run_lists_what_would_be_filled_from_the_servers_hdb(tmp_path):
    data = tmp_path / "data"
    _hdb(data / "uqs" / "hdb")
    dep = _dep(LocalRemote(), data_dir=str(data))
    shape = {"quote": ["time", "sym", "venue"], "book": ["time"]}
    line = hdb.planned(dep, {"hdb_shape": shape})
    # book is in neither partition; quote is in one, short of venue
    assert line.startswith("3 missing table(s) and 1 missing column(s) in 2 partition(s)")
    assert "refused without --fix-hdb" in line
    fixing = _dep(LocalRemote(), data_dir=str(data), fix_hdb=True)
    assert "filled by --fix-hdb" in hdb.planned(fixing, {"hdb_shape": shape})


def test_the_dry_run_of_a_complete_hdb_has_nothing_to_fill(tmp_path):
    _hdb(tmp_path / "uqs" / "hdb")
    (tmp_path / "uqs" / "hdb" / "2026.01.02" / "quote").mkdir()
    for c in ("time", "sym"):
        (tmp_path / "uqs" / "hdb" / "2026.01.02" / "quote" / c).write_bytes(b"")
    dep = _dep(LocalRemote(), data_dir=str(tmp_path))
    line = hdb.planned(dep, {"hdb_shape": {"quote": ["time", "sym"]}})
    assert line.startswith("every partition holds every declared table and column")


def test_an_artifact_without_a_shape_is_not_judged_in_the_dry_run():
    assert "built before #870" in hdb.planned(_dep(LocalRemote()), {})


@pytest.mark.skipif(not (PEACHQ and Path(PEACHQ).is_file()), reason="UQF_PEACHQ names no q")
def test_the_type_check_finds_a_column_stored_as_another_type(tmp_path):
    from uqs.stack import hdb_types

    script = Path(__file__).resolve().parents[3] / "scripts" / hdb_types.TYPES_SCRIPT
    (tmp_path / "mk.q").write_text(
        "`:hdb/2026.01.01/book/ set .Q.en[`:hdb] ([] sym:`a`b; px:1 2f);\n"
        "`:hdb/2026.01.02/book/ set .Q.en[`:hdb] ([] sym:`a`b; px:1 2);\nexit 0\n"
    )
    (tmp_path / "schema.q").write_text("book:([] sym:`symbol$(); px:`float$())\n")
    subprocess.run([PEACHQ, "mk.q", "-q"], cwd=tmp_path, check=True, stdin=subprocess.DEVNULL)
    out = subprocess.run(
        [PEACHQ, str(script), "hdb", "schema.q", "-q"],
        cwd=tmp_path, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    ).stdout.splitlines()  # fmt: skip
    assert out == ["type_change|2026.01.02|book|px|j|f", "DONE"]
