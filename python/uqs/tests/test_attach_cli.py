"""`uqs job attach NAME`, `uqs job detach NAME` and `uqs job attachment list` as an
operator runs them (#1424, #1457).

The stack is a scripted one that keeps state (attach_support.FakeStack); what is
under test is what the commands ask, print, refuse, change and exit with. The
command tree is read from the Typer object, never from rendered help text.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
import typer
from attach_support import FakeStack, FaultIpc, make_bundle
from typer.core import TyperGroup
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import config, facts, plan, publisher, service
from uqs.attach import verify as verifying
from uqs.attach.manifest import Manifest
from uqs.cli import attach_legacy, shared
from uqs.deploy import control
from uqs.paths import repo_root
from uqs.stack import attach_state, listing, runtime

TABLE = "piggybank_general_ledger"
SECRET = "s3cr3t-Pw!x"
REAL_START = publisher.UqsPublisher.start
STAGES = ("plan", "validate", "status", "apply", "verify", "start", "detach")


class _ErrorLog:
    """What `_die`/`log.error` was handed: loguru does not write into CliRunner's
    output, so asserting on the output alone would pass vacuously."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


class World(FakeStack):
    """The fake stack, and where this test's attachments and bundle live."""

    state_dir: Path
    bundle: Path


@pytest.fixture
def world(monkeypatch, tmp_path):
    fake = World(6000)
    fake.state_dir = tmp_path / "attachments"
    fake.bundle = make_bundle(tmp_path / "piggybank")
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(fake.state_dir))
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    # the publisher "listens" once started: on a port no stack process holds
    monkeypatch.setattr(
        facts, "_tcp_open", lambda host, port: bool(fake.started) and port not in fake.procs
    )
    errors = _ErrorLog()
    monkeypatch.setattr(shared, "log", errors)
    monkeypatch.setattr(shared, "configure_logging", lambda **kw: None)
    fake.errors = errors.messages
    monkeypatch.setattr(publisher.UqsPublisher, "start", lambda self, att: _started(fake, att))
    monkeypatch.setattr(publisher.UqsPublisher, "running", lambda self, att: bool(fake.started))
    monkeypatch.setattr(publisher.UqsPublisher, "stop", lambda self, att: fake.started.clear())
    return fake


def _started(fake, att) -> None:
    """What a real start does that the plan sees: the port listens, and the
    publisher registers with discovery."""
    port = plan.publisher_port(att, repo_root()).port
    fake.named("discovery1")["services"].append(
        {"procname": att.publisher.procname, "proctype": "pipeline", "hpup": f":testhost:{port}"}
    )
    fake.started.append(att.publisher.procname)


def new(fake, *extra: str) -> list[str]:
    """The first command line: a new attachment's identity."""
    return ["pb-uat", "--job", "pb_general_ledger", "--bundle", str(fake.bundle),
            "--stack-id", "6000", "--runtime-only", *extra]  # fmt: skip


def run(*argv: str, debug: bool = False, **kw):
    pre = ["--debug"] if debug else []
    return CliRunner().invoke(cli.app, [*pre, "job", *argv], catch_exceptions=False, **kw)


def changes(fake) -> list[tuple[str, str]]:
    """The calls that change a process, in order: (operation, process)."""
    names = {port: p["procname"] for port, p in fake.procs.items()}
    return [
        (op, names[port])
        for port, op, req in fake.calls
        if op != "probe" and not req.get("dry_run")
    ]


