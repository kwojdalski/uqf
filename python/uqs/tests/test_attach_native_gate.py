"""The native start of a registered publisher (#1845): the process file edit that
keeps every other byte, the launcher a row's qcmd names, and the start gate it
runs before q - verified, under its stack, its tables live (#1441)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from attach_native_support import PROC, make_world, new, run
from attach_support import FakeStack

from uqs.attach import config, native_gate, native_row
from uqs.attach.config import AttachmentError
from uqs.attach.registry import Registry, write
from uqs.paths import UqsError

TABLE = "piggybank_general_ledger"


@pytest.fixture
def world(monkeypatch, tmp_path):
    return make_world(monkeypatch, tmp_path)


# ------------------------------------------------------------- the file edit


def test_an_lf_file_with_blank_lines_and_a_bom_keeps_every_byte(tmp_path):
    text = "﻿host,port,procname,startwithall,qcmd,taskset_no\n\na,1,x1,1,q,\n   \nb,2,x2,0,q,4\n"
    path = tmp_path / "process.csv"
    path.write_text(text)
    reg = Registry.read(path)
    assert reg.header[2] == "procname" and reg.eol == b"\n"
    row = reg.row({"host": "localhost", "port": "9", "procname": "pub1", "qcmd": "/l.sh"})
    assert row == "localhost,9,pub1,,/l.sh,"
    added = reg.with_line(None, row)
    assert added == text.encode() + row.encode() + b"\n"
    path.write_bytes(added)
    reg = Registry.read(path)
    assert reg.line_of("pub1") == row
    replaced = reg.with_line(row, row.replace(",,", ",1,"))
    assert replaced == text.encode() + b"localhost,9,pub1,1,/l.sh,\n"
    path.write_bytes(replaced)
    assert Registry.read(path).without_line("localhost,9,pub1,1,/l.sh,") == text.encode()


def test_two_rows_for_one_process_and_an_unsafe_value_are_refused(tmp_path):
    path = tmp_path / "process.csv"
    path.write_text("host,procname\na,x1\nb,x1\n")
    with pytest.raises(AttachmentError, match="has 2 rows for x1: torq.sh starts the first"):
        Registry.read(path).line_of("x1")
    with pytest.raises(AttachmentError, match="holds a comma, quote or line break"):
        Registry.read(path).row({"procname": "a,b"})
    (tmp_path / "bare.csv").write_text("host,port\n")
    with pytest.raises(AttachmentError, match="is not a TorQ process file: its header has no"):
        Registry.read(tmp_path / "bare.csv")


def test_a_write_keeps_the_mode_and_replaces_the_file_a_symlink_names(tmp_path):
    real = tmp_path / "real.csv"
    real.write_text("host,procname\n")
    real.chmod(0o640)
    link = tmp_path / "process.csv"
    link.symlink_to(real)
    write(link, b"host,procname\na,x\n")
    assert link.is_symlink() and real.read_bytes() == b"host,procname\na,x\n"
    assert stat.S_IMODE(real.stat().st_mode) == 0o640
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


# ------------------------------------------------------------------ the gate


def _verified(world) -> config.Attachment:
    assert run("add", *new(world), "--yes").exit_code == 0
    return config.load("pb-uat")


def test_the_gate_passes_a_verified_attachment_whose_tables_are_live(world):
    _verified(world)
    said = native_gate.check("pb-uat", "6000", ipc=world, wait=0)
    assert said.startswith(f"{PROC}: attachment pb-uat is verified and ")
    assert said.endswith("holds its tables")


def test_the_gate_refuses_an_attachment_that_is_not_verified(world):
    assert run("add", *new(world), "--no-start", "--yes").exit_code == 0
    from uqs.attach.manifest import Manifest

    Manifest.load("pb-uat").set_state("applied", "test: verify failed")
    with pytest.raises(UqsError) as refused:
        native_gate.check("pb-uat", "6000", ipc=world, wait=0)
    assert "which is applied: a native start may start it once" in str(refused.value)
    assert str(refused.value).endswith("Nothing was started.")


def test_the_gate_refuses_another_stack(world):
    _verified(world)
    with pytest.raises(UqsError, match="this start is under -stackid 7000"):
        native_gate.check("pb-uat", "7000", ipc=world, wait=0)


def test_the_gate_refuses_while_the_tickerplant_lacks_the_tables(world):
    _verified(world)
    with pytest.raises(UqsError) as refused:
        native_gate.check("pb-uat", "6000", ipc=FakeStack(6000), wait=0)
    message = str(refused.value)
    assert f"its tables are not ready on stack 6000 after 0s: {TABLE} not registered on" in message


def test_the_gate_waits_for_a_tickerplant_that_is_coming_up(world):
    _verified(world)
    ipcs, naps = [FakeStack(6000), world], []

    class Switching:
        def call(self, endpoint, op, request):
            return ipcs[0].call(endpoint, op, request)

    def sleep(seconds):
        naps.append(seconds)
        ipcs.pop(0)

    clock = iter(range(100)).__next__
    said = native_gate.check("pb-uat", "6000", ipc=Switching(), sleep=sleep, clock=clock)
    assert naps == [native_gate.INTERVAL_S] and "holds its tables" in said


def test_the_gate_refuses_at_once_without_the_password_variable(world, monkeypatch):
    monkeypatch.setenv("UQS_GATE_PW", "x")
    assert run("add", *new(world), "--password-env", "UQS_GATE_PW", "--yes").exit_code == 0
    monkeypatch.delenv("UQS_GATE_PW")
    with pytest.raises(UqsError) as refused:
        native_gate.check("pb-uat", "6000", wait=0)
    assert "the password variable UQS_GATE_PW is not set" in str(refused.value)
    assert "in the environment of this start" in str(refused.value)


def test_main_says_why_on_stderr_and_exits_1(world, capsys):
    assert native_gate.main(["pb-uat", "--state-dir", str(world.state_dir), "--stack-id", "1"]) == 1
    assert "uqs start gate: refused: no attachment 'pb-uat'" in capsys.readouterr().err


# -------------------------------------------------------------- the launcher


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("gate_exit", [0, 1])
def test_the_launcher_runs_the_gate_before_q_and_q_only_when_it_passes(
    world, tmp_path, monkeypatch, gate_exit
):
    if shutil.which("sh") is None:
        pytest.skip("no sh")
    att = _verified(world)
    log = tmp_path / "calls.log"
    gate = _script(tmp_path / "python", f'echo "gate $*" >> {log}\nexit {gate_exit}\n')
    q = _script(tmp_path / "q", f'echo "q UQF_ROOT=$UQF_ROOT $*" >> {log}\n')
    text = native_row.launcher_text(att, {"UQF_ROOT": "/opt/uqf"}, python=str(gate), odbc_home="")
    launcher = _script(tmp_path / "native_start.sh", text.removeprefix("#!/bin/sh\n"))
    args = ["-stackid", "6000", "-proctype", "metrics", "-procname", PROC]
    done = subprocess.run(
        [str(launcher), *args],
        env={**os.environ, "QCMD": str(q)}, capture_output=True, text=True,
    )  # fmt: skip
    lines = log.read_text().splitlines()
    assert lines[0] == (
        f"gate -I -m uqs.attach.native_gate pb-uat --state-dir {world.state_dir} --stack-id 6000"
    )
    if gate_exit:
        assert done.returncode == 1 and len(lines) == 1
    else:
        assert done.returncode == 0
        assert lines[1] == f"q UQF_ROOT=/opt/uqf {' '.join(args)}"
