"""Which HDB `uqs data hdb-check` / `hdb-alter` act on, and the refusals before any q runs (#1755).

No q here: the resolution from --target, --attach and --hdb, the writers check,
the reader reload and ssh's own failure are all decided in Python. What the q
script then does to a database is test_hdb_alter_cli.py's (and the q suite's,
tests/q/test_hdb_alter.q).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from attach_backfill_support import HdbStack, save_attachment
from typer.testing import CliRunner

from uqs.attach import backfill_owner, hdb_where
from uqs.attach.config import AttachmentError
from uqs.attach.facts import Host
from uqs.cli import hdb_scope, shared, target, target_confirm
from uqs.cli.shared import app
from uqs.deploy import control
from uqs.paths import UqsError, paths_for_root
from uqs.stack import hdb_remote
from uqs.stack.hdb_alter import Request

UQF_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """An attachment `pb` on a scripted stack whose wdb writes `tmp_path/hdb`."""
    monkeypatch.setenv("UQS_ATTACH_DIR", str(tmp_path / "attachments"))
    hdb = tmp_path / "hdb"
    hdb.mkdir()
    fake = HdbStack(hdb)
    # 'testhost' is another machine: the commands would reach it over ssh
    monkeypatch.setattr(hdb_where, "HOST", Host(local_names=("localhost",)))
    return fake, hdb


# ------------------------------------------------------------------ --attach


def test_the_pinned_hdb_is_used_and_named(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake)
    assert where.hdb == str(hdb)
    assert "pinned storage.hdb_dir" in where.source
    assert where.ssh == "testhost", "the stack's host, as the current user, when none is set"


def test_without_a_pin_the_live_wdb_s_hdbdir_is_used_and_named(stack):
    fake, hdb = stack
    save_attachment("pb", hdb, pin=False)
    where = hdb_where.resolve("pb", fake)
    assert where.hdb == str(hdb)
    assert "wdb1's .wdb.hdbdir" in where.source


def test_a_pin_the_live_wdb_contradicts_is_refused(stack, tmp_path):
    fake, hdb = stack
    save_attachment("pb", tmp_path / "elsewhere")
    with pytest.raises(AttachmentError, match=r"wdb1 reports .*ambiguous"):
        hdb_where.resolve("pb", fake)


def test_no_pin_and_no_live_wdb_is_refused(stack):
    fake, hdb = stack
    save_attachment("pb", hdb, pin=False)
    fake.down.add(fake.named("wdb1")["port"])
    with pytest.raises(AttachmentError, match="no live wdb reports one"):
        hdb_where.resolve("pb", fake)


def test_the_attachment_s_ssh_setting_wins_and_a_local_stack_needs_none(stack, monkeypatch):
    fake, hdb = stack
    att = save_attachment("pb", hdb)
    assert hdb_where.ssh_destination(att)[0] == "testhost"
    with_ssh = replace(att, stack=replace(att.stack, ssh="torq@prod"))
    dest, setting = hdb_where.ssh_destination(with_ssh)
    assert dest == "torq@prod" and "ssh 'torq@prod'" in setting
    here = Host(local_names=("testhost",))
    assert hdb_where.ssh_destination(att, here) == (None, "")


def test_the_table_defaults_to_the_attachment_s_own(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    assert hdb_where.resolve("pb", fake).table == "duckdb_deals"


# ------------------------------------------------------------------ writers


def test_a_live_backfill_owner_is_refused(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake)
    backfill_owner.claim("pb", "duckdb_deals_backfill", os.getpid(), "abc")
    with pytest.raises(AttachmentError, match="attached backfill running"):
        hdb_where.refuse_running_writers(where, fake)


def test_an_end_of_day_reload_in_progress_is_refused(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake)
    fake.state_over["wdb1"] = {"reloadcomplete": False}
    with pytest.raises(AttachmentError, match="end-of-day reload in progress"):
        hdb_where.refuse_running_writers(where, fake)
    fake.state_over["wdb1"] = {}
    hdb_where.refuse_running_writers(where, fake)


class ReloadingStack(HdbStack):
    """hdb1 answers, and reloads when asked."""

    def __init__(self, hdb: Path) -> None:
        super().__init__(hdb)
        reader = self.procs.pop(-self.named("hdb1")["port"])
        self.procs[reader["port"]] = reader
        self.reloaded: list[str] = []

    def call(self, endpoint, op, request):
        if op == "hdb_reload":
            self.reloaded.append(endpoint.procname)
            return {"status": "reloaded", "detail": ""}
        return super().call(endpoint, op, request)


def test_the_readers_serving_the_hdb_are_reloaded(monkeypatch, tmp_path):
    monkeypatch.setenv("UQS_ATTACH_DIR", str(tmp_path / "attachments"))
    hdb = tmp_path / "hdb"
    hdb.mkdir()
    fake = ReloadingStack(hdb)
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake)
    rows = hdb_where.reload_readers(where, fake)
    assert fake.reloaded == ["hdb1"]
    assert rows == [{"procname": "hdb1", "ok": "yes", "detail": ""}]


# ------------------------------------------------------------------ ssh


def test_an_unreachable_ssh_destination_is_refused_naming_the_setting(tmp_path):
    def unreachable(argv, **kw):
        assert argv[0] == "ssh" and "BatchMode=yes" in argv
        return subprocess.CompletedProcess(argv, 255, "", "ssh: connect to host x: refused")

    req = Request("/data/hdb", "pb", "check")
    with pytest.raises(UqsError) as caught:
        hdb_remote.run_ssh(
            "torq@prod", "attachment pb's ssh 'torq@prod'", UQF_ROOT, req, apply=False,
            runner=unreachable,
        )  # fmt: skip
    said = str(caught.value)
    assert "attachment pb's ssh 'torq@prod'" in said and "--ssh" in said
    assert "Nothing was run" in said


def test_the_remote_script_carries_both_files_and_cleans_up():
    script = hdb_remote.remote_script(UQF_ROOT, Request("/d/hdb", "pb", "check"), apply=False)
    assert script.count("base64 -d") == 2, "hdb_alter.q and dbmaint.q, nothing else"
    assert "trap 'rm -rf \"$tmp\"' EXIT" in script
    assert "${QCMD:-q}" in script
    assert "-hdb /d/hdb -table pb -json -q" in script


# ------------------------------------------------------------------ handles


def test_two_handles_are_refused(monkeypatch):
    monkeypatch.setattr(hdb_scope, "_paths", lambda: paths_for_root(UQF_ROOT))
    with pytest.raises(UqsError, match="one of --target, --attach and --hdb"):
        hdb_scope.resolve("pb", "/data/hdb")


def test_with_no_handle_it_is_this_runtime_s_configured_hdb(monkeypatch):
    paths = paths_for_root(UQF_ROOT)
    monkeypatch.setattr(hdb_scope, "_paths", lambda: paths)
    handle = hdb_scope.resolve(None, None)
    assert handle.kind == "runtime" and handle.hdb == str(paths.hdb_dir)
    assert "this runtime's HDB" in handle.source


@pytest.mark.parametrize(
    ("command", "options"), [("hdb-check", []), ("hdb-alter", ["pb", "--reorder", "a,b"])]
)
def test_target_runs_both_commands_on_the_release_asking_there(command, options, monkeypatch):
    """`--target` routes both, unclassified as changing the server: each shows its
    plan and asks THERE; the global --yes reaches the command as its own. Each
    with its own words: one it does not take does not parse, and asks (#1780)."""
    args = ["data", command, *options, "--apply"]
    assert not target_confirm.changes_the_server(args)
    seen = {}
    remote = control.Remote("uat", "uat-host", "/srv/uqf")
    monkeypatch.setattr(control, "remote_for", lambda name, root: remote)
    monkeypatch.setattr(control, "run", lambda r, a, tty: seen.setdefault("args", a) and 0)
    target.forward("uat", True, args, interactive=False)
    assert seen["args"] == [*args, "--yes"]
    sent = control.remote_command(remote, seen["args"])
    assert " ".join(["data", command, *options]) in sent and "--hdb" not in sent, (
        "the release's own HDB"
    )


@pytest.mark.parametrize("handle", ["--attach", "--hdb"])
def test_target_with_another_handle_is_refused(handle):
    with pytest.raises(UqsError, match="one of --target, --attach and --hdb"):
        target.forward("uat", False, ["data", "hdb-alter", "pb", handle, "x"], interactive=False)


def test_fix_is_refused_naming_apply(monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr(shared, "log", type("L", (), {
        "error": lambda self, m, *a: errors.append(m.format(*a)),
        "__getattr__": lambda self, n: lambda *a, **k: None})())  # fmt: skip
    result = CliRunner().invoke(app, ["data", "hdb-check", "--fix"])
    assert result.exit_code == 1
    assert errors and "--apply" in errors[0]
