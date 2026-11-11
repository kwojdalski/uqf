"""How CI installs its pinned binaries, d2, qlinter and rlwrap (#2171, #2721).

Two runs went red on 2026-11-05 for reasons outside the change under test: a
504 from GitHub's release download for d2, and a qlinter pin bump that ran CI
before the q-lint release had uploaded its assets. These hold the shape that
stops both: each binary comes from the Actions cache keyed by its pin, is
downloaded with retries only on a miss, is verified by sha256 either way, and
a missing qlinter asset is one clear error before anything runs without it.
rlwrap's apt install failed on DNS twice in one pull request, so it is pinned
the same way, as a .deb, and no workflow step asks apt for anything.
"""

import os
import re
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
ACTION_PATH = "./.github/actions/install-qlinter"
ACTION = yaml.safe_load((REPO / ACTION_PATH / "action.yml").read_text())
CI = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())

# the SHA every actions/cache use is pinned to, as the rest of ci.yml pins it
CACHE_SHA = re.compile(r"^actions/cache(/restore|/save)?@[0-9a-f]{40}$")


def _steps(job: str) -> list[dict]:
    return CI["jobs"][job]["steps"]


def _named(steps: list[dict], name: str) -> dict:
    (step,) = [s for s in steps if s.get("name") == name]
    return step


def _action_step(name: str) -> dict:
    return _named(ACTION["runs"]["steps"], name)


def test_no_workflow_downloads_qlinter_itself():
    """Every qlinter install goes through the one cached action."""
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text()
        assert "q-lint/releases/download" not in text, f"{path.name} downloads qlinter itself"
    users = {
        (path.name, job)
        for path in WORKFLOWS.glob("*.yml")
        for job, body in yaml.safe_load(path.read_text())["jobs"].items()
        for step in body.get("steps", [])
        if step.get("uses") == ACTION_PATH
    }
    assert {("ci.yml", "checks"), ("ci.yml", "peachq-python"), ("ci.yml", "scope")} <= users
    assert ("kdbx-attestation.yml", "kdbx-attestation") in users


def test_qlinter_is_cached_by_its_pin_and_saved_straight_after_a_miss():
    restore = _action_step("Restore qlinter from the cache")
    save = _action_step("Save qlinter to the cache")
    download = _action_step("Download qlinter (cache miss)")
    assert CACHE_SHA.match(restore["uses"]) and restore["uses"].startswith("actions/cache/restore@")
    assert CACHE_SHA.match(save["uses"]) and save["uses"].startswith("actions/cache/save@")
    assert "steps.pin.outputs.version" in restore["with"]["key"]
    assert save["with"]["key"] == "${{ steps.cache.outputs.cache-primary-key }}"
    for step in (download, save):
        assert step["if"] == "${{ steps.cache.outputs.cache-hit != 'true' }}"


def test_qlinter_download_retries_and_is_verified():
    download = _action_step("Download qlinter (cache miss)")["run"]
    assert "--retry 5" in download
    assert "curl -sSIL" in download  # the HEAD request comes first
    assert ".digest" in download and "sha256sum -c" in download
    assert "sha256sum -c" in _action_step("Install qlinter")["run"]


def test_d2_is_cached_by_version_and_sha256_and_downloaded_with_retries():
    steps = _steps("checks")
    restore = _named(steps, "Restore d2 from the cache")
    download = _named(steps, "Download d2 (cache miss)")
    save = _named(steps, "Save d2 to the cache")
    assert CACHE_SHA.match(restore["uses"]) and CACHE_SHA.match(save["uses"])
    assert "steps.d2.outputs.version" in restore["with"]["key"]
    assert "steps.d2.outputs.sha256" in restore["with"]["key"]
    for step in (download, save):
        assert step["if"] == "${{ steps.d2-cache.outputs.cache-hit != 'true' }}"
    assert "--retry 5" in download["run"]
    assert "sha256sum -c" in download["run"]
    assert "sha256sum -c" in _named(steps, "Install d2")["run"]
    assert re.fullmatch(r"[0-9a-f]{64}", _named(steps, "Pin d2")["env"]["D2_SHA256"])


