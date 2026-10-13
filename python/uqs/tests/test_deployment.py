"""TorQ discovers the deployment, not the whole of process.csv (#1207).

Under `--profile essential` sort1 was told about sortworker2 - a process.csv
row nothing started - and retried its port every few seconds, forever. uqs
now writes the deployment set (stack/deployment.py) and
scripts/torqcode/handlers/deployment.q drops the other process.csv rows on
their way into .servers.SERVERS.

Two halves, tested apart: what uqs writes for each kind of start, and what
TorQ's own trackservers.q - the vendored file, run in a real q - then does
with it.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from uqs import cli, mcp
from uqs import paths as stack_paths
from uqs.cli import lifecycle
from uqs.interpreter import q_interpreter
from uqs.model import profiles
from uqs.paths import UqsError, UqsPaths
from uqs.stack import deployment, occupancy, runtime, start_policy
from uqs.stack.env import DEPLOYMENT_ENV, build_env

UQF_ROOT = Path(__file__).resolve().parents[3]
HANDLER = UQF_ROOT / "scripts" / "torqcode" / "handlers" / "deployment.q"


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> UqsPaths:
    """This checkout's process table, with a data directory of the test's own."""
    real = dataclasses.replace(stack_paths.default_paths(), torqdata=tmp_path / "data")
    monkeypatch.setattr(stack_paths, "default_paths", lambda: real)
    return real


@pytest.fixture
def torq_runs(monkeypatch) -> list[list[str]]:
    """run_torq_sh up to the launcher: refusals and recording are real, the
    bootstrap and torq.sh itself are not."""
    seen: list[list[str]] = []
    real_run = subprocess.run

    def launch(cmd, **kw):
        if cmd[0] != "torq.sh":
            return real_run(cmd, **kw)  # the q version check the refusals make
        seen.append(cmd[1:])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runtime, "torq_launcher", lambda _p: Path("torq.sh"))
    monkeypatch.setattr(runtime, "bootstrap", lambda _p, base_port=None, selected=None: {})
    monkeypatch.setattr(runtime.subprocess, "run", launch)
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda *_a: None)
    return seen


def test_the_essential_profile_leaves_sortworker2_out(paths):
    members, _ = start_policy.resolve_profiles(paths, "essential")
    assert "sortworker1" in members and "sortworker2" not in members


def test_naming_sortworker2_beside_the_profile_puts_it_in(paths):
    members, _ = start_policy.resolve_profiles(paths, "essential", ["sortworker2"])
    assert "sortworker2" in members


def test_a_profile_start_writes_exactly_its_set(paths, torq_runs):
    members, _ = start_policy.resolve_profiles(paths, "essential")
    paths.generated_deployment.parent.mkdir(parents=True)
    paths.generated_deployment.write_text("hdb2\nsortworker2\n")  # a previous profile's
    mcp.uqs_start(profile="essential")
    assert deployment.members(paths) == members, "replaced, not added to"
    assert torq_runs == [["start", " ".join(members)]]


def test_starting_sortworker2_later_makes_it_discoverable(paths, torq_runs):
    mcp.uqs_start(profile="essential")
    runtime.start(paths, "sortworker2")
    assert "sortworker2" in (deployment.members(paths) or ())


def test_a_refused_start_changes_nothing(paths, torq_runs):
    mcp.uqs_start(profile="essential")
    before = deployment.members(paths)
    with pytest.raises(UqsError, match="nosuchproc1"):
        runtime.start(paths, "sortworker2 nosuchproc1")
    assert deployment.members(paths) == before
    assert len(torq_runs) == 1


def test_a_start_without_a_profile_writes_no_set(paths, torq_runs):
    """No file, no filter: a stack never started from a profile discovers as
    TorQ always has."""
    runtime.start(paths, "sortworker2")
    assert deployment.members(paths) is None


def test_a_started_job_brings_what_it_reads(paths):
    deployment.select(paths, ["discovery1", "stp1"])
    deployment.admit(paths, "posbook1")
    assert profiles.closure(["posbook1"]) <= set(deployment.members(paths) or ())


def test_start_all_adds_every_startwithall_row(paths):
    deployment.select(paths, ["discovery1"])
    deployment.admit(paths, "all")
    got = set(deployment.members(paths) or ())
    assert {"sortworker2", "hdb2"} <= got, "both start with the stack"
    assert "feed1" not in got, "uqf starts feed1 only when asked"


def test_the_cli_hands_a_profile_start_its_set(paths, monkeypatch):
    calls: list[dict[str, Any]] = []

    def start(_paths, names, **kw):
        calls.append({"names": names, **kw})
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(runtime, "start", start)
    printed = subprocess.CompletedProcess([], 0, "", "")
    monkeypatch.setattr(runtime, "print_procs", lambda *_a, **_k: printed)
    monkeypatch.setattr(lifecycle, "_running", lambda _port: set())
    runner = CliRunner()
    assert runner.invoke(cli.app, ["start", "--profile", "essential"]).exit_code == 0
    assert runner.invoke(cli.app, ["start", "sortworker2"]).exit_code == 0
    assert runner.invoke(cli.app, ["start", "--profile", "essential", "--print"]).exit_code == 0
    members, _ = start_policy.resolve_profiles(paths, "essential")
    assert [c["selected"] for c in calls[:2]] == [list(members), None]
    assert len(calls) == 2, "--print starts nothing, so it records nothing"


def test_every_torq_process_is_told_where_the_set_is(paths):
    env = build_env(paths)
    assert env[DEPLOYMENT_ENV] == str(paths.generated_deployment)
    assert Path(env["KDBSERVCODE"]) / "handlers" / "deployment.q" == HANDLER
    assert HANDLER.is_file()


# ---------------------------------------------------------------------------
# What TorQ does with it: the vendored trackservers.q, in a real q
# ---------------------------------------------------------------------------

#: trackservers.q with the stand-ins torq.q would have given it, then sort1's
#: view of two sortworkers. hopen is replaced by a recorder that is always
#: refused - nothing listens - so TRIED is every connection attempt made.
#: Every step is TorQ's own: addprocs is what a discovery refresh and a
#: procupdate call, retry is the RETRY timer's body.
_HARNESS = r"""
.proc.loaded:1b;.proc.procname:`sort1;.proc.proctype:`sort;.proc.parentproctype:`;
.proc.getconfigfile:{[x] `$"/nonexistent/",x};.proc.getconfig:{[x;y] 2#`};.proc.cp:{.z.p};
.h.log:();.lg.o:.lg.e:.lg.w:{[x;y] .h.log,:enlist y};
.dotz.getcommand:{[x] `.h.zpc};.h.zpc:{[x]};.dotz.set:{[x;y]};.z.pc:{[x]};
.dotz.liveh:.dotz.liveh0:.dotz.livehn:{(not null x)&x in key .z.W};
.servers.enabled:0b;
system"l ",getenv[`TORQHOME],"/code/handlers/trackservers.q";
if[count getenv`WITH_HANDLER; system"l ",getenv`WITH_HANDLER];
/ a process's own hook, set after the handlers as gateway.q and monitor.q do
.h.custom:0;.servers.addprocscustom:{[t;p] .h.custom+:1};
.h.tried:`symbol$();.servers.opencon:{.h.tried,:x;0Ni};
procs:([]host:`localhost;port:6066 6067i;proctype:`sortworker;procname:`sortworker1`sortworker2);
.servers.procstab:.servers.formatprocs procs;.servers.nontorqprocesstab:0#.servers.procstab;
disc:select procname,proctype,hpup,attributes:(count i)#enlist ()!() from .servers.procstab;
show_:{[k;v] -1 k,":"," " sv string v};
.servers.addprocs[disc;`sortworker;0b];.servers.retry[];
show_["REGISTERED";exec procname from .servers.SERVERS];
.servers.addprocs[disc;`sortworker;0b];.servers.retry[];
show_["REFRESHED";exec procname from .servers.SERVERS];
show_["TRIED";`$last each ":" vs/: string .h.tried];
if[count f:getenv`UQS_DEPLOYMENT_FILE; (hsym`$f) 0: ("sort1";"sortworker1";"sortworker2")];
.servers.procupdate select procname,proctype,hpup,attributes from disc where procname=`sortworker2;
show_["STARTED";exec procname from .servers.SERVERS];
-1 "CUSTOM:",string .h.custom;
-1 "DROPLOG:",string sum .h.log like "not registering sortworker2*";
exit 0
"""


def _torq(tmp_path: Path, *, handler: bool, deployed: list[str] | None) -> dict[str, str]:
    q = q_interpreter()
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    script = tmp_path / "harness.q"
    script.write_text(_HARNESS)
    env = os.environ.copy()
    env["TORQHOME"] = str(UQF_ROOT / "lib" / "torq")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    env.pop(DEPLOYMENT_ENV, None)
    if handler:
        env["WITH_HANDLER"] = str(HANDLER)
    if deployed is not None:
        f = tmp_path / "deployment.txt"
        f.write_text("".join(f"{p}\n" for p in deployed))
        env[DEPLOYMENT_ENV] = str(f)
    out = subprocess.run(
        [str(q), str(script), "-q"],
        env=env,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    lines = [line.split(":", 1) for line in out.stdout.splitlines() if ":" in line]
    return {k: v for k, v in lines}


def test_torq_alone_retries_the_unselected_worker(tmp_path):
    """The bug, in TorQ's own code: without the handler, sortworker2 is
    registered from discovery's answer and retried with sortworker1."""
    got = _torq(tmp_path, handler=False, deployed=["sort1", "sortworker1"])
    assert got["REGISTERED"].split() == ["sortworker1", "sortworker2"]
    assert "6067" in got["TRIED"]


def test_an_unselected_worker_is_never_registered_or_tried(tmp_path):
    got = _torq(tmp_path, handler=True, deployed=["sort1", "sortworker1"])
    assert got["REGISTERED"].split() == ["sortworker1"]
    assert got["REFRESHED"].split() == ["sortworker1"], "a refresh does not bring it back"
    assert "6067" not in got["TRIED"]
    assert got["DROPLOG"] != "0", "what is left out is named in the log"


def test_a_selected_worker_is_still_retried_after_failing(tmp_path):
    """Both retries tried sortworker1: a selected process that is down keeps
    being reconnected through TorQ's own retry, so its failures stay in the log."""
    got = _torq(tmp_path, handler=True, deployed=["sort1", "sortworker1"])
    assert got["TRIED"].split() == ["6066", "6066"]


def test_a_worker_started_later_is_registered_when_it_comes_up(tmp_path):
    got = _torq(tmp_path, handler=True, deployed=["sort1", "sortworker1"])
    assert got["STARTED"].split() == ["sortworker1", "sortworker2"]


def test_the_process_own_hook_still_runs(tmp_path):
    """gateway.q and monitor.q set addprocscustom after the handlers load; the
    filter is on register, so their hook runs on every addprocs as before."""
    got = _torq(tmp_path, handler=True, deployed=["sort1", "sortworker1"])
    assert got["CUSTOM"] == "3"


def test_without_a_deployment_set_torq_discovers_as_it_always_has(tmp_path):
    got = _torq(tmp_path, handler=True, deployed=None)
    assert got["REGISTERED"].split() == ["sortworker1", "sortworker2"]
    assert got["DROPLOG"] == "0"
