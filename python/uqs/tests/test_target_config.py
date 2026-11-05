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
from typing import Any

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
    assert done.exit_code != 0 and "no target nosuch - " in "".join(logged)


def test_show_and_history_print_markup_in_server_held_text_literally(server):
    """A destination, user, via or reported path holding `[...]` is shown as
    written - never read as rich markup, which crashes on an unbalanced
    closing tag (#2025)."""
    (server / "shared/config/conf[/x]").mkdir(parents=True)
    push_id = remote_files.new_push_id()
    opened = remote_helper.handle({"protocol": 1, "op": "push_open", "root": str(server),
                                   "push_id": push_id, "destination": "shared/config/conf[/x]",
                                   "user": "k[/w]", "via": "web[/v]"})  # fmt: skip
    assert opened["ok"], opened
    data = b"a=1\n"
    answer = remote_helper.handle(
        {"protocol": 1, "op": "upload", "root": str(server), "path": "shared/config/conf[/x]/a",
         "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "overwrite": False,
         "push_id": push_id},
        io.BytesIO(data),
    )  # fmt: skip
    assert answer["ok"], answer
    reported = [{"path": "x[/b]", "outcome": "cancelled"}]
    closed = remote_helper.handle({"protocol": 1, "op": "push_close", "root": str(server),
                                   "push_id": push_id, "reported": reported})  # fmt: skip
    assert closed["ok"], closed
    shown = _uqs("show", "box", push_id)
    assert shown.exit_code == 0, shown.output
    said = " ".join(shown.stdout.split())  # the header may wrap
    for literal in ("to shared/config/conf[/x] by k[/w] via web[/v]", "'x[/b]' (cancelled)"):
        assert literal in said, shown.stdout
    listed = _uqs("history", "box")
    assert listed.exit_code == 0, listed.output
    assert "k[/w]" in listed.stdout and "web[/v]" in " ".join(listed.stdout.split())


def test_restore_prunes_to_the_retention_set_in_the_settings_view(server, monkeypatch, tmp_path):
    """#2072: retention changed through PUT /settings/runtime is the bound a
    CLI restore passes - not the environment's defaults, which would prune
    away the pushes the Settings view promised to keep."""
    from fastapi.testclient import TestClient

    from uqf_frontend.app import create_app
    from uqf_frontend.config import Settings
    from uqf_frontend.fleet import FakeFleet
    from uqf_frontend.gateway import FakeGateway

    monkeypatch.delenv("UQF_FRONTEND_CONFIG_HISTORY_KEEP", raising=False)
    monkeypatch.delenv("UQF_FRONTEND_CONFIG_HISTORY_DAYS", raising=False)
    kept = tmp_path / "settings.json"
    token = "k" * 64
    api = TestClient(
        create_app(
            gateway=FakeGateway({}),
            fleet=FakeFleet(),
            settings=Settings(
                enable_writes=True, write_token=token, allowed_hosts=("testserver",),
                settings_file=kept, activity_db=tmp_path / "a.sqlite3",
            ),
        )
    )  # fmt: skip
    seen = api.get("/settings").json()
    changed = api.put(
        "/settings/runtime",
        json={
            "version": seen["runtime_version"],
            "settings": {
                **seen["runtime"],
                "config_history_keep": 500,
                "config_history_days": 3000,
            },
        },
        headers={"authorization": f"Bearer {token}"},
    )
    assert changed.status_code == 200, changed.text

    monkeypatch.setenv("UQF_FRONTEND_SETTINGS_FILE", str(kept))
    bounds: list[tuple[Any, Any]] = []
    real: Any = remote_files.restore

    def spy(*args: Any, **kw: Any) -> Any:
        bounds.append((kw["keep"], kw["keep_days"]))
        return real(*args, **kw)

    monkeypatch.setattr(remote_files, "restore", spy)
    push = _push(server, {"shared/config/app.env": b"a=2\n"})
    done = _uqs("restore", "box", push, "--dry-run")
    assert done.exit_code == 0, done.output
    assert bounds == [(500, 3000)]