def test_rlwrap_is_a_cached_deb_pinned_by_sha256_and_downloaded_with_retries():
    steps = _steps("peachq-python")
    pin = _named(steps, "Pin rlwrap")
    restore = _named(steps, "Restore rlwrap from the cache")
    download = _named(steps, "Download rlwrap (cache miss)")
    save = _named(steps, "Save rlwrap to the cache")
    install = _named(steps, "Install what a PeachQ stack needs")
    assert CACHE_SHA.match(restore["uses"]) and restore["uses"].startswith("actions/cache/restore@")
    assert CACHE_SHA.match(save["uses"]) and save["uses"].startswith("actions/cache/save@")
    assert "steps.rlwrap.outputs.sha256" in restore["with"]["key"]
    assert save["with"]["key"] == "${{ steps.rlwrap-cache.outputs.cache-primary-key }}"
    for step in (download, save):
        assert "steps.rlwrap-cache.outputs.cache-hit != 'true'" in step["if"]
    assert "--retry 5" in download["run"]
    assert "sha256sum -c" in download["run"] and "sha256sum -c" in install["run"]
    assert re.fullmatch(r"[0-9a-f]{64}", pin["env"]["RLWRAP_SHA256"])
    assert re.fullmatch(r"rlwrap_[^/]+_amd64\.deb", pin["env"]["RLWRAP_DEB"])


def test_no_workflow_step_installs_from_apt():
    """A package index fetch on every run is a network dependency the cache avoids."""
    for path in WORKFLOWS.glob("*.yml"):
        for job, body in yaml.safe_load(path.read_text())["jobs"].items():
            for step in body.get("steps", []):
                assert "apt-get" not in step.get("run", ""), (
                    f"{path.name} {job}: {step.get('name')}"
                )


def test_a_pin_bump_installs_qlinter_in_scope_first():
    """Scope gates every verification job, so a missing asset stops them all."""
    steps = _steps("scope")
    names = [s.get("name") for s in steps]
    guard = _named(steps, "The pinned qlinter release is published")
    assert guard["uses"] == ACTION_PATH
    assert guard["if"] == "${{ steps.qlinter-pin.outputs.changed == 'true' }}"
    assert (
        "-- .qlinter-version"
        in _named(steps, "Has this pull request moved the qlinter pin?")["run"]
    )
    assert names.index("The pinned qlinter release is published") < names.index(
        "Decide which verification groups run"
    )


def test_peachq_python_runs_nothing_after_a_failed_qlinter_install():
    """Its PeachQ steps run with !cancelled(), which a failed step does not stop."""
    steps = _steps("peachq-python")
    at = [s.get("id") for s in steps].index("qlinter")
    for step in steps[at + 1 :]:
        cond = step.get("if", "")
        if (
            "!cancelled()" in cond
            and "env.PEACHQ_BIN" not in cond
            and "upload-artifact" not in step.get("uses", "")
        ):
            assert "steps.qlinter.outcome == 'success'" in cond, step["name"]


def test_a_missing_asset_is_one_clear_error(tmp_path):
    """Run the action's download script against a curl that answers 404."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "curl").write_text("#!/bin/sh\nprintf 404\nexit 22\n")
    (fake / "curl").chmod(0o755)
    (fake / "gh").write_text("#!/bin/sh\necho 'gh must not be reached' >&2\nexit 1\n")
    (fake / "gh").chmod(0o755)
    version = "v9.9.9"
    asset = f"qlinter-{version}-x86_64-unknown-linux-gnu.tar.gz"
    env = {
        **os.environ,
        "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
        "VERSION": version,
        "ASSET": asset,
        "URL": f"https://github.com/kwojdalski/q-lint/releases/download/{version}/{asset}",
        "DIR": str(tmp_path / "cache"),
    }
    run = subprocess.run(
        ["bash", "-c", _action_step("Download qlinter (cache miss)")["run"]],
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    errors = [line for line in run.stdout.splitlines() if line.startswith("::error")]
    assert len(errors) == 1
    assert f"qlinter {version} has no published Linux asset (HTTP 404" in errors[0]
    assert "still uploading" in errors[0]
    assert "gh must not be reached" not in run.stderr
    assert not (tmp_path / "cache").exists()
