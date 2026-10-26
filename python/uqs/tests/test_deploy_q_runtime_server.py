"""A bundled q on the server (#1557, step 6): installed write-once under
shared/runtimes/q/<sha256>/ beside the TorQ runtimes, run once there, reused
and never rewritten; named by each release's own deploy.env; and kept by
prune while any release left names it - a TorQ runtime and a q runtime alike.

As in test_deploy_torq_runtime_server.py, the remote runs each script with
bash against a destination under tmp_path, and the q is a real (shell) binary.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from test_deploy_q_runtime import peachq_entry
from test_deploy_torq_runtime_server import R1, R2, R3, LocalRemote, _dep
from test_deploy_torq_runtime_server import _runtime as torq_runtime_of

from uqs.deploy import prune, q_runtime, rollback, runtime_install
from uqs.deploy.config import DeployError
from uqs.deploy.verify import OK_MARKER


def _runtime(tmp_path: Path, name: str, says: str = "peachq") -> q_runtime.QRuntime:
    entry = peachq_entry(tmp_path / f"peachq-{name}", says=says)
    # a different binary per name, so a different runtime
    (entry / "q").write_text(f'#!/bin/sh\n# {name}\necho "UQF_Q {says} 5"\n')
    return q_runtime.build_runtime(entry, tmp_path / f"dist-{name}")


def test_a_q_is_installed_whole_read_only_and_runs_there(tmp_path):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"
    dep = _dep(dest)
    assert runtime_install.state(dep, rt) == runtime_install.INSTALL
    assert runtime_install.install(dep, rt, R1) == "installed"
    home = Path(rt.directory(str(dest)))
    assert home.parent == dest / "shared" / "runtimes" / "q"
    assert sorted(p.name for p in home.iterdir()) == [".uqf-runtime", "RUNTIME_MANIFEST.json", "q"]
    assert os.access(home / "q", os.X_OK) and not os.access(home / "q", os.W_OK)
    assert runtime_install.probe(dep, rt) == "5"
    assert runtime_install.state(dep, rt) == runtime_install.REUSE, "the probe wrote nothing there"


def test_a_second_push_reuses_it_and_writes_nothing(tmp_path):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"
    runtime_install.install(_dep(dest), rt, R1)
    remote = LocalRemote()
    assert runtime_install.install(_dep(dest, remote), rt, R2) == "reused"
    assert remote.puts == []


def test_a_replaced_binary_is_refused_and_never_overwritten(tmp_path):
    rt, dest = _runtime(tmp_path, "a"), tmp_path / "uqf"
    runtime_install.install(_dep(dest), rt, R1)
    binary = Path(rt.directory(str(dest))) / "q"
    binary.chmod(0o755)
    binary.write_text('#!/bin/sh\necho "UQF_Q kdbx 4.1"\n')
    with pytest.raises(DeployError, match="q and 0 other file.* is never overwritten"):
        runtime_install.install(_dep(dest), rt, R2)
    assert "kdbx" in binary.read_text()


def test_an_installed_q_that_is_kx_is_refused(tmp_path):
    """A q runtime is PeachQ by its manifest; run, this binary says otherwise."""
    rt, dest = _runtime(tmp_path, "a", says="peachq"), tmp_path / "uqf"
    runtime_install.install(_dep(dest), rt, R1)
    binary = Path(rt.directory(str(dest))) / "q"
    binary.chmod(0o755)
    binary.write_text('#!/bin/sh\necho "UQF_Q kdbx 4.1"\n')
    with pytest.raises(DeployError, match="says it is kdbx - KX's q is never bundled"):
        runtime_install.probe(_dep(dest), rt)


# --- each release names its own q: prune and rollback --------------------------------


def _release(dest: Path, rid: str, torq, rt: q_runtime.QRuntime, log: Path, **report) -> None:
    """A release as a push leaves it: its deploy.env naming its TorQ and q
    runtimes, its report, and a stand-in uqs logging the QCMD it saw."""
    root = dest / "releases" / rid
    (root / ".venv" / "bin").mkdir(parents=True)
    dep = _dep(dest)
    dep.torq_runtime, dep.q_runtime = torq, rt
    (root / "deploy.env").write_text("\n".join(dep.env_lines()) + "\n")
    body = {"profile": "capture", "processes": [{"process": "rdb1"}], "status": "deployed"}
    (root / "deploy-report.json").write_text(json.dumps({**body, **report}))
    uqs = root / ".venv" / "bin" / "uqs"
    verified = json.dumps({"passed": True, "reason": "", "processes": []})
    uqs.write_text(
        "#!/bin/bash\n"
        f'echo "{rid} $1 $QCMD" >> {log}\n'
        f"if [ \"$1\" = deploy ]; then echo '{verified}'; echo {OK_MARKER}; fi\n"
    )
    uqs.chmod(0o755)


@pytest.fixture
def server(tmp_path):
    """R3 is current and replaced R2, which replaced R1, on q runtimes C, A
    and B; D is installed and named by none. All three share TorQ runtime T."""
    dest, log = tmp_path / "uqf", tmp_path / "uqs.log"
    torq = torq_runtime_of(tmp_path, "T")
    runtime_install.install(_dep(dest), torq, R1)
    rts = {n: _runtime(tmp_path, n) for n in "ABCD"}
    for rt in rts.values():
        runtime_install.install(_dep(dest), rt, R1)
    _release(dest, R1, torq, rts["C"], log)
    _release(dest, R2, torq, rts["A"], log, previous_release=R1)
    _release(dest, R3, torq, rts["B"], log, previous_release=R2)
    (dest / "current").symlink_to(f"releases/{R3}")
    return dest, torq, rts, log


def test_prune_keeps_every_q_a_remaining_release_names(server):
    dest, torq, rts, _ = server
    out = io.StringIO()
    assert prune.prune(_dep(dest).cfg, LocalRemote(), keep=0, out=out) == 0
    result = json.loads(out.getvalue())
    assert result["removed"] == [R1]
    gone = sorted(f"q/{rts[n].sha256}" for n in "CD")
    assert sorted(result["runtimes"]["removed"]) == gone
    kept = {k["runtime"]: k["why"] for k in result["runtimes"]["kept"]}
    assert kept[f"q/{rts['A'].sha256}"] == f"named by {R2}"
    assert kept[f"torq/{torq.sha256}"] == f"named by {R2}, {R3}", "TorQ's too"
    left = {p.name for p in (dest / q_runtime.RUNTIMES_DIR).iterdir()}
    assert left == {rts["A"].sha256, rts["B"].sha256}


def test_rollback_runs_the_target_on_its_own_q(server):
    dest, _, rts, log = server
    assert rollback.rollback(_dep(dest).cfg, LocalRemote(), out=io.StringIO()) == 0
    a, b = (f"{rts[n].directory(str(dest))}/q" for n in "AB")
    assert log.read_text().splitlines() == [f"{R3} stop {b}", f"{R2} start {a}", f"{R2} deploy {a}"]
