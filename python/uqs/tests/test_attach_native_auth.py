"""How a natively registered publisher authenticates (#1849): #1847's model
(stack/attached_publisher), judged against the SITE's TorQ application - the one
the process file is in - never this tree's.

  site          KDBSECURE names a directory: the row has no `-U`, and the
                launcher exports UQF_SITE_AUTH=1 and KDBSECURE;
  starter-pack  the site's application has the access list: row and launcher
                exactly as before;
  neither       refused, with what to do, and nothing written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from attach_native_support import PROC, SECRET, TREE_ENV, UQF_ROW, make_world, new, rows_for
from plain_cli import PlainRunner

from uqs import cli
from uqs.attach import config, native, native_row
from uqs.attach.manifest import Manifest
from uqs.attach.registry import Registry
from uqs.stack import attached_publisher as ap

ACCESS = "${TORQAPPHOME}/appconfig/passwords/accesslist.txt"


@pytest.fixture
def world(monkeypatch, tmp_path):
    return make_world(monkeypatch, tmp_path)


@pytest.fixture
def secure(tmp_path, monkeypatch) -> Path:
    """A site's KDBSECURE, holding a credential no file uqs writes may carry."""
    path = tmp_path / "kdbsecure"
    path.mkdir()
    (path / "pm.enc").write_text(SECRET)
    monkeypatch.setenv(ap.KDBSECURE_ENV, str(path))
    return path


def run(*argv: str):
    return PlainRunner().invoke(cli.app, ["job", "attachment", *argv], catch_exceptions=False)


def _row(world) -> dict[str, str]:
    (line,) = rows_for(world.registry)
    header = world.registry.read_bytes().decode().splitlines()[0].split(",")
    return dict(zip(header, line.split(","), strict=True))


def _launcher(world) -> str:
    return (world.state_dir / "pb-uat" / "native_start.sh").read_text()


