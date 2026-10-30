"""The HDB repair, run from what a RELEASE ships rather than from the checkout (#1423).

`scripts/gates/fill_hdb_partitions.q` loads KX's dbmaint.q from `lib/kx-dbmaint`,
and a release excludes the rest of `lib/`. The release's file set is therefore
what has to be enough: these copy exactly the files `uqs deploy build` would
package (`payload.tracked_files`) into a directory with nothing else of the
checkout in it, and run the repair from there, from another working directory,
with no proxy and no network address in reach of a q that needs none.

The repair itself is covered in tests/q/test_fill_hdb_partitions.q; this is
the packaging half, and the deployment half: a column of another type is never
filled, and prepare refuses it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from uqs.deploy import hdb, payload
from uqs.deploy.config import DeployError, make_config
from uqs.deploy.stages import Deployment

ROOT = Path(__file__).resolve().parents[3]

SCHEMA = "book:([] time:`timestamp$(); sym:`symbol$(); px:`float$(); venue:`symbol$())\n"

BUILD = """\
root:hsym `$.z.x 0;
t:2026.01.01D00:00:00.000000000;
(` sv .Q.par[root;2026.01.01;`book],`) set .Q.en[root;([] time:2#t; sym:`EUR`USD; px:1 2f)];
d2:([] time:1#t; sym:enlist `EUR; px:enlist 3f; venue:enlist `LMAX);
(` sv .Q.par[root;2026.01.02;`book],`) set .Q.en[root;d2];
exit 0
"""


def _release_tree(into: Path) -> Path:
    """The files a release of this checkout would package that the repair
    can touch: its scripts and the vendored lib, laid out as in the tarball."""
    tree = into / "release"
    for rel in payload.tracked_files(ROOT):
        if rel.startswith(("scripts/gates/", "lib/")):
            dest = tree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, dest)
    return tree


def _offline(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if "proxy" not in k.lower()}


def _q(q_binary, cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    q, env = q_binary
    return subprocess.run(
        [q, *argv, "-q"],
        capture_output=True,
        text=True,
        env=_offline(env),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=120,
    )


def test_a_release_carries_the_repair_dbmaint_and_the_licence_that_must_travel(tmp_path):
    tree = _release_tree(tmp_path)
    for rel in (
        "scripts/gates/fill_hdb_partitions.q",
        "lib/kx-dbmaint/dbmaint.q",
        "lib/kx-dbmaint/LICENSE-kx-kdb",
        "lib/kx-dbmaint/NOTICE.md",
    ):
        assert (tree / rel).is_file(), rel
    assert "Apache License" in (tree / "lib/kx-dbmaint/LICENSE-kx-kdb").read_text()
    # the release file is the vendored file, byte for byte
    assert (tree / "lib/kx-dbmaint/dbmaint.q").read_bytes() == (
        ROOT / "lib/kx-dbmaint/dbmaint.q"
    ).read_bytes()


def test_the_repair_runs_from_a_release_with_nothing_else_of_the_checkout(tmp_path, q_binary):
    tree = _release_tree(tmp_path)
    (tmp_path / "build.q").write_text(BUILD)
    hdb_root = tmp_path / "hdb"
    assert _q(q_binary, tmp_path, str(tmp_path / "build.q"), str(hdb_root)).returncode == 0
    schema = tmp_path / "database.q"
    schema.write_text(SCHEMA)
    # a different working directory, and the script by absolute path: dbmaint is
    # found from the script's own location, not from where q was started
    fixed = _q(
        q_binary,
        tmp_path,
        str(tree / "scripts/gates/fill_hdb_partitions.q"),
        str(hdb_root),
        str(schema),
    )
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert "added 1 column(s) - venue" in fixed.stdout
    d = hdb_root / "2026.01.01" / "book" / ".d"
    assert d.is_file() and (hdb_root / "2026.01.01" / "book" / "venue").is_file()


def test_a_column_of_another_type_is_reported_and_the_deployment_refuses_it(tmp_path, q_binary):
    """The repair never converts; `data check` reports it; prepare refuses it,
    with or without --fix-hdb."""
    tree = _release_tree(tmp_path)
    (tmp_path / "build.q").write_text(BUILD)
    hdb_root = tmp_path / "hdb"
    assert _q(q_binary, tmp_path, str(tmp_path / "build.q"), str(hdb_root)).returncode == 0
    # px is declared long here and stored as float
    schema = tmp_path / "database.q"
    schema.write_text(
        SCHEMA.replace("px:`float$()", "px:`long$()").replace("; venue:`symbol$()", "")
    )
    px = hdb_root / "2026.01.01" / "book" / "px"
    before = px.read_bytes()
    fixed = _q(
        q_binary,
        tmp_path,
        str(tree / "scripts/gates/fill_hdb_partitions.q"),
        str(hdb_root),
        str(schema),
    )
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert px.read_bytes() == before, "a mistyped column is never rewritten"
    found = _q(
        q_binary,
        ROOT,
        str(ROOT / "scripts/gates/hdb_column_types.q"),
        str(hdb_root),
        str(schema),
    )
    assert "type_change|2026.01.01|book|px|f|j" in found.stdout

    report = {
        "present": True,
        "missing_tables": {},
        "missing_columns": {},
        "types_checked": True,
        "type_changes": [
            {
                "partition": "2026.01.01",
                "table": "book",
                "column": "px",
                "on_disk": "f",
                "declared": "j",
            }
        ],
    }

    class Remote:
        def run(self, script, timeout, stage, as_login=False):
            return subprocess.CompletedProcess([], 1, stdout=json.dumps(report), stderr="")

        def put(self, local, remote_path, timeout, stage):
            raise AssertionError("the check copies nothing")

    for fix_hdb in (False, True):
        cfg = make_config(
            artifact="", host="h", dest="/opt/uqf", profile="essential", fix_hdb=fix_hdb
        )
        with pytest.raises(DeployError, match=r"1 HDB column\(s\) changed type - a migration"):
            hdb.check(Deployment(cfg, Remote()), "/r")
