"""`uqs job attachment add --process-file`: the publisher registered in the stack's
own TorQ process file, so a native `start all` restarts ingestion too (#1845).

The stack is the scripted FakeStack, the site's process file a real file with
what production ones carry (CRLF, `taskset_no`, a quoted field). What is under
test is what the commands write, refuse, print and keep.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from attach_native_support import (
    PROC,
    SECRET,
    SITE,
    make_world,
    new,
    rows_for,
    run,
    said,
)

from uqs.attach import config, native
from uqs.attach.manifest import Manifest

TABLE = "piggybank_general_ledger"


@pytest.fixture
def world(monkeypatch, tmp_path):
    return make_world(monkeypatch, tmp_path)


def _registered(world) -> str:
    (line,) = rows_for(world.registry)
    return line


def _without_ours(world) -> bytes:
    """The file with the publisher's row taken out: what must equal the original."""
    data = world.registry.read_bytes()
    return b"".join(
        line for line in data.splitlines(keepends=True) if f",{PROC},".encode() not in line
    )


def _start_all(path) -> list[str]:
    """What torq.sh's `start all` starts: every line after the header whose
    startwithall field, found by its header the way torq.sh's awk does, is 1."""
    if shutil.which("awk") is None:
        pytest.skip("awk is not installed")
    prog = 'NR==1{for(i=1;i<=NF;i++) if($i=="startwithall") c=i; next} $c=="1"{print $4}'
    out = subprocess.run(["awk", "-F,", prog, str(path)], capture_output=True, text=True)
    return out.stdout.split()


# ------------------------------------------------------------------- preview


def test_dry_run_writes_nothing_and_previews_every_file_writer_and_policy(world):
    before = world.registry.read_bytes()
    result = run("add", *new(world), "--dry-run", "--json")
    assert result.exit_code == 0, result.output
    assert world.registry.read_bytes() == before
    assert not world.state_dir.exists() and not world.started
    native_preview = json.loads(result.output)["plan"]["native"]
    files = {f["file"]: f for f in native_preview["files"]}
    assert files[str(world.registry)]["writer"] == "cli"
    assert files[str(world.registry)]["change"] == "add the publisher's row"
    assert files[str(world.schema)]["writer"] == "cli"
    assert files[str(world.schema)]["host"] == "localhost"
    (launcher,) = [f for f in files if f.endswith("native_start.sh")]
    assert files[launcher]["change"] == "native start launcher (runs the start gate)"
    assert native_preview["publisher"]["process"] == PROC
    assert native_preview["publisher"]["proctype"] == "metrics"
    assert native_preview["start_with_all"] is False
    assert native_preview["startup"].startswith("on demand (startwithall=0)")


def test_without_process_file_the_preview_says_native_start_does_not_start_it(world):
    result = run("add", *new(world)[:-2], "--dry-run", "--json")
    preview = json.loads(result.output)["plan"]["native"]
    assert preview["startup"] == (
        "uqs only: no --process-file, so a native `start all` does not start it"
    )
    assert [f["file"] for f in preview["files"]] == [str(world.schema)]


# --------------------------------------------------------- apply and reapply


def test_add_registers_once_and_keeps_every_other_byte(world):
    before = world.registry.read_bytes()
    result = run("add", *new(world), "--yes")
    assert result.exit_code == 0, result.output
    line = _registered(world)
    assert _without_ours(world) == before
    raw = world.registry.read_bytes()
    assert raw.endswith(line.encode() + b"\r\n")  # the file's own line ending
    port = json.loads(run("add", "pb-uat", "--dry-run", "--json").output)["plan"]["publisher_port"]
    fields = line.split(",")
    header = before.decode().splitlines()[0].split(",")
    row = dict(zip(header, fields, strict=True))
    assert row["port"] == f"{{KDBBASEPORT}}+{port - 6000}"
    assert row["load"] == (
        "/opt/uqf/scripts/processes/torq_pipeline.q /opt/uqf/scripts/processes/torq_stream.q"
    )
    assert row["U"] == "${TORQAPPHOME}/appconfig/passwords/accesslist.txt"
    assert row["startwithall"] == "0" and row["taskset_no"] == ""
    assert row["qcmd"].endswith("/attachments/pb-uat/native_start.sh")
    again = run("add", *new(world), "--yes")
    assert again.exit_code == 0, again.output
    assert rows_for(world.registry) == [line]
    record = Manifest.load("pb-uat").registrations["process_row"]
    assert record["status"] == "done" and record["value"] == line