def _files(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _registration_findings(result) -> str:
    found = json.loads(result.output)["plan"]["findings"]
    return " ".join(" ".join(f["message"].split()) for f in found if f["code"] == native.CODE)


# ------------------------------------------------------------------- the site


def test_a_kdbsecure_site_gets_no_dash_u_and_a_launcher_exporting_its_auth(world, secure):
    result = run("add", *new(world), "--yes")
    assert result.exit_code == 0, result.output
    row = _row(world)
    assert row["U"] == ""
    assert str(secure) not in world.registry.read_text()  # not written into the row
    launcher = _launcher(world)
    assert f"export {ap.SITE_AUTH_ENV}=1\n" in launcher
    assert f"export {ap.KDBSECURE_ENV}={secure}\n" in launcher
    for name, path in TREE_ENV.items():
        assert f"export {name}={path}\n" in launcher  # tree_env, still


def test_the_written_launcher_hands_q_the_sites_auth(world, secure, tmp_path):
    if shutil.which("sh") is None:
        pytest.skip("no sh")
    assert run("add", *new(world), "--yes").exit_code == 0
    log = tmp_path / "q.log"
    gate = tmp_path / "gate"
    gate.write_text("#!/bin/sh\nexit 0\n")
    gate.chmod(0o755)
    q = tmp_path / "q"
    q.write_text(f'#!/bin/sh\necho "$UQF_SITE_AUTH $KDBSECURE $UQF_ROOT" >> {log}\n')
    q.chmod(0o755)
    text = _launcher(world)
    assert sys.executable in text
    launcher = tmp_path / "native_start.sh"
    launcher.write_text(text.replace(sys.executable, str(gate)))
    env = {k: v for k, v in os.environ.items() if k not in (ap.SITE_AUTH_ENV, ap.KDBSECURE_ENV)}
    done = subprocess.run(
        ["sh", str(launcher), "-stackid", "6000", "-procname", PROC],
        env={**env, "QCMD": str(q)}, capture_output=True, text=True,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert log.read_text().split() == ["1", str(secure), TREE_ENV["UQF_ROOT"]]


# --------------------------------------------------------- the starter pack


def test_a_starter_pack_site_gets_the_row_and_launcher_it_always_did(world):
    result = run("add", *new(world), "--yes")
    assert result.exit_code == 0, result.output
    att = config.load("pb-uat")
    launcher = native_row.launcher_path(att)
    offset = int(_row(world)["port"].split("+")[1])
    values = native_row.row_values(att, dict(UQF_ROW), offset, launcher, dict(TREE_ENV))
    (line,) = rows_for(world.registry)
    assert line == Registry.read(world.registry).row(values)  # the #1845 row
    assert _row(world)["U"] == ACCESS
    assert _launcher(world) == native_row.launcher_text(att, dict(TREE_ENV))
    assert ap.SITE_AUTH_ENV not in _launcher(world) and "KDBSECURE" not in _launcher(world)


# ------------------------------------------------------------------ refusals


def test_a_site_app_without_the_access_list_is_refused_and_nothing_written(world, tmp_path):
    (world.app / ap.STARTER_ACCESS_LIST).unlink()
    before = _files(tmp_path)
    result = run("add", *new(world), "--yes", "--json")
    assert result.exit_code == 1
    said = _registration_findings(result)
    assert f"{PROC} cannot be registered: the stack's TorQ application ({world.app})" in said
    assert "has no appconfig/passwords/accesslist.txt, and KDBSECURE is not set" in said
    assert "uqf will not fall back to TorQ's demo credentials" in said
    assert "export KDBSECURE as the site's processes see it and run the same command" in said
    assert "Nothing was written." in said
    assert _files(tmp_path) == before and not world.started


def test_a_process_file_outside_an_appconfig_folder_is_refused(world, tmp_path):
    loose = tmp_path / "elsewhere" / "process.csv"
    loose.parent.mkdir()
    loose.write_bytes(world.registry.read_bytes())
    before = _files(tmp_path)
    result = run("add", *new(world)[:-2], "--process-file", str(loose), "--yes", "--json")
    assert result.exit_code == 1
    said = _registration_findings(result)
    assert f"{loose} is not in a TorQ application's appconfig/ folder" in said
    assert "export KDBSECURE as the site's processes see it" in said
    assert _files(tmp_path) == before


def test_kdbsecure_naming_no_directory_is_refused(world, tmp_path, monkeypatch):
    monkeypatch.setenv(ap.KDBSECURE_ENV, str(tmp_path / "missing"))
    before = _files(tmp_path)
    result = run("add", *new(world), "--yes", "--json")
    assert result.exit_code == 1
    said = _registration_findings(result)
    assert f"{PROC} cannot be registered: KDBSECURE=" in said and "is not a directory" in said
    assert _files(tmp_path) == before


# ----------------------------------------------------- the decision changing


def test_switching_auth_rewrites_both_owned_files_without_a_drift_refusal(
    world, tmp_path, monkeypatch
):
    assert run("add", *new(world), "--yes").exit_code == 0
    assert _row(world)["U"] == ACCESS
    secure = tmp_path / "kdbsecure"
    secure.mkdir()
    monkeypatch.setenv(ap.KDBSECURE_ENV, str(secure))
    site = run("add", "pb-uat", "--yes")
    assert site.exit_code == 0, site.output
    assert _row(world)["U"] == "" and f"export {ap.SITE_AUTH_ENV}=1" in _launcher(world)
    regs = Manifest.load("pb-uat").registrations
    assert regs["process_row"]["value"] == rows_for(world.registry)[0]
    assert regs["launcher"]["value"] == native_row.sha(_launcher(world))
    assert all(r["status"] == "done" for r in regs.values())
    monkeypatch.delenv(ap.KDBSECURE_ENV)
    back = run("add", "pb-uat", "--yes")
    assert back.exit_code == 0, back.output
    assert _row(world)["U"] == ACCESS and ap.SITE_AUTH_ENV not in _launcher(world)


# -------------------------------------------------------------------- secrets


def test_no_secret_reaches_any_file_on_a_site(world, secure, monkeypatch):
    monkeypatch.setenv("UQS_NATIVE_TEST_PW", SECRET)
    done = run("add", *new(world), "--password-env", "UQS_NATIVE_TEST_PW", "--yes", "--json")
    assert done.exit_code == 0, done.output
    assert SECRET not in done.output
    for path in [world.registry, *(world.state_dir / "pb-uat").rglob("*")]:
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path
