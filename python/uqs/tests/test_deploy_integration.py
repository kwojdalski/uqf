"""scripts/deploy.py against a real, disposable server (#773).

Skipped unless UQF_DEPLOY_TEST_HOST names one: it needs ssh access, a
licensed q, an installed TorQ and starter pack, uv and Python 3.14 there, and
it starts and stops a stack - so it never runs in an ordinary test pass.

    UQF_DEPLOY_TEST_HOST=uqf-test UQF_DEPLOY_TEST_DEST=/tmp/uqf-deploy \\
    UQF_DEPLOY_TEST_TORQHOME=/opt/torq \\
    UQF_DEPLOY_TEST_TORQAPPHOME=/opt/torq-finance-starter-pack \\
    UQF_DEPLOY_TEST_QCMD=/opt/kx/bin/q UQF_DEPLOY_TEST_QHOME=/opt/kx \\
    uv run pytest python/uqs/tests/test_deploy_integration.py

The destination is wiped first, so point it at a throwaway directory. In
order, one test, because each step needs the one before:

  1. a first deployment with --init-data: SCP transfer, the release-local
     environment, the numerical smoke test and every process answering;
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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOST = os.environ.get("UQF_DEPLOY_TEST_HOST", "")
DEST = os.environ.get("UQF_DEPLOY_TEST_DEST", "/tmp/uqf-deploy-test")
PROFILE = os.environ.get("UQF_DEPLOY_TEST_PROFILE", "essential")

pytestmark = pytest.mark.skipif(not HOST, reason="UQF_DEPLOY_TEST_HOST names no test server")


def _deploy(*extra: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(ROOT / "scripts" / "deploy.py"), "--host", HOST, "--dest", DEST]
    argv += ["--profile", PROFILE, "--allow-dirty", *extra]
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


def test_deploy_upgrade_and_rollback_on_a_real_server():
    _ssh(f"rm -rf {DEST}")
    first = _deploy("--init-data")
    assert first.returncode == 0, first.stderr
    release_1 = _ssh(f"basename $(readlink {DEST}/current)")

    _ssh(f"echo kept > {DEST}/shared/data/deploy-test-marker")
    second = _deploy("--restart")
    assert second.returncode == 0, second.stderr
    release_2 = _ssh(f"basename $(readlink {DEST}/current)")
    assert release_2 != release_1
    assert _ssh(f"cat {DEST}/shared/data/deploy-test-marker") == "kept"

    third = _deploy("--restart", "--verify-timeout", "1")
    assert third.returncode == 1
    assert _ssh(f"basename $(readlink {DEST}/current)") == release_2
    assert "restarted release" in third.stdout

    _ssh(f"cd {DEST}/current && source ./deploy.env && uv run --frozen --quiet uqs stop all")