def test_a_file_without_a_final_newline_gets_one_before_the_row(world):
    world.registry.write_bytes(SITE.encode().rstrip(b"\r\n"))
    assert run("add", *new(world), "--yes").exit_code == 0
    assert world.registry.read_bytes().startswith(SITE.encode())
    assert _without_ours(world) == SITE.encode()


def test_startwithall_is_off_for_a_new_row_then_on_kept_when_omitted_and_off(world):
    assert run("add", *new(world), "--yes").exit_code == 0
    assert _registered(world).split(",")[10] == "0"
    assert PROC not in _start_all(world.registry)
    on = run("add", "pb-uat", "--start-with-all", "--yes")
    assert on.exit_code == 0, on.output
    assert _registered(world).split(",")[10] == "1"
    assert PROC in _start_all(world.registry)
    assert config.load("pb-uat").native.start_with_all is True
    kept = run("add", "pb-uat", "--yes")
    assert kept.exit_code == 0 and _registered(world).split(",")[10] == "1"
    off = run("add", "pb-uat", "--no-start-with-all", "--yes")
    assert off.exit_code == 0, off.output
    assert _registered(world).split(",")[10] == "0"
    assert config.load("pb-uat").native.start_with_all is False
    assert PROC not in _start_all(world.registry)
    assert set(_start_all(world.registry)) == {"discovery1", "stp1", "rdb1"}


def test_no_start_registers_and_does_not_change_the_saved_policy(world):
    result = run("add", *new(world), "--start-with-all", "--no-start", "--yes")
    assert result.exit_code == 0, result.output
    assert not world.started
    assert _registered(world).split(",")[10] == "1"


def test_start_with_all_is_refused_with_runtime_only(world):
    result = run("add", *new(world), "--transient", "--start-with-all", "--yes")
    assert result.exit_code == 1
    assert "--start-with-all needs durable table registration" in said(world, result)
    assert rows_for(world.registry) == []


def _saved(world) -> dict[str, bytes]:
    """Everything a refused command must leave as it was."""
    folder = world.state_dir / "pb-uat"
    files = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    return files | {"registry": world.registry.read_bytes(), "schema": world.schema.read_bytes()}


@pytest.mark.parametrize("extra", [(), ("--start-with-all",)])
def test_a_replacement_while_the_publisher_runs_is_refused_and_saves_nothing(world, extra):
    """A transient attachment, running, replaced with a durable writer (#2760): apply
    refuses while the publisher runs, so the replacement - which only an apply would
    carry out (a block in the schema file) - is refused before anything is saved,
    not saved as if applied while no block is written and startwithall=1 is."""
    assert run("add", *new(world), "--transient", "--yes").exit_code == 0
    assert world.started == [PROC]
    before = _saved(world)
    refused = run("add", *new(world), "--schema-writer", "cli", *extra, "--replace", "--yes")
    assert refused.exit_code == 1, refused.output
    text = said(world, refused)
    assert f"{PROC} is running" in text and "--schema-writer: 'runtime-only' -> 'cli'" in text
    assert ("--start-with-all: False -> True" in text) == bool(extra)
    assert f"stop it first (`uqs stop {PROC} --base-port 6000`)" in text
    assert "Nothing was saved" in text
    assert _saved(world) == before, "no configuration, manifest, row or block changed"
    assert config.load("pb-uat").persist == "runtime-only"
    assert world.started == [PROC]
    again = run("add", "pb-uat", "--yes", "--json")
    assert again.exit_code == 0, again.output
    stages = {s["stage"]: s["status"] for s in json.loads(again.output)["stages"]}
    assert stages == {"save": "skipped", "apply": "skipped", "verify": "done",
                      "register": "done", "start": "skipped"}  # fmt: skip
    assert _saved(world) == before, "a plain re-run of a running attachment changes nothing"


