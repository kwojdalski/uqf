"""`uqs job attachment add|remove|show NAME` and `uqs job attachment list` as an
operator runs them (#1424, #1457, #1737).

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
from attach_support import LEDGER_Q, FakeStack, FaultIpc, make_bundle, registered_at_runtime
from typer.core import TyperGroup
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_owner, config, facts, live, plan, publisher, service
from uqs.attach import verify as verifying
from uqs.attach.manifest import Manifest
from uqs.cli.common import shared, target_confirm
from uqs.deploy import control
from uqs.paths import repo_root
from uqs.stack import attach_inventory, attach_state, listing, runtime

TABLE = "piggybank_general_ledger"
SECRET = "s3cr3t-Pw!x"
REAL_START = publisher.UqsPublisher.start


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
            "--base-port", "6000", "--transient", *extra]  # fmt: skip


def run(*argv: str, debug: bool = False, **kw):
    pre = ["--debug"] if debug else []
    return CliRunner().invoke(
        cli.app, [*pre, "job", "attachment", *argv], catch_exceptions=False, **kw
    )


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


def test_attachment_is_a_noun_and_no_stage_or_status_command_remains():
    root = typer.main.get_command(cli.app)
    noun = _group(root, "job", "attachment")
    assert {"add", "remove", "show", "list", "contract"} <= set(noun.commands)
    for word in ("add", "remove", "show"):
        assert not isinstance(noun.commands[word], TyperGroup), word
    assert "attachments" not in _group(root, "job").commands
    options = {
        o
        for p in noun.commands["add"].params
        for o in [*getattr(p, "opts", []), *getattr(p, "secondary_opts", [])]
    }
    assert {"--dry-run", "--no-start", "--json", "--yes", "--accept-risks", "--override",
            "--reason", "--clear-overrides", "--replace"} <= options  # fmt: skip
    remove = {o for p in noun.commands["remove"].params for o in getattr(p, "opts", [])}
    assert {"--dry-run", "--yes", "--json"} <= remove
    assert "attachments" in listing.LISTABLE_KINDS


def test_show_is_one_attachment_saved_and_now(world):
    assert run("add", *new(world), "--yes").exit_code == 0
    doc = json.loads(run("show", "pb-uat", "--json").output)
    assert doc["attachment"]["name"] == "pb-uat"
    assert doc["attachment"]["publisher"]["job"] == "pb_general_ledger"
    assert doc["state"]["attachment"] == "pb-uat" and doc["state"]["state"] == "verified"
    human = flat(run("show", "pb-uat").output)
    assert "state: verified" in human and "pb_general_ledger" in human
    missing = run("show", "nosuch")
    assert missing.exit_code == 1 and "nosuch" in " ".join(world.errors)


# ------------------------------------------------------------------ dry run


def test_a_dry_run_shows_the_plan_and_changes_nothing(world):
    result = run("add", *new(world, "--dry-run"))
    assert result.exit_code == 0, result.output
    text = flat(result.output)
    assert "Affected components" in text and "Changes attach would make" in text
    assert "dry run: nothing was changed" in text
    assert not world.state_dir.exists(), "no config, no manifest, no lock"
    assert {op for _, op, _ in world.calls} == {"probe"} and world.started == []


def test_a_dry_run_of_a_blocked_stack_exits_one_and_still_saves_nothing(world):
    world.named("stp1")["multilog"] = "singular"
    result = run("add", *new(world, "--dry-run", "--json"))
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["ok"] is False and data["dry_run"] is True
    assert "LOGGING_MODE" in {f["code"] for f in data["plan"]["findings"]}
    assert not world.state_dir.exists()


def test_a_dry_run_of_a_saved_attachment_leaves_every_file_as_it_was(world):
    assert run("add", *new(world, "--no-start", "--yes")).exit_code == 0
    before, calls = snapshot(world.state_dir), len(changes(world))
    assert run("add", "pb-uat", "--dry-run").exit_code == 0
    assert run("remove", "pb-uat", "--dry-run").exit_code == 0
    assert snapshot(world.state_dir) == before
    assert len(changes(world)) == calls, "detach's own removal checks ran as dry runs only"


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_attach_refuses_an_attachment_whose_detach_is_in_progress_and_changes_nothing(world, extra):
    assert run("add", *new(world, "--no-start", "--yes")).exit_code == 0
    path = world.state_dir / "pb-uat" / "manifest.json"
    m = Manifest.load("pb-uat", world.state_dir)
    m.set_state("detaching", "set by the test")
    before, calls = snapshot(world.state_dir), len(changes(world))
    result = run("add", "pb-uat", "--yes", *extra)
    assert result.exit_code != 0
    text = flat(result.output + " ".join(world.errors))
    assert "is detaching" in text and "uqs job attachment remove pb-uat" in text
    assert snapshot(world.state_dir) == before and path.is_file()
    assert len(changes(world)) == calls


def test_a_dry_run_detach_creates_no_lock_file(world):
    assert run("add", *new(world, "--no-start", "--yes")).exit_code == 0
    lock = world.state_dir / "pb-uat" / ".lock"
    lock.unlink()
    result = run("remove", "pb-uat", "--dry-run")
    assert result.exit_code == 0, result.output
    assert not lock.exists()
    assert "dry run: nothing was changed" in flat(result.output)


def test_a_dry_run_shows_the_manifest_and_a_registration_the_stack_lost(world):
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    stp = world.named("stp1")
    stp["schemas"].pop(TABLE)  # a tickerplant restart without a persisted schema
    stp["owned"].pop(TABLE)
    data = json.loads(run("add", "pb-uat", "--dry-run", "--json").output)["plan"]
    assert data["manifest"]["state"] == "verified"
    assert any(
        line.startswith("registration-lost: stp1 no longer holds") for line in data["health"]
    )
    human = flat(run("add", "pb-uat", "--dry-run").output)
    assert "registration-lost" in human and "Manifest: verified" in human


def _restart_tp_without_schema(fake) -> None:
    """A tickerplant restart that drops a runtime-only registration, and the rdb
    and wdb restarted after it (TorQ does not reconnect them): the table, its
    ownership record and its subscriptions are gone everywhere."""
    stp = fake.named("stp1")
    for key in ("schemas", "owned", "subscribers", "logged", "updtab", "logfiles", "replay"):
        stp[key].pop(TABLE, None)
    for key in ("tables", "published", "names"):
        stp[key].remove(TABLE)
    for name in ("rdb1", "wdb1"):
        p = fake.named(name)
        p["schemas"].pop(TABLE)
        p["owned"].pop(TABLE)
        for key in ("subscribed", "names", "tables"):
            p[key].remove(TABLE)


def _refused_lost_registration(text: str) -> None:
    assert "pb_ledger1 is running, but the stack lost the registration" in text
    assert f"stp1 no longer holds {TABLE}" in text
    assert "rows pb_ledger1 published since that restart were dropped" in text
    assert "stop it first (`uqs stop pb_ledger1 --base-port 6000`)" in text
    assert "then run `uqs job attachment add pb-uat` again" in text
    assert "Nothing was saved or changed" in text
    assert "nothing to apply" not in text, "a refused run is not already attached and verified"


def test_a_rerun_while_the_publisher_runs_on_a_lost_registration_is_refused(world):
    """Transient, running, tickerplant restarted (#2765): the re-run the RUNTIME_ONLY
    warning names is refused up front - rows published since the restart were
    dropped, and apply refuses while the publisher runs - instead of re-verifying,
    failing, taking `verified` back and leaving the publisher running."""
    assert run("add", *new(world, "--yes")).exit_code == 0
    _restart_tp_without_schema(world)
    before, calls = snapshot(world.state_dir), len(changes(world))
    result = run("add", "pb-uat", "--yes")
    assert result.exit_code == 1, result.output
    _refused_lost_registration(flat(result.output + " ".join(world.errors)))
    assert snapshot(world.state_dir) == before, "nothing saved, nothing demoted"
    assert Manifest.load("pb-uat").state == "verified"
    assert len(changes(world)) == calls and world.started == ["pb_ledger1"]


def test_a_dry_run_on_a_lost_registration_with_the_publisher_running_is_not_ok(world):
    assert run("add", *new(world, "--yes")).exit_code == 0
    _restart_tp_without_schema(world)
    world.errors.clear()
    before = snapshot(world.state_dir)
    result = run("add", "pb-uat", "--dry-run", "--json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    _refused_lost_registration(flat(data["error"]))
    assert any(line.startswith("registration-lost: stp1") for line in data["plan"]["health"])
    human = run("add", "pb-uat", "--dry-run")
    assert human.exit_code == 1
    _refused_lost_registration(flat(human.output + " ".join(world.errors)))
    assert snapshot(world.state_dir) == before


def test_with_the_publisher_stopped_a_lost_registration_is_applied_again(world):
    assert run("add", *new(world, "--yes")).exit_code == 0
    _restart_tp_without_schema(world)
    world.started.clear()  # `uqs stop pb_ledger1 --base-port 6000`
    assert run("add", "pb-uat", "--dry-run").exit_code == 0
    result = run("add", "pb-uat", "--yes", "--json")
    assert result.exit_code == 0, result.output
    stages = {s["stage"]: s["status"] for s in json.loads(result.output)["stages"]}
    assert stages == {"save": "done", "apply": "done", "verify": "done", "start": "done"}
    assert TABLE in world.named("stp1")["schemas"], "registered again"
    assert world.started == ["pb_ledger1"]
    assert Manifest.load("pb-uat").state == "verified"


# ------------------------------------------------------------------ execution


def test_attach_asks_first_and_changes_nothing_on_no(world, terminal):
    result = run("add", *new(world), input="n\n")
    assert result.exit_code != 0
    assert "Attach pb-uat to stack 6000 as above?" in flat(result.output)
    assert "Affected components" in result.output, "the plan is shown before the question"
    assert changes(world) == [] and not world.state_dir.exists()


def test_attach_applies_verifies_and_starts_in_that_order(world):
    result = run("add", *new(world, "--yes"))
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
    result = run("add", *new(world, "--yes", "--json"))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [s["stage"] for s in data["stages"]] == ["save", "apply", "verify", "start"]
    assert [s["status"] for s in data["stages"]] == ["done"] * 4
    assert data["verify"]["ok"] is True and data["apply"]["state"] == "applied"


def test_no_start_leaves_a_verified_attachment_and_a_stopped_publisher(world):
    result = run("add", *new(world, "--yes", "--no-start", "--json"))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    last = data["stages"][-1]
    assert last["status"] == "skipped" and "--no-start" in last["detail"]
    assert world.started == [] and Manifest.load("pb-uat").state == "verified"


def test_a_rerun_converges_without_a_second_registration_or_publisher(world):
    assert run("add", *new(world, "--yes")).exit_code == 0
    first = changes(world)
    again = run("add", *new(world, "--yes", "--json"))
    assert again.exit_code == 0, again.output
    data = json.loads(again.output)
    assert changes(world) == first, "nothing was registered or subscribed twice"
    assert world.started == ["pb_ledger1"], "the publisher was not started twice"
    assert [s["status"] for s in data["stages"]] == ["skipped", "skipped", "done", "skipped"]
    assert run("add", "pb-uat", "--yes").exit_code == 0, "by name alone, too"


def saved_storage(fake) -> dict:
    return json.loads((fake.state_dir / "pb-uat" / config.CONFIG_FILE).read_text())["storage"]


def test_a_rerun_of_a_running_attachment_pins_the_hdb_it_left_unpinned(world):
    """Saved while the wdb reported no HDB, the attachment refuses every backfill
    (`pins no HDB directory`); re-running the attach is how that is put right, so it
    must pin the directory even though there is nothing to apply."""
    wdb = world.named("wdb1")
    hdbdir, wdb["hdbdir"] = wdb["hdbdir"], ""
    assert run("add", *new(world, "--yes")).exit_code == 0
    assert saved_storage(world)["hdb_dir"] == ""
    wdb["hdbdir"] = hdbdir
    first = changes(world)
    result = run("add", "pb-uat", "--yes", "--json")
    assert result.exit_code == 0, result.output
    stages = json.loads(result.output)["stages"]
    assert [s["status"] for s in stages] == ["done", "skipped", "done", "skipped"]
    assert "pinned hdb_dir" in stages[0]["detail"]
    assert saved_storage(world)["hdb_dir"] == "/data/hdb"
    assert changes(world) == first and world.started == ["pb_ledger1"]
    again = json.loads(run("add", "pb-uat", "--yes", "--json").output)["stages"]
    assert again[0]["status"] == "skipped", "a pinned directory is never re-pinned"


def test_an_attachment_without_its_wdb_pins_the_hdb_an_unmanaged_wdb_reports(world):
    assert run("add", *new(world, "--consumer", "rdb1", "--yes")).exit_code == 0
    storage = saved_storage(world)
    assert storage["hdb_dir"] == "/data/hdb" and storage["wdb_savedir"] == "/data/wdbhdb"


def test_wdbs_outside_the_attachment_that_disagree_pin_nothing(world):
    other = dict(world.named("wdb1"), procname="wdb2", port=6006, hdbdir=":/elsewhere")
    world.add(other)
    world.refresh_discovery()
    assert run("add", *new(world, "--consumer", "rdb1", "--yes")).exit_code == 0
    assert saved_storage(world)["hdb_dir"] == ""


REPIN = "`uqs job attachment add pb-uat --replace --repin-storage`"


def _restart_on_another_hdb(world) -> None:
    """The stack restarted on another HDB directory: its wdb reports it (#2767)."""
    world.named("wdb1")["hdbdir"] = ":/moved/hdb"
    world.named("rdb1")["hdbdir"] = ":/moved/hdb"


@pytest.mark.parametrize("consumers", [("--consumer", "rdb1"), ()], ids=["rdb1", "all"])
def test_a_moved_hdb_is_refused_everywhere_until_it_is_repinned(world, consumers):
    """The pin is taken from wdb1 whether or not the attachment manages it, so
    wdb1 moving its HDB is a STORAGE_MISMATCH either way, verify takes `verified`
    back, `data --attachment` refuses naming the re-pin, and the re-pin is the
    operator's act: shown old -> new, applied and verified again (#2767)."""
    from uqs.attach import hdb_where

    assert run("add", *new(world, *consumers, "--yes", "--no-start")).exit_code == 0
    assert saved_storage(world)["hdb_dir"] == "/data/hdb"
    _restart_on_another_hdb(world)

    planned = json.loads(run("add", "pb-uat", "--dry-run", "--json").output)["plan"]
    (found,) = [f for f in planned["findings"] if f["code"] == "STORAGE_MISMATCH"]
    assert found["message"] == "hdb_dir was recorded as /data/hdb but wdb1 reports /moved/hdb"
    assert REPIN in found["action"]
    checked = verifying.verify(config.load("pb-uat"), world)
    assert not checked.ok and Manifest.load("pb-uat").state == "applied"
    with pytest.raises(config.AttachmentError, match="wdb1 reports /moved/hdb") as refused:
        hdb_where.resolve("pb-uat", world, writes=False)
    assert REPIN in str(refused.value)
    plain = run("add", "pb-uat", "--replace", "--yes", "--no-start")
    assert plain.exit_code == 1, "a --replace that changes nothing never re-pins"
    assert saved_storage(world)["hdb_dir"] == "/data/hdb"

    result = run("add", "pb-uat", "--replace", "--repin-storage", "--yes", "--no-start")
    assert result.exit_code == 0, result.output
    assert "re-pin storage.hdb_dir: '/data/hdb' -> '/moved/hdb'" in flat(result.output)
    assert saved_storage(world)["hdb_dir"] == "/moved/hdb"
    m = Manifest.load("pb-uat")
    assert m.state == "verified"
    (event,) = [e for e in m.history if e["event"] == "configuration replaced"]
    assert "storage.hdb_dir: '/data/hdb' -> '/moved/hdb'" in event["detail"]
    assert hdb_where.resolve("pb-uat", world, writes=False).hdb == "/moved/hdb"


def test_a_repin_is_refused_while_the_publisher_runs(world):
    """A re-pin is a replacement: with the publisher running it cannot be applied
    and verified, so nothing is saved (#2760, #2767)."""
    assert run("add", *new(world, "--consumer", "rdb1", "--yes")).exit_code == 0
    _restart_on_another_hdb(world)
    result = run("add", "pb-uat", "--replace", "--repin-storage", "--yes")
    assert result.exit_code == 1
    (said,) = [m for m in world.errors if "pb_ledger1 is running" in m]
    assert "--repin-storage" in said and "Nothing was saved" in said
    assert saved_storage(world)["hdb_dir"] == "/data/hdb"


def test_a_repin_is_refused_while_a_backfill_owns_the_attachment(world):
    """A run keeps its state under the destination it was launched for: the
    pin it writes into cannot move under it (#2758, #2767)."""
    assert run("add", *new(world, "--consumer", "rdb1", "--yes", "--no-start")).exit_code == 0
    _restart_on_another_hdb(world)
    _a_backfill_runs()
    result = run("add", "pb-uat", "--replace", "--repin-storage", "--yes", "--no-start")
    assert result.exit_code == 1
    assert any("attached backfill running" in m and "then replace it" in m for m in world.errors)
    assert saved_storage(world)["hdb_dir"] == "/data/hdb"


def test_a_repin_needs_replace_and_one_location_to_pin(world):
    """Without --replace it is refused naming the command; with wdbs that
    disagree it is refused rather than left unpinned (#2767)."""
    assert run("add", *new(world, "--consumer", "rdb1", "--yes", "--no-start")).exit_code == 0
    assert run("add", "pb-uat", "--repin-storage", "--yes").exit_code == 1
    assert any("needs --replace" in m and REPIN in m for m in world.errors)
    world.add(dict(world.named("wdb1"), procname="wdb2", port=6006, hdbdir=":/elsewhere"))
    world.refresh_discovery()
    result = run("add", "pb-uat", "--replace", "--repin-storage", "--yes", "--no-start")
    assert result.exit_code == 1
    assert any(
        "reports no single wdb_savedir, hdb_dir now" in m and "wdb2 :/elsewhere" in m
        for m in world.errors
    )
    assert saved_storage(world)["hdb_dir"] == "/data/hdb"


def test_a_rerun_after_no_start_applies_nothing_and_then_starts(world):
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    first = changes(world)
    result = run("add", "pb-uat", "--yes", "--json")
    assert result.exit_code == 0, result.output
    steps = json.loads(result.output)["apply"]["steps"]
    assert {s["outcome"] for s in steps} == {"already"}
    assert changes(world) == first and world.started == ["pb_ledger1"]


def test_a_stack_that_changed_after_the_preview_is_refused_at_execution(world):
    assert run("add", *new(world, "--dry-run")).exit_code == 0
    world.named("stp1")["multilog"] = "singular"
    result = run("add", *new(world, "--yes"))
    assert result.exit_code == 1
    assert any("LOGGING_MODE" in m and "Nothing was changed" in m for m in world.errors)
    assert changes(world) == [] and not world.state_dir.exists()


def test_conflicting_options_for_a_saved_attachment_are_refused_unless_replaced(world):
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    moved = new(world, "--yes")
    moved[moved.index("6000")] = "6100"
    refused = run("add", *moved)
    assert refused.exit_code == 1
    assert any("saved with other settings" in m and "--base-port" in m for m in world.errors)
    replaced = run("add", *moved, "--replace")
    assert replaced.exit_code == 1, "its manifest holds changes on stack 6000"
    assert any("detach it first" in m for m in world.errors)
    assert config.load("pb-uat").stack.id == 6000
    assert Manifest.load("pb-uat").state == "verified", "the ownership record is untouched"


def test_a_replacement_keeps_every_option_it_does_not_give(world):
    """A transient attachment replaced with only --user given (#2762): it stays
    runtime-only - no block is written, the one thing --transient declined - and keeps
    its ssh and storage pins; what was replaced, and journalled, is --user alone."""
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    assert run("add", "pb-uat", "--ssh", "ops@box", "--yes", "--no-start").exit_code == 0
    pins = saved_storage(world)
    result = run("add", *new(world)[:-1], "--user", "bob", "--replace", "--yes", "--no-start",
                 debug=True)  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "replaced --user: 'admin' -> 'bob'" in flat(result.output)
    att = config.load("pb-uat")
    assert (att.persist, att.stack.user, att.stack.ssh) == ("runtime-only", "bob", "ops@box")
    assert saved_storage(world) == pins, "no pin dropped: nothing that decides one changed"
    m = Manifest.load("pb-uat")
    assert not [o for o in m.operations if o.op.startswith("schema_")]
    (event,) = [e for e in m.history if e["event"] == "configuration replaced"]
    assert event["detail"] == "--user: 'admin' -> 'bob'"
    assert m.state == "verified", "applied and verified again as replaced"


def test_a_replacement_saved_at_a_blocked_plan_is_no_longer_verified(world):
    """--clear-overrides saves at a blocked plan; a replacement saved with it is a
    configuration never applied, so the manifest must not keep `verified` (#2762)."""
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    world.named("stp1")["multilog"] = "singular"
    result = run("add", "pb-uat", "--user", "bob", "--replace", "--clear-overrides", "--yes")
    assert result.exit_code == 1
    assert any("LOGGING_MODE" in m and "Nothing was changed" in m for m in world.errors)
    assert config.load("pb-uat").stack.user == "bob"
    m = Manifest.load("pb-uat")
    assert m.state == "applied"
    assert m.history[-1]["detail"] == "the configuration was replaced: apply and verify it"


def test_a_replacement_apply_refuses_is_no_longer_verified(world, monkeypatch):
    """Saved, then refused by apply's own plan (the stack changed after the preview):
    the start gate and backfill trust `verified`, so it is taken back (#2762)."""
    from uqs.attach import apply as applying

    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0

    def refused(*args, **kwargs):
        raise applying.ApplyRefused([])

    monkeypatch.setattr(applying, "apply", refused)
    result = run("add", "pb-uat", "--user", "bob", "--replace", "--yes", "--no-start")
    assert result.exit_code == 1
    assert "nothing was changed on the stack" in flat(result.output)
    assert config.load("pb-uat").stack.user == "bob"
    assert Manifest.load("pb-uat").state == "applied"


def _a_backfill_runs(name: str = "pb-uat") -> Path:
    """An owner record naming a live process (this one), as launch writes it."""
    return backfill_owner.claim(name, "demo_deals_backfill", os.getpid(), "0123456789ab")


@pytest.mark.parametrize("dry", [False, True])
def test_a_replacement_is_refused_while_a_backfill_owns_the_attachment(world, dry):
    """A run reads its destination's state dir, and `stop` its owner record; a
    replacement that changed a pin would move what the config names under it
    (#2758). Refused as detach is - before anything is saved, on --dry-run too."""
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    record = _a_backfill_runs()
    before, first = snapshot(world.state_dir), changes(world)
    result = run("add", "pb-uat", "--user", "bob", "--replace", "--yes", "--no-start",
                 *(["--dry-run"] if dry else []))  # fmt: skip
    assert result.exit_code == 1, result.output
    (said,) = [m for m in world.errors if "attached backfill running" in m]
    assert "demo_deals_backfill (pid" in said and str(record) in said
    assert "then replace it. Nothing was changed." in said
    assert snapshot(world.state_dir) == before and changes(world) == first
    assert Manifest.load("pb-uat").state == "verified"


def test_a_replacement_save_rechecks_the_owner_under_the_lock(world, monkeypatch):
    """A run launched between the CLI's check and the save is still refused:
    workflow.save checks again under the lock launch claims under (#2758)."""
    from uqs.attach import workflow

    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    real = workflow.refuse_replacing_backfilled
    calls = []

    def late(resolved, state_dir=None):
        if not calls:
            calls.append(_a_backfill_runs())  # the run starts just after the first check
            return
        real(resolved, state_dir)

    monkeypatch.setattr(workflow, "refuse_replacing_backfilled", late)
    result = run("add", "pb-uat", "--user", "bob", "--replace", "--yes", "--no-start")
    assert result.exit_code == 1
    text = flat(result.output)
    assert "attached backfill running" in text and "nothing was saved" in text
    assert config.load("pb-uat").stack.user == "admin"
    assert Manifest.load("pb-uat").state == "verified"


def test_a_plain_rerun_while_a_backfill_runs_is_allowed(world):
    """No replacement: the saved pins are kept as they are, so the config is
    written back unchanged, and apply on a converged stack changes nothing - the
    run's destination cannot move under it (#2758)."""
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    _a_backfill_runs()
    saved, first = (world.state_dir / "pb-uat" / config.CONFIG_FILE).read_bytes(), changes(world)
    result = run("add", "pb-uat", "--yes", "--no-start")
    assert result.exit_code == 0, result.output
    assert (world.state_dir / "pb-uat" / config.CONFIG_FILE).read_bytes() == saved
    assert changes(world) == first


def test_a_new_attachment_needs_a_job_and_a_stack_id(world):
    result = run("add", "pb-uat", "--dry-run")
    assert result.exit_code == 1
    assert any("needs --job and --base-port" in m for m in world.errors)


def test_a_partial_apply_exits_nonzero_with_recovery_and_starts_nothing(world, monkeypatch):
    cut = FaultIpc(world, [("consumer_attach", 1, "before")])
    monkeypatch.setattr(service, "ipc_for", lambda att: cut)
    result = run("add", *new(world, "--yes"))
    assert result.exit_code == 1
    text = flat(result.output)
    assert "the publisher was not started" in text
    assert (
        "`uqs job attachment add pb-uat`" in text and "`uqs job attachment remove pb-uat`" in text
    )
    assert world.started == []
    m = Manifest.load("pb-uat")
    assert m.state == "partial"
    assert [o.status for o in m.operations] == ["done", "done", "pending"], "the records stay"
    monkeypatch.setattr(service, "ipc_for", lambda att: world)
    assert run("add", "pb-uat", "--yes").exit_code == 0, "and the same command recovers"
    assert world.started == ["pb_ledger1"]


def test_a_failed_verify_prevents_startup(world, monkeypatch):
    real = verifying.run
    monkeypatch.setattr(
        verifying, "run", lambda *a: real(*a) + [verifying.Check("rdb1: injected", False, "x")]
    )
    result = run("add", *new(world, "--yes", "--json"))
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["ok"] is False and data["stages"][-1]["stage"] == "verify"
    assert any("publisher (pb_ledger1) was not started" in line for line in data["recovery"])
    assert world.started == [] and Manifest.load("pb-uat").state == "applied"


def test_a_failed_start_is_reported_with_its_recovery(world, monkeypatch):
    def boom(self, att):
        raise config.AttachmentError("pb_ledger1 did not start (exit 3)")

    monkeypatch.setattr(publisher.UqsPublisher, "start", boom)
    result = run("add", *new(world, "--yes"))
    assert result.exit_code == 1
    assert "only the publisher's start failed" in flat(result.output)
    assert Manifest.load("pb-uat").state == "verified"


# ------------------------------------------------------------- confirmations


def test_json_execution_without_yes_refuses_with_a_json_error(world, terminal):
    """#2229: --json never asks, even with a terminal that would answer y -
    refused, exit 1, the one message every command gives."""
    refusal = "--json cannot ask for confirmation: add --yes to change the stack"
    result = run("add", *new(world, "--json"), input="y\n")
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["ok"] is False and refusal in data["error"]
    assert changes(world) == [] and not world.state_dir.exists()
    assert run("add", *new(world, "--yes")).exit_code == 0
    before = changes(world)
    detach = run("remove", "pb-uat", "--json", input="y\n")
    data = json.loads(detach.output)
    assert detach.exit_code == 1 and data["ok"] is False and refusal in data["error"]
    assert changes(world) == before and world.started == ["pb_ledger1"], "nothing was detached"


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
    result = run("add", *new(world, "--password-env", "ATTACH_PW", *mode), debug=debug)
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output and not any(SECRET in m for m in world.errors)
    if "--json" in mode:
        data = json.loads(result.output)
        assert data["plan"]["stack"]["password_env"] == "ATTACH_PW"
        assert ("debug" in data) is debug
    if "--json" in mode:
        gw = next(c for c in data["plan"]["components"] if c["process"] == "gw1")
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
        result = run("add", *new(world, "--password-env", "ATTACH_PW", "--dry-run", *extra))
        assert result.exit_code == 1
        assert SECRET not in result.output and not any(SECRET in m for m in world.errors)
    assert json.loads(result.output)["ok"] is False


# ------------------------------------------------------------------ debug


def test_debug_adds_resolution_checks_and_timings(world):
    result = run("add", *new(world, "--yes", "--no-start"), debug=True)
    assert result.exit_code == 0, result.output
    text = flat(result.output)
    assert "debug: configuration: new" in text and "debug: resolved: stack 6000" in text
    assert "Verify (reads only; publishes nothing)" in text, "every check, with --debug"
    assert "seconds" in text and "debug: total" in text


def test_debug_goes_through_remote_dispatch():
    target, yes, rest = control.split_global(
        ["--debug", "--target", "uat", "job", "attachment", "add", "pb-uat", "--dry-run"]
    )
    assert (target, yes) == ("uat", False)
    assert rest == ["--debug", "job", "attachment", "add", "pb-uat", "--dry-run"]
    remote = control.Remote("uat", "uat-host", "/srv/uqf")
    assert "--debug job attachment add pb-uat --dry-run" in control.remote_command(remote, rest)
    assert not target_confirm.changes_the_server(["job", "attachment", "list"])


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
    result = run("add", *new(world, "--yes"), debug=True)
    assert result.exit_code == 0, result.output
    assert seen == [("pb_ledger1", 6000, {"trace": False, "capture": True})], "no -trace, no -debug"
    assert "LOG_LEVEL" not in os.environ


# -------------------------------------------------------------------- detach


def test_detach_before_publication_rolls_back_and_says_so(world):
    assert run("add", *new(world, "--yes")).exit_code == 0
    result = run("remove", "pb-uat", "--yes", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["outcome"] == "rolled_back" and data["publisher_stopped"] is True
    assert world.started == []
    assert Manifest.load("pb-uat").state == "rolled_back"


def test_a_transient_detach_after_publication_says_a_restart_drops_the_table(world):
    """#2766: the output names what a restart loses, and a dry run afterwards does
    not tell a detached attachment to apply again."""
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    world.publish(TABLE, 3)
    result = run("remove", "pb-uat", "--yes")
    assert result.exit_code == 0, result.output
    text = flat(result.output)
    assert f"restart: {TABLE}: kept only until stp1 restarts" in text
    assert "let end of day run before stp1 restarts" in text
    for name in ("stp1", "rdb1", "wdb1"):
        world.named(name)["schemas"].pop(TABLE)
    data = json.loads(run("add", "pb-uat", "--dry-run", "--json").output)["plan"]
    assert data["manifest"]["state"] == "detached"
    assert data["health"] and not any("apply it again" in line for line in data["health"])
    assert all(line.startswith("detached: ") for line in data["health"])


# ------------------------------------------------------------ list attachments


def _list(*extra: str):
    return CliRunner().invoke(
        cli.app, ["job", "attachment", "list", *extra], catch_exceptions=False
    )


def test_list_attachments_shows_attachment_and_publisher_state_separately(world, monkeypatch):
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    monkeypatch.setattr(attach_inventory, "_port_state", lambda host, port: ("stopped", ""))
    before = snapshot(world.state_dir)
    result = _list("--json")
    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.output)["attachments"]
    assert row["attachment"] == "pb-uat" and row["job"] == "pb_general_ledger"
    assert row["publisher"] == "pb_ledger1" and row["stack"] == 6000
    assert row["host"] == "localhost" and row["persist"] == "runtime-only"
    assert row["state"] == "verified" and row["publisher_state"] == "stopped"
    assert snapshot(world.state_dir) == before, "listing changes nothing"
    monkeypatch.setattr(
        attach_inventory, "_port_state", lambda host, port: ("unreachable", "no route to host")
    )
    (row,) = json.loads(_list("--json").output)["attachments"]
    assert row["publisher_state"] == "unreachable" and row["state"] == "verified"
    assert "attachments (1)" in _list().output


def test_list_probes_the_publisher_here_not_on_a_remote_stacks_host(world, monkeypatch):
    """uqs starts the publisher on this machine (PUBLISHER_HOST), so a remote
    stack's host says nothing about whether it runs (#2768)."""
    assert run("add", *new(world, "--yes", "--no-start")).exit_code == 0
    config = world.state_dir / "pb-uat" / attach_state.CONFIG_FILE
    cfg = json.loads(config.read_text())
    cfg["stack"]["host"] = "torq-prod.example"
    config.write_text(json.dumps(cfg))
    probed: list[tuple[str, int]] = []

    def port_state(host, port):
        probed.append((host, port))
        return ("running", "") if host == attach_state.PUBLISHER_HOST else ("unreachable", "")

    monkeypatch.setattr(attach_inventory, "_port_state", port_state)
    (row,) = json.loads(_list("--json").output)["attachments"]
    assert row["host"] == "torq-prod.example" and row["publisher_state"] == "running"
    assert probed and all(host == "localhost" for host, _ in probed), probed


def test_the_publisher_port_state_tells_stopped_from_unreachable():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert attach_inventory._port_state("127.0.0.1", port)[0] == "running"
    assert attach_inventory._port_state("127.0.0.1", port)[0] == "stopped"
    assert attach_inventory._port_state("no-such-host.invalid", 1)[0] == "unreachable"


@pytest.mark.parametrize("stack_defines", [True, False], ids=["schema file", "runtime only"])
def test_a_durable_attach_over_a_table_someone_else_registered(world, tmp_path, stack_defines):
    """#2763: the table is already on the stack, with no ownership record. Apply
    writes no block for a table it did not create, so the plan must not promise one:
    when the stack's own schema file defines the table a restart restores it and
    the plan says so; when only a runtime registration holds it, the plan and
    verify both warn that a restart drops it, and the start gate asks the stack."""
    schema = tmp_path / "database.q"
    schema.write_text("trade:([] time:`timestamp$())\n" + f"{LEDGER_Q}\n" * stack_defines)
    text = schema.read_text()
    tp = world.named("stp1")
    tp["schemafile"] = str(schema)
    for d in ("tplogs", "wdb", "hdb"):
        (tmp_path / d).mkdir()
    tp["tplogdir"] = str(tmp_path / "tplogs")
    world.named("wdb1")["savedir"] = f":{tmp_path / 'wdb'}"
    world.named("wdb1")["hdbdir"] = f":{tmp_path / 'hdb'}"
    registered_at_runtime(world, TABLE)
    durable = [a for a in new(world) if a != "--transient"]
    result = run("add", *durable, "--yes", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    found = {f["code"]: f["message"] for f in data["plan"]["findings"]}
    assert "WILL_PERSIST" not in found
    assert not [c for c in data["plan"]["changes"] if c["process"] == "schema file"]
    assert [s["outcome"] for s in data["apply"]["steps"] if s["op"] == "stp_register"] == [
        "adopted"
    ]
    assert schema.read_text() == text, "apply wrote no block, as the plan said"
    m = Manifest.load("pb-uat", world.state_dir)
    if stack_defines:
        assert found["STACK_DEFINES"].startswith(f"{schema} defines {TABLE} outside any")
        assert "ADOPTED_RUNTIME_ONLY" not in found
        assert data["verify"]["warnings"] == []
    else:
        said = found["ADOPTED_RUNTIME_ONLY"]
        assert said.startswith(f"{TABLE} is runtime-only: it was registered on the tickerplant")
        assert data["verify"]["ok"] and data["verify"]["warnings"] == [said]
    att = config.load("pb-uat", world.state_dir)
    assert live.needed(att, m, facts.Host(lambda h, p: False, ("localhost",))) is not stack_defines
