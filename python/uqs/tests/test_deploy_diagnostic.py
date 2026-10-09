"""Every remote failure is read through remote.diagnostic (#971).

A refusal the remote command printed on stdout, with ssh's own notices on
stderr, must reach the operator at each remote path - the sudo preflight,
the live check, the port check, the hdb-check and `uqs --target`'s check -
and the notices must not stand in its place.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from uqs.deploy import control, hdb
from uqs.deploy.config import DeployError, make_config
from uqs.deploy.stages import Deployment
from uqs.paths import UqsError

REFUSAL = "uqs: refused - the release's profile names no such process"
NOISE = (
    "Warning: Permanently added 'h' (ED25519) to the list of known hosts.\n"
    "** WARNING: connection is not using a post-quantum key exchange algorithm.\n"
)
DEPLOY = Path(control.__file__).parent
#: build.py and payload.py run local commands (uv, git, pip), not remote ones.
LOCAL = {"remote.py", "build.py", "payload.py"}


def _refused() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, stdout=REFUSAL + "\n", stderr=NOISE)


class Refusing:
    """A transport whose every remote command refuses, on stdout, behind ssh noise."""

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        return _refused()

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        pass


def _dep(**kw) -> Deployment:
    cfg = make_config(artifact="", host="h", dest="/opt/uqf", profile="essential", **kw)
    return Deployment(cfg, Refusing())


def _says_the_refusal(message: str) -> None:
    assert REFUSAL in message
    assert "Permanently added" not in message and "post-quantum" not in message


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(lambda: _dep(remote_user="svc").check_sudo(), id="sudo-preflight"),
        pytest.param(lambda: _dep(live_check="fx").live_check("r1"), id="live-check"),
        pytest.param(lambda: _dep().ports_free("r1"), id="ports"),
        pytest.param(lambda: hdb.check(_dep(), "r1"), id="hdb-check"),
    ],
)
def test_a_deploy_step_reports_a_stdout_refusal_behind_ssh_noise(step):
    with pytest.raises(DeployError) as exc:
        step()
    _says_the_refusal(str(exc.value))


def test_the_target_check_reports_a_stdout_refusal_behind_ssh_noise():
    r = control.Remote(name="uat", host="h", dest="/opt/uqf")
    with pytest.raises(UqsError) as exc:
        control.current_release(r, runner=lambda *a, **k: _refused())
    _says_the_refusal(str(exc.value))


def test_no_deploy_module_reads_remote_stderr_on_its_own():
    """The next remote path goes through remote.diagnostic too, not its own read."""
    readers = [
        f"{p.name}:{n}"
        for p in sorted(DEPLOY.glob("*.py"))
        if p.name not in LOCAL
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"\.stderr\b", line)
    ]
    assert not readers, f"read remote failures with remote.diagnostic: {readers}"
