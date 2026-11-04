"""`uqs target config history|show|diff|restore`: a target's config push
history (#2002).

The server is a temporary deployment and `ssh` a stand-in on PATH that runs
the remote command here (as in test_target_adopt.py), so the quoting, the
release's Python and the helper the frontend sends all run for real. Pushes
are made with the helper itself, in process, as an upload through the
frontend would leave them.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqf_frontend import remote_files, remote_helper
from uqs.cli import app
from uqs.deploy import history, targets

FAKE_SSH = """#!/bin/bash
exec bash -c "${@: -1}"
"""
RELEASE = "20261101T090000Z-abc1234"


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    dest = tmp_path / "srv" / "uqf"
    rel = dest / "releases" / RELEASE
    (rel / ".venv" / "bin").mkdir(parents=True)
    (rel / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (dest / "current").symlink_to(f"releases/{RELEASE}")
    (dest / "shared" / "config").mkdir(parents=True)
    (dest / "shared" / "config" / "app.env").write_bytes(b"a=1\n")
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text(f'[targets.box]\nssh = "ops@box"\ndest = "{dest}"\n')
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    monkeypatch.delenv(history.HISTORY_ENV, raising=False)
    return dest


def _push(dest: Path, files: dict[str, bytes], overwrite: bool = True) -> str:
    push_id = remote_files.new_push_id()
    opened = remote_helper.handle({"protocol": 1, "op": "push_open", "root": str(dest),
                                   "push_id": push_id, "destination": "shared/config",
                                   "user": "kw", "via": "frontend"})  # fmt: skip
    assert opened["ok"], opened
    for path, data in files.items():
        answer = remote_helper.handle(
            {"protocol": 1, "op": "upload", "root": str(dest), "path": path, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "overwrite": overwrite,
             "push_id": push_id},
            io.BytesIO(data),
        )  # fmt: skip
        assert answer["ok"], answer
    return push_id


@pytest.fixture
def logged():
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING", format="{message}")
    yield lines
    logger.remove(sink)


def _uqs(*argv: str):
    return CliRunner().invoke(app, ["target", "config", *argv])


def test_history_lists_the_pushes_newest_first(server):
    first = _push(server, {"shared/config/app.env": b"a=2\n"})
    second = _push(server, {"shared/config/new.csv": b"x\n"}, overwrite=False)
    done = _uqs("history", "box", "--json")
    assert done.exit_code == 0, done.output
    doc = json.loads(done.stdout)
    assert [p["push_id"] for p in doc["pushes"]] == [second, first]
    assert doc["target"] == "box" and doc["pushes"][0]["user"] == "kw"
    table = _uqs("history", "box")
    assert (
        table.exit_code == 0 and second in table.output and "push to shared/config" in table.output
    )


def test_show_says_each_file_and_what_it_is_now(server):
    push = _push(server, {"shared/config/app.env": b"a=2\n"})
    doc = json.loads(_uqs("show", "box", push, "--json").stdout)
    (entry,) = doc["push"]["files"]
    assert entry["action"] == "replace" and entry["now"] == "after"
    assert entry["previous_sha256"] == hashlib.sha256(b"a=1\n").hexdigest()


def test_diff_prints_a_unified_diff_and_hashes_for_binary(server):
    push = _push(server, {"shared/config/app.env": b"a=2\n", "shared/config/b.bin": b"\x00\x01"})
    done = _uqs("diff", "box", push)
    assert done.exit_code == 0, done.output
    assert "-a=1\n+a=2\n" in done.stdout
    assert "binary: 'shared/config/b.bin'" in done.stdout
    one = json.loads(_uqs("diff", "box", push, "shared/config/b.bin", "--json").stdout)
    assert [f["kind"] for f in one["files"]] == ["binary"]


def test_restore_asks_and_a_dry_run_writes_nothing(server, logged):
    push = _push(server, {"shared/config/app.env": b"a=2\n"})
    dry = _uqs("restore", "box", push, "--dry-run")
    assert dry.exit_code == 0, dry.output
    assert "write" in dry.output and "nothing was changed" in dry.output
    unasked = _uqs("restore", "box", push)  # no terminal to ask on, and no --yes
    assert unasked.exit_code != 0 and "--yes" in "".join(logged)
    assert (server / "shared/config/app.env").read_bytes() == b"a=2\n"


def test_restore_round_trip_from_the_cli(server):
    push = _push(server, {"shared/config/app.env": b"a=2\n", "shared/config/n/new": b"n"})
    done = _uqs("restore", "box", push, "--yes")
    assert done.exit_code == 0, done.output
    assert (server / "shared/config/app.env").read_bytes() == b"a=1\n"
    assert not (server / "shared/config/n").exists()
    newest = json.loads(_uqs("history", "box", "--json").stdout)["pushes"][0]
    assert newest["kind"] == "restore" and newest["restores"] == push
    assert newest["via"] == "cli"


def test_a_conflict_or_a_pruned_push_is_refused_writing_nothing(server, monkeypatch, logged):
    push = _push(server, {"shared/config/app.env": b"a=2\n"})
    (server / "shared/config/app.env").write_bytes(b"edited\n")
    refused = _uqs("restore", "box", push, "--yes")
    assert refused.exit_code != 0 and "changed since push" in "".join(logged)
    assert (server / "shared/config/app.env").read_bytes() == b"edited\n"
    monkeypatch.setenv("UQF_FRONTEND_CONFIG_HISTORY_KEEP", "1")
    remote_helper.handle({"protocol": 1, "op": "push_open", "root": str(server), "keep": 1,
                          "push_id": remote_files.new_push_id(),
                          "destination": "shared/config"})  # fmt: skip
    gone = _uqs("restore", "box", push, "--yes")
    assert gone.exit_code != 0 and "was pruned" in "".join(logged)


def test_an_unknown_target_is_refused(server, logged):
    done = _uqs("history", "nosuch")
    assert done.exit_code != 0 and "no target 'nosuch'" in "".join(logged)
