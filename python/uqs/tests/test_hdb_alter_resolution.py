"""Which HDB `uqs data check` / `alter` act on, and the refusals before any q runs (#1755).

No q here: the resolution from --target, --attachment and --hdb, the writers check,
the reader reload and ssh's own failure are all decided in Python. What the q
script then does to a database is test_hdb_alter_cli.py's (and the q suite's,
tests/q/test_hdb_alter.q).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from attach_backfill_support import HdbStack, save_attachment
from attach_support import ListedHosts
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs.attach import backfill_owner, hdb_where, service
from uqs.attach import config as attach_config
from uqs.attach.config import AttachmentError
from uqs.attach.facts import Host
from uqs.cli.common import shared, target_confirm
from uqs.cli.common.shared import app
from uqs.cli.data import hdb_scope
from uqs.cli.target import target
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


# ------------------------------------------------------------------ --attachment


def test_the_pinned_hdb_is_used_and_named(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake, writes=True)
    assert where.hdb == str(hdb)
    assert "pinned storage.hdb_dir" in where.source
    assert where.ssh == "testhost", "the stack's host, as the current user, when none is set"


def test_without_a_pin_the_live_wdb_s_hdbdir_is_used_and_named(stack):
    fake, hdb = stack
    save_attachment("pb", hdb, pin=False)
    where = hdb_where.resolve("pb", fake, writes=False)
    assert where.hdb == str(hdb)
    assert "wdb1's .wdb.hdbdir" in where.source


def test_a_pin_the_live_wdb_contradicts_is_refused(stack, tmp_path):
    fake, hdb = stack
    save_attachment("pb", tmp_path / "elsewhere")
    with pytest.raises(AttachmentError, match=r"wdb1 reports .*ambiguous"):
        hdb_where.resolve("pb", fake, writes=True)


def test_no_pin_and_no_live_wdb_is_refused(stack):
    fake, hdb = stack
    save_attachment("pb", hdb, pin=False)
    fake.down.add(fake.named("wdb1")["port"])
    with pytest.raises(AttachmentError, match="no live wdb reports one"):
        hdb_where.resolve("pb", fake, writes=False)


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
    assert hdb_where.resolve("pb", fake, writes=True).table == "duckdb_deals"


# ------------------------------------------------------------------ writers


def test_a_live_backfill_owner_is_refused(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake, writes=True)
    backfill_owner.claim("pb", "duckdb_deals_backfill", os.getpid(), "abc")
    with pytest.raises(AttachmentError, match="attached backfill running"):
        hdb_where.refuse_running_writers(where, fake)


#: apply_plan hands the request to Handle.run, which these tests replace.
NO_REQUEST = cast(Request, None)


def _attach_handle(where) -> hdb_scope.Handle:
    return hdb_scope.Handle("attach", where.hdb, where.source, paths_for_root(UQF_ROOT), where)


def test_writers_are_checked_again_after_the_prompt_under_the_lock(stack, monkeypatch):
    """A backfill that starts while the operator reads the prompt is refused before
    the write, not only before the question (#2984)."""
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake, writes=True)
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)

    def answer_late(question, *, yes):
        backfill_owner.claim("pb", "duckdb_deals_backfill", os.getpid(), "abc")

    monkeypatch.setattr(hdb_scope.safety, "confirm", answer_late)
    ran = []
    monkeypatch.setattr(hdb_scope.Handle, "run", lambda self, req, apply: ran.append(req))
    with pytest.raises(AttachmentError, match="attached backfill running"):
        hdb_scope.apply_plan(_attach_handle(where), NO_REQUEST, as_json=False, yes=True, what="x")
    assert ran == [], "nothing was written"


def test_the_write_holds_the_attachment_lock(stack, monkeypatch):
    """While the write runs, a backfill or receiver start - which takes the same
    lock - is refused (#2984)."""
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake, writes=True)
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    monkeypatch.setattr(hdb_scope.safety, "confirm", lambda question, *, yes: None)
    monkeypatch.setattr(hdb_scope, "readers", lambda handle: [])

    def write(self, req, apply):
        with pytest.raises(AttachmentError, match="another uqs operation"):
            with attach_config.locked("pb"):
                pass
        return {"status": "applied"}

    monkeypatch.setattr(hdb_scope.Handle, "run", write)
    result = hdb_scope.apply_plan(
        _attach_handle(where), NO_REQUEST, as_json=False, yes=True, what="x"
    )
    assert result["status"] == "applied"


def test_an_end_of_day_reload_in_progress_is_refused(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake, writes=True)
    fake.state_over["wdb1"] = {"reloadcomplete": False}
    with pytest.raises(AttachmentError, match="end-of-day reload in progress"):
        hdb_where.refuse_running_writers(where, fake)
    fake.state_over["wdb1"] = {}
    hdb_where.refuse_running_writers(where, fake)