def snapshot(folder: Path) -> dict[str, bytes]:
    if not folder.exists():
        return {}
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def flat(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------ the command tree


def _group(command, *path: str) -> TyperGroup:
    for word in path:
        assert isinstance(command, TyperGroup)
        command = command.commands[word]
    assert isinstance(command, TyperGroup)
    return command


def test_attach_and_detach_are_commands_and_no_stage_or_status_command_remains():
    job = _group(typer.main.get_command(cli.app), "job")
    assert {"attach", "detach"} <= set(job.commands)
    for word in ("attach", "detach"):
        assert not isinstance(job.commands[word], TyperGroup), word
    assert "attachments" not in job.commands
    options = {o for p in job.commands["attach"].params for o in getattr(p, "opts", [])}
    assert {"--dry-run", "--no-start", "--json", "--yes", "--accept-risks", "--override",
            "--override-reason", "--clear-overrides", "--replace"} <= options  # fmt: skip
    detach = {o for p in job.commands["detach"].params for o in getattr(p, "opts", [])}
    assert {"--dry-run", "--yes", "--json"} <= detach
    assert "attachments" in listing.LISTABLE_KINDS


@pytest.mark.parametrize("stage", STAGES)
def test_every_old_stage_command_is_refused_with_the_command_that_replaced_it(world, stage):
    result = run("attach", stage, "pb-uat")
    assert result.exit_code == 1
    message = world.errors[-1]
    assert f"`uqs job attach {stage}` was replaced" in message
    new_command = attach_legacy.MIGRATION[stage][0].format(name="pb-uat")
    assert new_command in message
    assert not world.state_dir.exists() and changes(world) == []


# ------------------------------------------------------------------ dry run


def test_a_dry_run_shows_the_plan_and_changes_nothing(world):
    result = run("attach", *new(world, "--dry-run"))
    assert result.exit_code == 0, result.output
    text = flat(result.output)
    assert "Affected components" in text and "Changes attach would make" in text
    assert "dry run: nothing was saved or changed" in text
    assert not world.state_dir.exists(), "no config, no manifest, no lock"
    assert {op for _, op, _ in world.calls} == {"probe"} and world.started == []


def test_a_dry_run_of_a_blocked_stack_exits_one_and_still_saves_nothing(world):
    world.named("stp1")["multilog"] = "singular"
    result = run("attach", *new(world, "--dry-run", "--json"))
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["ok"] is False and data["dry_run"] is True
    assert "LOGGING_MODE" in {f["code"] for f in data["plan"]["findings"]}
    assert not world.state_dir.exists()


def test_a_dry_run_of_a_saved_attachment_leaves_every_file_as_it_was(world):
    assert run("attach", *new(world, "--no-start", "--yes")).exit_code == 0
    before, calls = snapshot(world.state_dir), len(changes(world))
    assert run("attach", "pb-uat", "--dry-run").exit_code == 0
    assert run("detach", "pb-uat", "--dry-run").exit_code == 0
    assert snapshot(world.state_dir) == before
    assert len(changes(world)) == calls, "detach's own removal checks ran as dry runs only"


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_attach_refuses_an_attachment_whose_detach_is_in_progress_and_changes_nothing(world, extra):
    assert run("attach", *new(world, "--no-start", "--yes")).exit_code == 0
    path = world.state_dir / "pb-uat" / "manifest.json"
    m = Manifest.load("pb-uat", world.state_dir)
    m.set_state("detaching", "set by the test")
    before, calls = snapshot(world.state_dir), len(changes(world))
    result = run("attach", "pb-uat", "--yes", *extra)
    assert result.exit_code != 0
    text = flat(result.output + " ".join(world.errors))
    assert "is detaching" in text and "uqs job detach pb-uat" in text
    assert snapshot(world.state_dir) == before and path.is_file()
    assert len(changes(world)) == calls


def test_a_dry_run_detach_creates_no_lock_file(world):
    assert run("attach", *new(world, "--no-start", "--yes")).exit_code == 0
    lock = world.state_dir / "pb-uat" / ".lock"
    lock.unlink()
    result = run("detach", "pb-uat", "--dry-run")
    assert result.exit_code == 0, result.output
    assert not lock.exists()
    assert "dry run: nothing was changed" in flat(result.output)


def test_a_dry_run_shows_the_manifest_and_a_registration_the_stack_lost(world):
    assert run("attach", *new(world, "--yes", "--no-start")).exit_code == 0
    stp = world.named("stp1")
    stp["schemas"].pop(TABLE)  # a tickerplant restart without a persisted schema
    stp["owned"].pop(TABLE)
    data = json.loads(run("attach", "pb-uat", "--dry-run", "--json").output)["plan"]
    assert data["manifest"]["state"] == "verified"
    assert any(
        line.startswith("registration-lost: stp1 no longer holds") for line in data["health"]
    )
    human = flat(run("attach", "pb-uat", "--dry-run").output)
    assert "registration-lost" in human and "Manifest: verified" in human


# ------------------------------------------------------------------ execution


def test_attach_asks_first_and_changes_nothing_on_no(world):
    result = run("attach", *new(world), input="n\n")
    assert result.exit_code != 0
    assert "Attach pb-uat to stack 6000 as above?" in flat(result.output)
    assert "Affected components" in result.output, "the plan is shown before the question"
    assert changes(world) == [] and not world.state_dir.exists()


def test_attach_applies_verifies_and_starts_in_that_order(world):
    result = run("attach", *new(world, "--yes"))
    assert result.exit_code == 0, result.output
    assert changes(world) == [
        ("stp_register", "stp1"), ("consumer_attach", "rdb1"), ("consumer_attach", "wdb1"),
    ]  # fmt: skip
    text = flat(result.output)
    assert "Affected components" in text, "--yes does not hide the plan"
    assert "Stages" in text and "attached" in text
    assert world.started == ["pb_ledger1"]
    assert Manifest.load("pb-uat").state == "verified"


def test_the_stages_are_reported_in_order_in_json(world):
    result = run("attach", *new(world, "--yes", "--json"))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [s["stage"] for s in data["stages"]] == ["save", "apply", "verify", "start"]
    assert [s["status"] for s in data["stages"]] == ["done"] * 4
    assert data["verify"]["ok"] is True and data["apply"]["state"] == "applied"


def test_no_start_leaves_a_verified_attachment_and_a_stopped_publisher(world):
    result = run("attach", *new(world, "--yes", "--no-start", "--json"))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    last = data["stages"][-1]
    assert last["status"] == "skipped" and "--no-start" in last["detail"]
    assert world.started == [] and Manifest.load("pb-uat").state == "verified"


def test_a_rerun_converges_without_a_second_registration_or_publisher(world):
    assert run("attach", *new(world, "--yes")).exit_code == 0
    first = changes(world)
    again = run("attach", *new(world, "--yes", "--json"))
    assert again.exit_code == 0, again.output
    data = json.loads(again.output)
    assert changes(world) == first, "nothing was registered or subscribed twice"
    assert world.started == ["pb_ledger1"], "the publisher was not started twice"
    assert [s["status"] for s in data["stages"]] == ["skipped", "skipped", "done", "skipped"]
    assert run("attach", "pb-uat", "--yes").exit_code == 0, "by name alone, too"


def saved_storage(fake) -> dict:
    return json.loads((fake.state_dir / "pb-uat" / config.CONFIG_FILE).read_text())["storage"]


def test_a_rerun_of_a_running_attachment_pins_the_hdb_it_left_unpinned(world):
    """Saved while the wdb reported no HDB, the attachment refuses every backfill
    (`pins no HDB directory`); re-running the attach is how that is put right, so it
    must pin the directory even though there is nothing to apply."""
    wdb = world.named("wdb1")
    hdbdir, wdb["hdbdir"] = wdb["hdbdir"], ""
    assert run("attach", *new(world, "--yes")).exit_code == 0
    assert saved_storage(world)["hdb_dir"] == ""
    wdb["hdbdir"] = hdbdir
    first = changes(world)
    result = run("attach", "pb-uat", "--yes", "--json")
    assert result.exit_code == 0, result.output
    stages = json.loads(result.output)["stages"]
    assert [s["status"] for s in stages] == ["done", "skipped", "done", "skipped"]
    assert "pinned hdb_dir" in stages[0]["detail"]
    assert saved_storage(world)["hdb_dir"] == "/data/hdb"
    assert changes(world) == first and world.started == ["pb_ledger1"]
    again = json.loads(run("attach", "pb-uat", "--yes", "--json").output)["stages"]
    assert again[0]["status"] == "skipped", "a pinned directory is never re-pinned"


def test_an_attachment_without_its_wdb_pins_the_hdb_an_unmanaged_wdb_reports(world):
    assert run("attach", *new(world, "--consumer", "rdb1", "--yes")).exit_code == 0
    storage = saved_storage(world)
    assert storage["hdb_dir"] == "/data/hdb" and storage["wdb_savedir"] == "/data/wdbhdb"


def test_wdbs_outside_the_attachment_that_disagree_pin_nothing(world):
    other = dict(world.named("wdb1"), procname="wdb2", port=6006, hdbdir=":/elsewhere")
    world.add(other)
    world.refresh_discovery()
    assert run("attach", *new(world, "--consumer", "rdb1", "--yes")).exit_code == 0
    assert saved_storage(world)["hdb_dir"] == ""


def test_a_rerun_after_no_start_applies_nothing_and_then_starts(world):
    assert run("attach", *new(world, "--yes", "--no-start")).exit_code == 0
    first = changes(world)
    result = run("attach", "pb-uat", "--yes", "--json")
    assert result.exit_code == 0, result.output
    steps = json.loads(result.output)["apply"]["steps"]
    assert {s["outcome"] for s in steps} == {"already"}
    assert changes(world) == first and world.started == ["pb_ledger1"]


def test_a_stack_that_changed_after_the_preview_is_refused_at_execution(world):
    assert run("attach", *new(world, "--dry-run")).exit_code == 0
    world.named("stp1")["multilog"] = "singular"
    result = run("attach", *new(world, "--yes"))
    assert result.exit_code == 1
    assert any("LOGGING_MODE" in m and "Nothing was changed" in m for m in world.errors)
    assert changes(world) == [] and not world.state_dir.exists()


def test_conflicting_options_for_a_saved_attachment_are_refused_unless_replaced(world):
    assert run("attach", *new(world, "--yes", "--no-start")).exit_code == 0
    moved = new(world, "--yes")
    moved[moved.index("6000")] = "6100"
    refused = run("attach", *moved)
    assert refused.exit_code == 1
    assert any("saved with other settings" in m and "--stack-id" in m for m in world.errors)
    replaced = run("attach", *moved, "--replace")
    assert replaced.exit_code == 1, "its manifest holds changes on stack 6000"
    assert any("detach it first" in m for m in world.errors)
    assert config.load("pb-uat").stack.id == 6000
    assert Manifest.load("pb-uat").state == "verified", "the ownership record is untouched"


def test_a_new_attachment_needs_a_job_and_a_stack_id(world):
    result = run("attach", "pb-uat", "--dry-run")
    assert result.exit_code == 1
    assert any("needs --job and --stack-id" in m for m in world.errors)


def test_a_partial_apply_exits_nonzero_with_recovery_and_starts_nothing(world, monkeypatch):
    cut = FaultIpc(world, [("consumer_attach", 1, "before")])
    monkeypatch.setattr(service, "ipc_for", lambda att: cut)
    result = run("attach", *new(world, "--yes"))
    assert result.exit_code == 1
    text = flat(result.output)
    assert "the publisher was not started" in text
    assert "`uqs job attach pb-uat`" in text and "`uqs job detach pb-uat`" in text
    assert world.started == []
    m = Manifest.load("pb-uat")
    assert m.state == "partial"
    assert [o.status for o in m.operations] == ["done", "done", "pending"], "the records stay"
    monkeypatch.setattr(service, "ipc_for", lambda att: world)
    assert run("attach", "pb-uat", "--yes").exit_code == 0, "and the same command recovers"
    assert world.started == ["pb_ledger1"]


def test_a_failed_verify_prevents_startup(world, monkeypatch):
    real = verifying.run
    monkeypatch.setattr(
        verifying, "run", lambda *a: real(*a) + [verifying.Check("rdb1: injected", False, "x")]
    )
    result = run("attach", *new(world, "--yes", "--json"))
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["ok"] is False and data["stages"][-1]["stage"] == "verify"
    assert any("publisher (pb_ledger1) was not started" in line for line in data["recovery"])
    assert world.started == [] and Manifest.load("pb-uat").state == "applied"


def test_a_failed_start_is_reported_with_its_recovery(world, monkeypatch):
    def boom(self, att):
        raise config.AttachmentError("pb_ledger1 did not start (exit 3)")

    monkeypatch.setattr(publisher.UqsPublisher, "start", boom)
    result = run("attach", *new(world, "--yes"))
    assert result.exit_code == 1
    assert "only the publisher's start failed" in flat(result.output)
    assert Manifest.load("pb-uat").state == "verified"


# ------------------------------------------------------------- confirmations


def test_json_execution_without_yes_refuses_with_a_json_error(world):
    result = run("attach", *new(world, "--json"))
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["ok"] is False and "--yes" in data["error"]
    assert changes(world) == [] and not world.state_dir.exists()
    detach = run("detach", "pb-uat", "--json")
    assert detach.exit_code == 1 and json.loads(detach.output)["ok"] is False


# ---------------------------------------------------------------- redaction


@pytest.mark.parametrize(
    "mode",
    [
        ["--dry-run"],
        ["--dry-run", "--json"],
        ["--yes"],
        ["--yes", "--json"],
        ["--yes", "--no-start"],
    ],
)
@pytest.mark.parametrize("debug", [False, True])
def test_the_credential_is_redacted_in_every_output_mode(world, monkeypatch, mode, debug):
    monkeypatch.setenv("ATTACH_PW", SECRET)
    disc = world.named("discovery1")
    disc["services"].append(
        {"procname": "gw1", "proctype": "gateway", "hpup": f":testhost:6007:admin:{SECRET}"}
    )
    result = run("attach", *new(world, "--password-env", "ATTACH_PW", *mode), debug=debug)
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output and not any(SECRET in m for m in world.errors)
    if "--json" in mode:
        data = json.loads(result.output)
        assert data["plan"]["stack"]["password_env"] == "ATTACH_PW"
        assert ("debug" in data) is debug
    if "--json" in mode:
        gw = next(c for c in data["plan"]["components"] if c["procname"] == "gw1")
        assert gw["endpoint"] == ":testhost:6007:admin:***"
        if debug:
            assert any(line.startswith("configuration: new") for line in data["debug"])
    else:
        assert "***" in result.output


def test_an_error_carrying_the_credential_is_redacted(world, monkeypatch):
    monkeypatch.setenv("ATTACH_PW", SECRET)

    def leak(*a, **kw):
        raise config.AttachmentError(f"q error: 'access for admin:{SECRET}")

    monkeypatch.setattr(service, "make_plan", leak)
    for extra in ([], ["--json"]):
        result = run("attach", *new(world, "--password-env", "ATTACH_PW", "--dry-run", *extra))
        assert result.exit_code == 1
        assert SECRET not in result.output and not any(SECRET in m for m in world.errors)
    assert json.loads(result.output)["ok"] is False


# ------------------------------------------------------------------ debug


def test_debug_adds_resolution_checks_and_timings(world):
    result = run("attach", *new(world, "--yes", "--no-start"), debug=True)
    assert result.exit_code == 0, result.output
    text = flat(result.output)
    assert "debug: configuration: new" in text and "debug: resolved: stack 6000" in text
    assert "Verify (reads only; publishes nothing)" in text, "every check, with --debug"
    assert "seconds" in text and "debug: total" in text


def test_debug_goes_through_remote_dispatch():
    target, yes, rest = control.split_global(
        ["--debug", "--target", "uat", "job", "attach", "pb-uat", "--dry-run"]
    )
    assert (target, yes) == ("uat", False)
    assert rest == ["--debug", "job", "attach", "pb-uat", "--dry-run"]
    remote = control.Remote("uat", "uat-host", "/srv/uqf")
    assert "--debug job attach pb-uat --dry-run" in control.remote_command(remote, rest)
    assert not control.changes_the_server(["job", "attachment", "list"])


def test_debug_does_not_turn_on_the_publishers_runtime_logging(world, monkeypatch):
    seen: list[tuple] = []

    def fake_start(paths, names, base_port=None, **kw):
        from types import SimpleNamespace

        seen.append((names, base_port, kw))
        world.started.append(names)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runtime, "start", fake_start)
    monkeypatch.setattr(publisher.UqsPublisher, "start", REAL_START)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    result = run("attach", *new(world, "--yes"), debug=True)
    assert result.exit_code == 0, result.output
    assert seen == [("pb_ledger1", 6000, {"capture": True})], "no extras, no -debug/-verbose"
    assert "LOG_LEVEL" not in os.environ