def test_the_same_replacement_with_the_publisher_stopped_applies_it(world):
    assert run("add", *new(world), "--transient", "--yes").exit_code == 0
    world.started.clear()
    result = run(
        "add", *new(world), "--schema-writer", "cli", "--start-with-all", "--replace",
        "--yes", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    stages = {s["stage"]: s["status"] for s in json.loads(result.output)["stages"]}
    assert stages["apply"] == "done" and stages["register"] == "done"
    assert "uqs-attach" in world.schema.read_text(), "the block a restart restores from"
    assert _registered(world).split(",")[10] == "1"
    m = Manifest.load("pb-uat")
    assert any(o.op == "schema_block" and o.status == "done" for o in m.operations)
    (event,) = [e for e in m.history if e["event"] == "configuration replaced"]
    assert "--schema-writer: 'runtime-only' -> 'cli'" in event["detail"]
    assert config.load("pb-uat").persist == "schema-file"


def test_a_durable_attachment_replaced_as_transient_before_publication_loses_its_block(world):
    """--transient keeps no block (#2762): a durable attachment replaced so, before
    anything was published, has the block it wrote removed - hash checked, shown in the
    plan - and its record reverted, not left in the file under a runtime-only config."""
    original = world.schema.read_bytes()
    assert run("add", *new(world), "--yes", "--no-start").exit_code == 0
    assert "uqs-attach" in world.schema.read_text()
    shown = run("add", *new(world), "--transient", "--replace", "--dry-run", "--json")
    assert shown.exit_code == 0, shown.output
    (removal,) = [c for c in json.loads(shown.output)["plan"]["changes"]
                  if c["action"].startswith("remove this attachment's block")]  # fmt: skip
    assert removal["action"] == (
        f"remove this attachment's block from {world.schema} (--transient keeps none)"
    )
    result = run("add", *new(world), "--transient", "--replace", "--yes", "--no-start", "--json")
    assert result.exit_code == 0, result.output
    steps = json.loads(result.output)["apply"]["steps"]
    assert [s["outcome"] for s in steps if s["op"] == "schema_block"] == ["removed"]
    assert world.schema.read_bytes() == original
    (op,) = [o for o in Manifest.load("pb-uat").operations if o.op == "schema_block"]
    assert op.status == "reverted"
    assert Manifest.load("pb-uat").state == "verified"
    assert config.load("pb-uat").persist == "runtime-only"


def test_a_durable_attachment_replaced_as_transient_after_publication_is_refused(world):
    """After publication a block is kept, as detach keeps it: a restart and the log's
    replay need it. So the --transient replacement is refused, and nothing is saved."""
    assert run("add", *new(world), "--yes", "--no-start").exit_code == 0
    world.publish(TABLE)
    before = _saved(world)
    result = run("add", *new(world), "--transient", "--replace", "--yes", "--no-start")
    assert result.exit_code == 1
    text = said(world, result)
    assert "BLOCK_AFTER_PUBLICATION (schema)" in text
    assert "its tables were published" in text and "the tickerplant counted messages" in text
    assert "--replace --schema-writer cli" in text
    assert _saved(world) == before, "no configuration, manifest or block changed"
    assert config.load("pb-uat").persist == "schema-file"


def test_start_with_all_is_refused_for_a_durable_attachment_a_restart_would_not_restore(world):
    """The label is not the evidence (#2759, #2760): pb-b asks for a durable writer, but
    it only shares a table pb-a created with --transient, so the tickerplant restart a
    `start all` is drops it and the start gate refuses the publisher. --start-with-all
    is refused on what a restart would restore, in the plan and at registration."""
    first = ["pb-a", *new(world)[1:-2], "--transient", "--no-start", "--yes"]
    assert run("add", *first).exit_code == 0
    shared = ["pb-b", *new(world)[1:], "--start-with-all"]
    shown = run("add", *shared, "--dry-run", "--json")
    assert shown.exit_code == 1, shown.output
    found = json.loads(shown.output)["plan"]["findings"]
    (said,) = [f["message"] for f in found if f["code"] == native.CODE]
    assert f"a tickerplant restart would not restore {TABLE}" in said
    assert "give --no-start-with-all" in said
    refused = run("add", *shared, "--yes")
    assert refused.exit_code == 1 and rows_for(world.registry) == []
    kept = run("add", *shared[:-1], "--yes")
    assert kept.exit_code == 0, kept.output
    assert _registered(world).split(",")[10] == "0", "registered on demand, through its gate"


def test_start_with_all_without_a_process_file_is_refused(world):
    result = run("add", *new(world)[:-2], "--start-with-all", "--dry-run")
    assert result.exit_code == 1
    assert "give --process-file to register it there" in said(world, result)


# ----------------------------------------------------------------- refusals


def _refusal(world, *extra: str) -> str:
    result = run("add", *new(world), *extra, "--dry-run", "--json")
    assert result.exit_code == 1, result.output
    found = json.loads(result.output)["plan"]["findings"]
    return " ".join(f["message"] for f in found if f["code"] == native.CODE)


def test_a_row_for_the_publisher_the_attachment_did_not_write_is_refused(world):
    world.registry.write_bytes(
        SITE.encode() + f"localhost,7000,metrics,{PROC},,1,0,,,x.q,0,,q,\r\n".encode()
    )
    assert f"already has a row for {PROC} that this attachment did not write" in _refusal(world)


def test_a_port_clash_is_refused(world):
    shown = run("add", *new(world), "--dry-run", "--json").output
    port = json.loads(shown)["plan"]["publisher_port"]
    clash = f"localhost,{{KDBBASEPORT}}+{port - 6000},hdb,hdb1,,1,0,,,x,1,,q,\r\n"
    world.registry.write_bytes(SITE.encode() + clash.encode())
    assert f"hdb1 already has port {port}, the publisher's" in _refusal(world)


def test_an_ambiguous_tickerplant_is_refused(world):
    second = "localhost,{KDBBASEPORT}+9,segmentedtickerplant,stp2,,1,0,,,x,1,,q,\r\n"
    world.registry.write_bytes(SITE.encode() + second.encode())
    assert "lists 2 tickerplants (stp1, stp2)" in _refusal(world)
    assert "give --tickerplant" in _refusal(world)


def test_missing_credentials_for_the_publisher_are_refused(world, monkeypatch):
    monkeypatch.delenv("UQS_NATIVE_TEST_PW", raising=False)
    message = _refusal(world, "--password-env", "UQS_NATIVE_TEST_PW")
    assert "reads the stack's password from $UQS_NATIVE_TEST_PW" in message
    assert "it is not set here" in message


def test_a_process_file_on_another_machine_is_refused(world):
    result = run("add", *new(world), "--host", "otherbox.invalid", "--dry-run")
    assert result.exit_code == 1
    assert "remote registration is not supported yet" in said(world, result)
    scp_style = run(
        "add", *new(world)[:-2], "--process-file", f"uat-host:{world.registry}", "--dry-run"
    )
    assert "remote registration is not supported yet" in said(world, scp_style)


def test_a_process_file_that_does_not_exist_is_refused(world, tmp_path):
    result = run("add", *new(world)[:-2], "--process-file", str(tmp_path / "nope.csv"))
    assert result.exit_code == 1
    assert "no such file on this machine" in said(world, result)


def test_an_owned_row_changed_by_someone_else_is_refused_for_update_and_detach(world):
    assert run("add", *new(world), "--yes").exit_code == 0
    line = _registered(world)
    edited = world.registry.read_bytes().replace(
        line.encode(), line.replace(",0,", ",1,", 1).encode()
    )
    world.registry.write_bytes(edited)
    update = run("add", "pb-uat", "--start-with-all", "--yes", "--json")
    assert update.exit_code == 1
    doc = json.loads(update.output)
    (found,) = [f for f in doc["plan"]["findings"] if f["code"] == native.CODE]
    assert (
        f"the {PROC} row uqs wrote in {world.registry} was changed by someone else"
        in (found["message"])
    )
    assert f"PROCESS_REGISTRY ({world.registry})" in doc["error"]
    assert world.registry.read_bytes() == edited
    removed = run("remove", "pb-uat", "--yes", "--json")
    assert removed.exit_code == 1
    doc = json.loads(removed.output)
    assert doc["outcome"] == "refused" and doc["publisher_stopped"] is True
    assert "was changed by someone else since" in doc["blockers"][0]["reason"]
    assert world.registry.read_bytes() == edited
    assert Manifest.load("pb-uat").state == "applied"


# ------------------------------------------------------- interrupted writes


def test_an_interrupted_write_is_recorded_first_and_converges_on_rerun(world, monkeypatch):
    real, calls = native.write, []

    def failing(path, data, mode=None):
        calls.append(path)
        if path == world.registry and len([c for c in calls if c == world.registry]) == 1:
            raise OSError("injected: the disk went away")
        real(path, data, mode)

    monkeypatch.setattr(native, "write", failing)
    first = run("add", *new(world), "--yes")
    assert first.exit_code == 1
    assert "register failed - injected: the disk went away" in said(world, first)
    record = Manifest.load("pb-uat").registrations["process_row"]
    assert record["status"] == "pending" and rows_for(world.registry) == []
    second = run("add", "pb-uat", "--yes")
    assert second.exit_code == 0, second.output
    assert len(rows_for(world.registry)) == 1
    assert Manifest.load("pb-uat").registrations["process_row"]["status"] == "done"


def test_a_crash_after_the_write_is_resumed_without_a_second_row(world, monkeypatch):
    real, crashed = native._done, []

    def crash(m, kind):
        if kind == native.ROW and not crashed:
            crashed.append(kind)
            raise OSError("injected: killed after the rename")
        real(m, kind)

    monkeypatch.setattr(native, "_done", crash)
    assert run("add", *new(world), "--yes").exit_code == 1
    assert len(rows_for(world.registry)) == 1  # written, but not yet confirmed
    assert Manifest.load("pb-uat").registrations["process_row"]["status"] == "pending"
    assert run("add", "pb-uat", "--yes").exit_code == 0
    assert len(rows_for(world.registry)) == 1
    assert Manifest.load("pb-uat").registrations["process_row"]["status"] == "done"


# -------------------------------------------------------------------- detach


def test_detach_stops_the_publisher_then_removes_only_its_unchanged_row(world, monkeypatch):
    original = world.registry.read_bytes()
    assert run("add", *new(world), "--yes").exit_code == 0
    assert world.started
    seen, real = [], native.unregister

    def spy(m, dry_run=False):
        seen.append(list(world.started))
        return real(m, dry_run=dry_run)

    monkeypatch.setattr(native, "unregister", spy)
    dry = run("remove", "pb-uat", "--dry-run")
    assert dry.exit_code == 0 and len(rows_for(world.registry)) == 1
    result = run("remove", "pb-uat", "--yes")
    assert result.exit_code == 0, result.output
    assert seen[-1] == []  # stopped before the row was touched
    assert world.registry.read_bytes() == original
    assert not (world.state_dir / "pb-uat" / "native_start.sh").exists()
    assert Manifest.load("pb-uat").registrations == {}


def test_a_refused_detach_leaves_the_row_and_the_launcher(world):
    """#2911: a refusal found after the process file was checked - here the block
    in the schema file edited since apply - changes nothing but the stopped
    publisher, as the dry run and the guide say: the site's native start still
    starts it. The detach that follows the fix removes both."""
    assert run("add", *new(world), "--yes").exit_code == 0
    registry, launcher = world.registry.read_bytes(), world.state_dir / "pb-uat" / "native_start.sh"
    text = world.schema.read_text()
    world.schema.write_text(text.replace(f"{TABLE}:", f"{TABLE} :", 1))
    refused = run("remove", "pb-uat", "--yes", "--json")
    assert refused.exit_code == 1
    doc = json.loads(refused.output)
    assert doc["outcome"] == "refused" and doc["steps"] == []
    assert "was edited" in doc["blockers"][0]["reason"]
    assert world.registry.read_bytes() == registry and launcher.exists()
    m = Manifest.load("pb-uat")
    assert m.state == "applied" and set(m.registrations) == {"process_row", "launcher"}
    world.schema.write_text(text)
    assert run("remove", "pb-uat", "--yes").exit_code == 0
    assert rows_for(world.registry) == [] and not launcher.exists()


# ------------------------------------------------- schema writer and secrets


def test_schema_writer_tickerplant_and_cli_are_saved_choices(world):
    result = run("add", *new(world)[:-2], "--schema-writer", "tickerplant", "--dry-run", "--json")
    doc = json.loads(result.output)["plan"]
    assert doc["schema_writer"] == "tickerplant" and doc["persist"] == "remote-schema-file"
    result = run("add", *new(world)[:-2], "--schema-writer", "cli", "--dry-run", "--json")
    doc = json.loads(result.output)["plan"]
    assert doc["schema_writer"] == "cli" and doc["persist"] == "schema-file"
    refused = run("add", *new(world), "--transient", "--schema-writer", "cli", "--dry-run")
    assert "--transient and --schema-writer cli exclude each other" in said(world, refused)


def test_no_secret_reaches_the_file_the_manifest_the_preview_or_json(world, monkeypatch):
    monkeypatch.setenv("UQS_NATIVE_TEST_PW", SECRET)
    shown = run("add", *new(world), "--password-env", "UQS_NATIVE_TEST_PW", "--dry-run", "--json")
    assert shown.exit_code == 0, shown.output
    assert SECRET not in shown.output
    done = run("add", *new(world), "--password-env", "UQS_NATIVE_TEST_PW", "--yes", "--json")
    assert done.exit_code == 0, done.output
    assert SECRET not in done.output
    for path in [world.registry, *(world.state_dir / "pb-uat").rglob("*")]:
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path
    launcher = (world.state_dir / "pb-uat" / "native_start.sh").read_text()
    assert "UQS_NATIVE_TEST_PW" not in launcher  # the gate reads it from the attachment
