"""`uqs data check --table` and `uqs data alter`, end to end on a real q (#1755).

Each case builds a temporary HDB with q: five date partitions, `pb` on the last
three only (a backfilled table), `other` on all five, both enumerated against
one shared sym file. The commands then run as an operator runs them, through
the CLI, and the files are compared byte for byte. KDB-X only: the q script
refuses PeachQ, which cannot write a partitioned HDB natively (each case is in
python/peachq_known_gaps.txt).

The --attach cases go through a stand-in `ssh` on PATH that runs the remote
script with bash on this machine: what is tested is the script uqs sends - the
copy into a temporary directory, the run with $QCMD, the clean-up - not ssh.
The Docker lane (tests/remote) would put a real sshd in between.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from attach_backfill_support import HdbStack, save_attachment
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs.attach import backfill_owner, hdb_where, service
from uqs.attach.facts import Host
from uqs.attach.schema import TableSpec
from uqs.cli import shared
from uqs.cli.shared import app

BUILD_Q = r"""
r:hsym `$getenv`HDB;
put:{[r;d;t;x] (` sv r,(`$string d),t,`) set .Q.en[r] x};
{[r;d] put[r;d;`other;([] time:3#d+0D; sym:`a`b`c; v:1 2 3f)]}[r] each 2026.01.01+til 5;
pb:{[d] ([] time:d+0D 0D01; sym:`x`a; amt:1.5 2.5; n:3 4i)};
{[r;d] put[r;d;`pb;pb d]}[r] each 2026.01.03+til 3;
if[count getenv`SKEW; @[` sv r,`2026.01.04`pb;`.d;:;`time`sym`n`amt]];
exit 0
"""

PB = TableSpec("pb", (("time", "p"), ("sym", "s"), ("amt", "f"), ("n", "i")))


@pytest.fixture
def world(q_binary, monkeypatch, tmp_path):
    """A temporary HDB built by q, the environment the CLI runs q in, and the log."""
    q, env = q_binary
    for key in ("QHOME", "QLIC"):
        if env.get(key):
            monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("QCMD", q)
    hdb = tmp_path / "hdb"
    errors: list[str] = []

    class Log:
        def error(self, message, *args):
            errors.append(str(message).format(*args))

        def __getattr__(self, _name):
            return lambda *a, **k: None

    monkeypatch.setattr(shared, "log", Log())

    def build(skew: bool = False) -> Path:
        script = tmp_path / "build.q"
        script.write_text(BUILD_Q)
        subprocess.run([q, str(script), "-q"], env={**env, "HDB": str(hdb),
                       "SKEW": "1" if skew else ""}, stdin=subprocess.DEVNULL,
                       capture_output=True, check=True, timeout=60)  # fmt: skip
        return hdb

    return type("World", (), {"hdb": hdb, "build": build, "errors": errors, "tmp": tmp_path})


def run_uqs(*argv: str):
    return CliRunner().invoke(app, list(argv), env={"COLUMNS": "300"})


def digest(root: Path) -> dict[str, str]:
    """Every file under `root`, and beside it (the journal), with its sha256."""
    files = [*root.rglob("*"), *root.parent.glob(f"{root.name}.uqf-alter/*")]
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files) if p.is_file()
    }


def doc(result) -> dict:
    return json.loads(result.stdout)


# ------------------------------------------------------------------ dry run


def test_a_dry_run_writes_nothing_and_creates_no_journal(world):
    hdb = world.build()
    before = digest(hdb)
    for argv in (
        ["alter", "pb", "--reorder", "n,amt,sym,time"],
        ["alter", "pb", "--rename", "amt=amount"],
        ["alter", "pb", "--attr", "sym:g"],
        ["alter", "pb", "--cast", "n:long"],
        ["check", "--table", "pb"],
    ):
        result = run_uqs("data", *argv, "--hdb", str(hdb))
        assert result.exit_code == 0, (argv, result.output, world.errors)
        assert "dry run: nothing was written" in result.output
    assert digest(hdb) == before, "every file's bytes are unchanged"
    assert not (hdb.parent / "hdb.uqf-alter").exists(), "and no journal was created"


def test_the_plan_names_the_hdb_its_source_and_the_partitions(world):
    hdb = world.build()
    result = run_uqs("data", "alter", "pb", "--hdb", str(hdb), "--reorder", "n,amt,sym,time",
                 "--json")  # fmt: skip
    d = doc(result)
    assert d["ok"] and d["resolved_from"] == "--hdb" and d["hdb"] == str(hdb.resolve())
    assert d["plan"]["partitions_touched"] == ["2026.01.03", "2026.01.04", "2026.01.05"]
    assert d["plan"]["partitions_lacking"] == ["2026.01.01", "2026.01.02"]
    assert d["plan"]["journal"].startswith(str(hdb.resolve()) + ".uqf-alter/")
    assert any("writers were not checked" in w for w in d["warnings"])
    assert d["result"] is None


# ------------------------------------------------------------------ applying


def test_check_table_apply_adds_the_empty_table_only_where_missing(world):
    hdb = world.build()
    before = digest(hdb)
    result = run_uqs("data", "check", "--hdb", str(hdb), "--table", "pb", "--apply", "--yes",
                 "--json")  # fmt: skip
    d = doc(result)
    assert result.exit_code == 0 and d["result"]["status"] == "applied", d
    assert d["result"]["final_check"]["clean"]
    after = digest(hdb)
    assert {k: after[k] for k in before} == before, "partitions with rows, other and sym unchanged"
    added = sorted(set(after) - set(before))
    assert added and all("/2026.01.01/pb/" in a or "/2026.01.02/pb/" in a for a in added)
    template = (hdb / "2026.01.05" / "pb" / ".d").read_bytes()
    for day in ("2026.01.01", "2026.01.02"):
        assert (hdb / day / "pb" / ".d").read_bytes() == template, "the template's column order"
    assert d["plan"]["target"]["types"] == "psfi", "and its types"


def test_reorder_then_undo_restores_every_file(world):
    hdb = world.build()
    before = {k: v for k, v in digest(hdb).items() if ".uqf-alter" not in k}
    applied = run_uqs("data", "alter", "pb", "--hdb", str(hdb), "--reorder", "n,amt,sym,time",
                  "--apply", "--yes", "--json")  # fmt: skip
    d = doc(applied)
    assert applied.exit_code == 0 and d["result"]["final_check"]["clean"], d
    changed = {k for k, v in digest(hdb).items() if before.get(k) not in (None, v)}
    assert changed == {str(hdb / day / "pb" / ".d") for day in d["plan"]["partitions_touched"]}
    undone = run_uqs("data", "alter", "--hdb", str(hdb), "--undo", d["plan"]["journal"],
                 "--apply", "--yes")  # fmt: skip
    assert undone.exit_code == 0, (undone.output[-600:], world.errors)
    assert {k: v for k, v in digest(hdb).items() if ".uqf-alter" not in k} == before


# ------------------------------------------------------------------ refusals


def test_apply_without_a_terminal_and_without_yes_is_refused(world):
    hdb = world.build()
    before = digest(hdb)
    result = run_uqs("data", "alter", "pb", "--hdb", str(hdb), "--rename", "amt=amount", "--apply")
    assert result.exit_code == 1
    assert any("needs a terminal" in e and "--yes" in e for e in world.errors)
    assert digest(hdb) == before


def test_partitions_that_disagree_are_refused_naming_each_shape(world):
    hdb = world.build(skew=True)
    before = digest(hdb)
    result = run_uqs("data", "alter", "pb", "--hdb", str(hdb), "--cast", "n:long", "--apply",
                 "--yes", "--json")  # fmt: skip
    d = doc(result)
    assert result.exit_code == 1 and not d["ok"]
    assert "disagree" in d["error"]
    assert "n:i, amt:f  <- 2026.01.04" in d["error"]
    assert "amt:f, n:i  <- 2026.01.03, 2026.01.05" in d["error"]
    assert digest(hdb) == before, "before any write"


# ------------------------------------------------------------------ --attach, over ssh


class ReloadingStack(HdbStack):
    """A scripted stack whose hdb1 answers and reloads."""

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


@pytest.fixture
def attached(world, monkeypatch):
    """Attachment `pb` on a stack at 'testhost' - another machine - reached by a stand-in
    ssh that runs the remote script here, recording each destination it was given."""
    hdb = world.build()
    monkeypatch.setenv("UQS_ATTACH_DIR", str(world.tmp / "attachments"))
    fake = ReloadingStack(hdb.resolve())
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    monkeypatch.setattr(hdb_where, "HOST", Host(local_names=("localhost",)))
    save_attachment("pb", hdb.resolve(), table=PB)
    bin_dir, remote_tmp = world.tmp / "bin", world.tmp / "remote-tmp"
    bin_dir.mkdir()
    remote_tmp.mkdir()
    log = world.tmp / "ssh.log"
    ssh = bin_dir / "ssh"
    ssh.write_text(f'#!/bin/sh\necho "$5" >> {log}\nexec env TMPDIR={remote_tmp} bash -s\n')
    ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return type("Attached", (), {"hdb": hdb, "stack": fake, "log": log, "remote_tmp": remote_tmp})


def test_attach_plans_and_applies_on_the_stack_s_host_and_reloads_its_readers(world, attached):
    hdb = attached.hdb
    plan = run_uqs("data", "alter", "--attach", "pb", "--reorder", "n,amt,sym,time", "--json")
    d = doc(plan)
    assert plan.exit_code == 0 and d["ok"], (plan.output, world.errors)
    assert d["ssh"] == "testhost" and "pinned storage.hdb_dir" in d["resolved_from"]
    assert d["plan"]["table"] == "pb", "the attachment's own table, when none is named"
    applied = run_uqs("data", "alter", "--attach", "pb", "--reorder", "n,amt,sym,time",
                  "--apply", "--yes", "--json")  # fmt: skip
    r = doc(applied)["result"]
    assert applied.exit_code == 0 and r["final_check"]["clean"], (applied.output, world.errors)
    assert attached.stack.reloaded == ["hdb1"]
    assert {"procname": "hdb1", "ok": "yes", "detail": ""} in r["readers"]
    # a plan, then --apply's plan and its write: each one ssh session to the stack's host
    assert attached.log.read_text().split() == ["testhost"] * 3
    assert not list(attached.remote_tmp.iterdir()), "the copied script is removed after each run"
    assert (hdb / "2026.01.05" / "pb" / "n").is_file()


def test_attach_refuses_while_a_backfill_owns_the_attachment(world, attached):
    before = digest(attached.hdb)
    backfill_owner.claim("pb", "pb_backfill", os.getpid(), "abc")
    result = run_uqs("data", "check", "--attach", "pb", "--apply", "--yes")
    assert result.exit_code == 1
    assert any("attached backfill running" in e for e in world.errors), world.errors
    assert digest(attached.hdb) == before