# -------------------------------------------------------------------- detach


def test_detach_before_publication_rolls_back_and_says_so(world):
    assert run("attach", *new(world, "--yes")).exit_code == 0
    result = run("detach", "pb-uat", "--yes", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["outcome"] == "rolled_back" and data["publisher_stopped"] is True
    assert world.started == []
    assert Manifest.load("pb-uat").state == "rolled_back"


# ------------------------------------------------------------ list attachments


def _list(*extra: str):
    return CliRunner().invoke(
        cli.app, ["job", "attachment", "list", *extra], catch_exceptions=False
    )


def test_list_attachments_shows_attachment_and_publisher_state_separately(world, monkeypatch):
    assert run("attach", *new(world, "--yes", "--no-start")).exit_code == 0
    monkeypatch.setattr(attach_state, "_port_state", lambda host, port: ("stopped", ""))
    before = snapshot(world.state_dir)
    result = _list("--json")
    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.output)
    assert row["attachment"] == "pb-uat" and row["job"] == "pb_general_ledger"
    assert row["publisher"] == "pb_ledger1" and row["stack"] == "6000"
    assert row["host"] == "localhost" and row["persist"] == "runtime-only"
    assert row["state"] == "verified" and row["publisher_state"] == "stopped"
    assert snapshot(world.state_dir) == before, "listing changes nothing"
    monkeypatch.setattr(
        attach_state, "_port_state", lambda host, port: ("unreachable", "no route to host")
    )
    (row,) = json.loads(_list("--json").output)
    assert row["publisher_state"] == "unreachable" and row["state"] == "verified"
    assert "attachments (1)" in _list().output


def test_the_publisher_port_state_tells_stopped_from_unreachable():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert attach_state._port_state("127.0.0.1", port)[0] == "running"
    assert attach_state._port_state("127.0.0.1", port)[0] == "stopped"
    assert attach_state._port_state("no-such-host.invalid", 1)[0] == "unreachable"