@pytest.mark.parametrize(
    ("now", "said"),
    [
        ("2026.10.16D23:30:00.000000000", "30 minutes away"),
        ("2026.10.16D00:20:00.000000000", "last end of day was 20 minutes ago"),
    ],
)
def test_a_change_near_end_of_day_is_refused(stack, now, said):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake, writes=True)
    fake.state_over["wdb1"] = {"now": now}
    with pytest.raises(AttachmentError, match=said):
        hdb_where.refuse_running_writers(where, fake)


def test_a_writer_that_does_not_report_its_clock_is_refused(stack):
    fake, hdb = stack
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", fake, writes=True)
    fake.state_over["wdb1"] = {"next_roll": ""}
    with pytest.raises(AttachmentError, match="does not report its clock"):
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
    where = hdb_where.resolve("pb", fake, writes=True)
    rows = hdb_where.reload_readers(where, fake)
    assert fake.reloaded == ["hdb1"]
    assert rows == [{"process": "hdb1", "ok": True, "outcome": "reloaded", "detail": ""}]


def test_a_reader_on_a_second_host_is_reloaded_where_discovery_lists_it(monkeypatch, tmp_path):
    """Asked at the stack's host, it read as not running and was skipped (#2907)."""
    monkeypatch.setenv("UQS_ATTACH_DIR", str(tmp_path / "attachments"))
    hdb = tmp_path / "hdb"
    hdb.mkdir()
    fake = ReloadingStack(hdb)
    fake.named("hdb1")["host"] = "hostb.example.com"
    fake.refresh_discovery()
    ipc = ListedHosts(fake)
    save_attachment("pb", hdb)
    where = hdb_where.resolve("pb", ipc, writes=True)
    rows = hdb_where.reload_readers(where, ipc)
    assert rows == [{"process": "hdb1", "ok": True, "outcome": "reloaded", "detail": ""}]
    assert [(host, op) for name, host, op in ipc.asked if name == "hdb1"] == [
        ("hostb.example.com", "hdb_reader"),
        ("hostb.example.com", "hdb_reload"),
    ]


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


def test_the_remote_script_carries_its_files_and_cleans_up():
    script = hdb_remote.remote_script(UQF_ROOT, Request("/d/hdb", "pb", "check"), apply=False)
    assert script.count("base64 -d") == 4, "hdb/alter.q, dbmaint.q and the two lock files"
    for f in ("src/etl/core/backfill_state.q", "src/etl/core/io_hdb.q"):
        assert f'> "$tmp/{f}"' in script, f
    assert "trap 'rm -rf \"$tmp\"' EXIT" in script
    assert "${QCMD:-q}" in script
    assert "-hdb /d/hdb -table pb -json -q" in script


@pytest.mark.parametrize(
    ("raised", "said"),
    [
        (KeyboardInterrupt(), "interrupted"),
        (subprocess.TimeoutExpired("ssh", 1), "did not finish"),
    ],
)
def test_an_interrupted_apply_says_the_remote_run_may_still_hold_the_lock(raised, said):
    def interrupted(argv, **kw):
        raise raised

    req = Request("/data/hdb", "pb", "reorder")
    with pytest.raises(UqsError) as caught:
        hdb_remote.run_ssh("torq@prod", "ssh", UQF_ROOT, req, apply=True, runner=interrupted)
    assert said in str(caught.value)
    assert "may still be running on torq@prod" in str(caught.value)
    assert "write lock" in str(caught.value)


def test_an_interrupted_plan_says_nothing_was_written():
    def interrupted(argv, **kw):
        raise KeyboardInterrupt

    req = Request("/data/hdb", "pb", "check")
    with pytest.raises(UqsError, match="nothing was written"):
        hdb_remote.run_ssh("torq@prod", "ssh", UQF_ROOT, req, apply=False, runner=interrupted)


# ------------------------------------------------------------------ handles


def test_two_handles_are_refused(monkeypatch):
    monkeypatch.setattr(hdb_scope, "_paths", lambda: paths_for_root(UQF_ROOT))
    with pytest.raises(UqsError, match="one of --target, --attachment and --hdb"):
        hdb_scope.resolve("pb", "/data/hdb", writes=False)


def test_with_no_handle_it_is_this_runtime_s_configured_hdb(monkeypatch):
    paths = paths_for_root(UQF_ROOT)
    monkeypatch.setattr(hdb_scope, "_paths", lambda: paths)
    handle = hdb_scope.resolve(None, None, writes=False)
    assert handle.kind == "runtime" and handle.hdb == str(paths.hdb_dir)
    assert "this runtime's HDB" in handle.source


