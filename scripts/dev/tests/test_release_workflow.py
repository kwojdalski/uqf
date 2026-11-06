"""release.yml checks the commit it builds (#2200).

`checks` calls ci.yml, whose checkouts would otherwise take the triggering
ref - on workflow_dispatch the dispatching branch's head, not the tag. These
hold the three links: release.yml resolves the tag to one commit and passes it
as `ref`, every ci.yml checkout names that ref and ci.yml reports what it
checked, and the build fails unless it builds that same commit.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


CI = _load("ci.yml")
RELEASE = _load("release.yml")
#: PyYAML reads the bare key `on` as True.
CALL = CI[True]["workflow_call"]


def _checkouts(workflow: dict) -> list[tuple[str, dict]]:
    return [
        (job, step)
        for job, body in workflow["jobs"].items()
        for step in body.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]


def test_ci_takes_a_ref_when_called_and_reports_what_it_checked():
    assert CALL["inputs"]["ref"] == {
        "description": CALL["inputs"]["ref"]["description"],
        "type": "string",
        "default": "",
    }
    assert CALL["outputs"]["checked_sha"]["value"] == "${{ jobs.scope.outputs.sha }}"
    assert CI["jobs"]["scope"]["outputs"]["sha"] == "${{ steps.checked.outputs.sha }}"


def test_every_ci_checkout_checks_out_the_callers_ref():
    found = _checkouts(CI)
    assert found, "ci.yml has no checkout - this test reads the wrong file"
    for job, step in found:
        assert step.get("with", {}).get("ref") == "${{ inputs.ref }}", job


def test_release_passes_the_tags_commit_to_its_checks():
    jobs = RELEASE["jobs"]
    assert jobs["checks"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["checks"]["needs"] == "revision"
    assert jobs["checks"]["with"] == {"ref": "${{ needs.revision.outputs.sha }}"}
    (_, checkout), *_ = _checkouts({"jobs": {"revision": jobs["revision"]}})
    assert checkout["with"]["ref"] == "${{ env.TAG }}"


def test_the_build_refuses_a_commit_other_than_the_one_checked():
    build = RELEASE["jobs"]["build"]
    assert set(build["needs"]) == {"revision", "checks"}
    steps = build["steps"]
    names = [s.get("name") for s in steps]
    guard = steps[names.index("The commit built is the commit checked")]
    assert guard["env"] == {
        "RESOLVED": "${{ needs.revision.outputs.sha }}",
        "CHECKED": "${{ needs.checks.outputs.checked_sha }}",
    }
    # after the checkout, before anything is built
    checkout = next(i for i, s in enumerate(steps) if "actions/checkout@" in s.get("uses", ""))
    assert checkout < names.index(guard["name"]) < names.index("Build the artifacts")
    assert "exit 1" in guard["run"]


def _guard_run(tmp_path: Path, resolved: str, checked: str):
    """The build's guard step, run by bash in a one-commit repository."""
    steps = RELEASE["jobs"]["build"]["steps"]
    guard = next(s for s in steps if s.get("name") == "The commit built is the commit checked")
    tmp_path.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    env = dict(os.environ, RESOLVED=resolved or head, CHECKED=checked or head)
    if checked == "-":
        env["CHECKED"] = ""
    return subprocess.run(
        ["bash", "-c", guard["run"]], cwd=tmp_path, env=env, capture_output=True, text=True
    )


def test_the_guard_passes_only_when_built_checked_and_resolved_agree(tmp_path):
    other = "0" * 40
    assert _guard_run(tmp_path / "same", "", "").returncode == 0
    assert _guard_run(tmp_path / "unchecked", "", other).returncode == 1
    assert _guard_run(tmp_path / "moved", other, "").returncode == 1
    assert _guard_run(tmp_path / "nothing", "", "-").returncode == 1
