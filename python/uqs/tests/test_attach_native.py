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