@pytest.mark.parametrize(
    ("command", "options"), [("check", []), ("alter", ["pb", "--reorder", "a,b"])]
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


@pytest.mark.parametrize("handle", ["--attachment", "--hdb"])
def test_target_with_another_handle_is_refused(handle):
    with pytest.raises(UqsError, match="one of --target, --attachment and --hdb"):
        target.forward("uat", False, ["data", "alter", "pb", handle, "x"], interactive=False)


# ------------------------------------------------------------------ attachment state (#2769)

STATES = ["verified", "applied", "detached", "rolled_back", None]  # None: planned, no manifest


def _named(state: str | None) -> str:
    return state or "planned"


@pytest.mark.parametrize("pin", [True, False], ids=["pinned", "unpinned"])
@pytest.mark.parametrize("state", STATES, ids=_named)
def test_a_write_needs_a_verified_attachment_that_pins_its_hdb(stack, state, pin):
    """`data check --apply` / `data alter --attachment` hold the HDB to an attached
    backfill's rule: verified (require_verified's own refusal), then pinned."""
    fake, hdb = stack
    save_attachment("pb", hdb, state=state, pin=pin)
    if state != "verified":
        with pytest.raises(UqsError) as caught:
            hdb_where.resolve("pb", fake, writes=True)
        said = str(caught.value)
        assert f"attachment pb is {_named(state)}, not verified: a write to its HDB needs" in said
        assert "`uqs job attachment add pb --no-start`" in said
        assert ("a detached attachment's stack is not ours to write" in said) == (
            state == "detached"
        )
    elif not pin:
        with pytest.raises(AttachmentError) as caught:
            hdb_where.resolve("pb", fake, writes=True)
        said = str(caught.value)
        assert "attachment pb pins no HDB directory (storage.hdb_dir is empty)" in said
        assert "never falls back to a live wdb's .wdb.hdbdir" in said
        assert "re-run `uqs job attachment add pb` while a wdb of the stack is up" in said
        assert "--hdb DIR" in said
    else:
        where = hdb_where.resolve("pb", fake, writes=True)
        assert where.hdb == str(hdb) and "pinned storage.hdb_dir" in where.source


@pytest.mark.parametrize("pin", [True, False], ids=["pinned", "unpinned"])
@pytest.mark.parametrize("state", STATES, ids=_named)
def test_a_read_takes_any_attachment_state_and_the_live_wdb_s_hdb(stack, state, pin):
    """A `data check` plan writes nothing, so it resolves the HDB whatever the
    attachment's state - the pinned one, else the live wdb's."""
    fake, hdb = stack
    save_attachment("pb", hdb, state=state, pin=pin)
    where = hdb_where.resolve("pb", fake, writes=False)
    assert where.hdb == str(hdb)
    assert ("pinned storage.hdb_dir" if pin else "wdb1's .wdb.hdbdir") in where.source


@pytest.fixture
def detached_cli(stack, monkeypatch):
    """`uqs data ...` against a detached attachment `pb`, its q runs recorded, not made."""
    fake, hdb = stack
    save_attachment("pb", hdb, state="detached")
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    ran: list[bool] = []

    def run(self, req, *, apply):
        ran.append(apply)
        return {"status": "planned", "steps": [], "table": req.table}

    monkeypatch.setattr(hdb_scope.Handle, "run", run)
    errors: list[str] = []

    class Log:
        def error(self, message, *args):
            errors.append(str(message).format(*args))

        def __getattr__(self, _name):
            return lambda *a, **k: None

    monkeypatch.setattr(shared, "log", Log())
    return type("Cli", (), {"ran": ran, "errors": errors})


def test_check_s_plan_takes_a_detached_attachment(detached_cli):
    result = CliRunner().invoke(app, ["data", "check", "--attachment", "pb", "--json"])
    assert result.exit_code == 0, (result.output, detached_cli.errors)
    assert detached_cli.ran == [False], "the plan alone, which writes nothing"


@pytest.mark.parametrize(
    "argv",
    [
        ["data", "check", "--attachment", "pb", "--apply", "--yes"],
        ["data", "alter", "--attachment", "pb", "--reorder", "a,b"],
        ["data", "alter", "--attachment", "pb", "--reorder", "a,b", "--apply", "--yes"],
    ],
    ids=["check-apply", "alter-plan", "alter-apply"],
)
def test_a_writing_command_refuses_a_detached_attachment_before_any_q(detached_cli, argv):
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 1
    assert detached_cli.ran == [], "refused before the plan runs"
    said = " ".join(detached_cli.errors)
    assert "attachment pb is detached, not verified: a write to its HDB needs" in said
    assert "a detached attachment's stack is not ours to write" in said
