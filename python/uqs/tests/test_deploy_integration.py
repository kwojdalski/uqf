"""`uqs deploy build` and `uqs deploy push` against a real, disposable
server (#773, #778, #835).

Skipped unless UQF_DEPLOY_TEST_HOST names one: it needs ssh access, a
licensed q, an installed TorQ and starter pack, uv and Python 3.14 there, and
it starts and stops a stack - so it never runs in an ordinary test pass.

    UQF_DEPLOY_TEST_HOST=uqf-test UQF_DEPLOY_TEST_DEST=/tmp/uqf-deploy \\
    UQF_DEPLOY_TEST_TORQHOME=/opt/torq \\
    UQF_DEPLOY_TEST_TORQAPPHOME=/opt/torq-finance-starter-pack \\
    UQF_DEPLOY_TEST_QCMD=/opt/kx/bin/q UQF_DEPLOY_TEST_QHOME=/opt/kx \\
    uv run pytest python/uqs/tests/test_deploy_integration.py

The destination is wiped first, so point it at a throwaway directory. Three
artifacts are built here first - a destination takes each release once - and
the server installs every one of them OFFLINE, from the artifact's wheels.
In order, one test, because each step needs the one before:

  1. a first deployment with --init-data: SCP transfer, the offline
     release-local environment, the numerical smoke test and every process
     answering;
  2. a marker file is left in the runtime data directory;
  3. a second deployment with --restart replaces the first and keeps it;
  4. a third whose verification cannot pass (a one-second deadline) fails,
     leaves `current` on the second release and brings its processes back;
  5. the stack is stopped.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

#: The `uqs` console script of the environment running these tests.
UQS = str(Path(sys.executable).parent / "uqs")
HOST = os.environ.get("UQF_DEPLOY_TEST_HOST", "")
DEST = os.environ.get("UQF_DEPLOY_TEST_DEST", "/tmp/uqf-deploy-test")
PROFILE = os.environ.get("UQF_DEPLOY_TEST_PROFILE", "essential")

pytestmark = pytest.mark.skipif(not HOST, reason="UQF_DEPLOY_TEST_HOST names no test server")


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory) -> list[Path]:
    """Three releases of this tree; ids are UTC seconds, so a second apart."""
    out = tmp_path_factory.mktemp("dist")
    built = []
    for _ in range(3):
        r = subprocess.run(
            [UQS, "deploy", "build", "--output", str(out), "--allow-dirty"],
            capture_output=True, text=True, check=True, timeout=1800,
        )  # fmt: skip
        built.append(Path(r.stdout.strip().splitlines()[-1]))
        time.sleep(1.1)
    return built


def _deploy(artifact: Path, *extra: str) -> subprocess.CompletedProcess:
    argv = [UQS, "deploy", "push", str(artifact)]
    argv += ["--host", HOST, "--dest", DEST, "--profile", PROFILE, *extra]
    for flag, var in (
        ("--torq-home", "UQF_DEPLOY_TEST_TORQHOME"),
        ("--torq-app-home", "UQF_DEPLOY_TEST_TORQAPPHOME"),
        ("--qcmd", "UQF_DEPLOY_TEST_QCMD"),
        ("--qhome", "UQF_DEPLOY_TEST_QHOME"),
    ):
        if os.environ.get(var):
            argv += [flag, os.environ[var]]
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=3600)


def _ssh(command: str) -> str:
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", HOST, command],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return r.stdout.strip()


def test_deploy_upgrade_and_rollback_on_a_real_server(artifacts):
    _ssh(f"rm -rf {DEST}")
    first = _deploy(artifacts[0], "--init-data")
    assert first.returncode == 0, first.stderr
    release_1 = _ssh(f"basename $(readlink {DEST}/current)")

    _ssh(f"echo kept > {DEST}/shared/data/deploy-test-marker")
    second = _deploy(artifacts[1], "--restart")
    assert second.returncode == 0, second.stderr
    release_2 = _ssh(f"basename $(readlink {DEST}/current)")
    assert release_2 != release_1
    assert _ssh(f"cat {DEST}/shared/data/deploy-test-marker") == "kept"

    again = _deploy(artifacts[1], "--restart")
    assert again.returncode == 1 and "already on" in again.stderr

    third = _deploy(artifacts[2], "--restart", "--verify-timeout", "1")
    assert third.returncode == 1
    assert _ssh(f"basename $(readlink {DEST}/current)") == release_2
    assert "restarted release" in third.stdout

    _ssh(f"cd {DEST}/current && source ./deploy.env && .venv/bin/uqs stop all")
