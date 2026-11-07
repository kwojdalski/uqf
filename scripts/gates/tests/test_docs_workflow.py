"""Docs reuse must preserve input identity, full checks and publication provenance."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CI = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
PAGES = yaml.safe_load((REPO / ".github/workflows/docs.yml").read_text())
STEPS = CI["jobs"]["doc-links"]["steps"]


def _node(script, data):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to execute the workflow's JavaScript")
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(data), capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(result.stdout)


def _git(root, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Docs test",
         "-c", "user.email=docs@example.com", *args],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def test_the_cache_ignores_empty_commits_but_invalidates_for_every_tracked_input(tmp_path):
    _git(tmp_path, "init")
    for file in ("page.md", "source.q", "linked.csv", "workflow.yml"):
        (tmp_path / file).write_text("original\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "source")
    command = next(s["run"] for s in STEPS if s.get("id") == "docs-tree")
    output = tmp_path / "output"

    def key():
        output.write_text("")
        subprocess.run(
            ["bash", "-e", "-c", command], cwd=tmp_path,
            env={**os.environ, "GITHUB_OUTPUT": str(output)}, check=True,
        )  # fmt: skip
        return output.read_text().strip()

    previous = key()
    _git(tmp_path, "commit", "--allow-empty", "-m", "attestation")
    assert key() == previous, "a proof commit must reuse the checked tree"
    for file in ("page.md", "source.q", "linked.csv", "workflow.yml"):
        (tmp_path / file).write_text("changed\n")
        _git(tmp_path, "add", file)
        _git(tmp_path, "commit", "-m", f"change {file}")
        current = key()
        assert current != previous, file
        previous = current
    (tmp_path / "linked.csv").unlink()
    _git(tmp_path, "add", "-u")
    _git(tmp_path, "commit", "-m", "remove linked target")
    assert key() != previous
    restore = next(s for s in STEPS if s.get("id") == "docs-cache")
    save = next(s for s in STEPS if s.get("name") == "Save the validated site")
    assert restore["with"]["key"] == save["with"]["key"]
    assert "steps.docs-tree.outputs.tree" in restore["with"]["key"]
    assert "restore-keys" not in restore["with"], "a partial match is not verified"


@pytest.mark.parametrize(("full", "cached"), [(False, False), (False, True), (True, True)])
def test_only_an_exact_hit_in_targeted_mode_skips_the_render(full, cached):
    result = _node(
        r"""
        import fs from 'node:fs';
        const input = JSON.parse(fs.readFileSync(0, 'utf8'));
        const needs = {scope: {outputs: {full: String(input.full)}}};
        const steps = {'docs-cache': {outputs: {}}};
        const allowed = step => {
          const expr = step.if.replace(/^\$\{\{|\}\}$/g, '').trim()
            .replace(/\.([\w-]+)/g, '["$1"]');
          return new Function('needs', 'steps', `return (${expr})`)(needs, steps);
        };
        if (allowed(input.restore)) {
          steps['docs-cache'].outputs['cache-hit'] = String(input.cached);
        }
        console.log(JSON.stringify(input.render.map(allowed)));
        """,
        {
            "full": full, "cached": cached,
            "restore": next(s for s in STEPS if s.get("id") == "docs-cache"),
            "render": [s for s in STEPS if s.get("name") in (
                "Install Quarto", "Restore PeachQ built from its pinned commit",
                "Provide PeachQ for the q API pages",
                "Check every link and anchor in docs/ resolves",
            )],
        },
    )  # fmt: skip
    assert len(result) == 4
    assert result == [full or not cached] * 4


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"event": "pull_request"},
        {"head_branch": "feature"},
        {"path": ".github/workflows/other.yml"},
        {"conclusion": "failure"},
        {"status": "in_progress"},
        {"head_repository": {"full_name": "other/uqf"}},
    ],
)
@pytest.mark.parametrize("manual", [False, True])
def test_publication_executes_the_source_guard_and_rejects_untrusted_runs(change, manual):
    source = PAGES["jobs"]["deploy"]["steps"][0]
    run = {
        "path": ".github/workflows/ci.yml",
        "head_branch": "master",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "head_repository": {"full_name": "kwojdalski/uqf"},
        **change,
    }
    result = _node(
        """
        import fs from 'node:fs';
        const input = JSON.parse(fs.readFileSync(0, 'utf8'));
        const outputs = {};
        const payload = input.manual ? {inputs: {ci_run_id: '123'}} : {workflow_run: {id: 123}};
        const github = {rest: {actions: {getWorkflowRun: async args => {
          if (args.run_id !== 123) throw new Error('wrong run requested');
          return {data: input.run};
        }}}};
        try {
          const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
          await new AsyncFunction('github', 'context', 'core', input.script)(
            github, {repo: {owner: 'kwojdalski', repo: 'uqf'}, payload},
            {setOutput: (key, value) => outputs[key] = value});
          console.log(JSON.stringify({outputs}));
        } catch (error) {
          console.log(JSON.stringify({error: error.message}));
        }
        """,
        {"script": source["with"]["script"], "run": run, "manual": manual},
    )
    if change:
        assert "Only a successful master push" in result["error"]
    else:
        assert result == {"outputs": {"sha": run["head_sha"]}}


def test_pages_downloads_the_selected_run_and_never_builds_again():
    steps = PAGES["jobs"]["deploy"]["steps"]
    download = steps[1]
    assert download["with"]["run-id"] == "${{ github.event.workflow_run.id || inputs.ci_run_id }}"
    assert download["with"]["name"] == "docs-site-${{ steps.source.outputs.sha }}"
    assert not any("run" in s or "checkout@" in s.get("uses", "") for s in steps)


@pytest.mark.parametrize(
    ("event", "conclusion", "ci_event", "sha", "publish"),
    [
        ("workflow_run", "success", "push", "current", True),
        ("workflow_run", "success", "pull_request", "current", False),
        ("workflow_run", "failure", "push", "current", False),
        ("workflow_run", "success", "push", "older", False),
        ("workflow_dispatch", "", "", "", True),
    ],
)
def test_automatic_publication_waits_for_success_and_ignores_superseded_runs(
    event, conclusion, ci_event, sha, publish
):
    result = _node(
        """
        import fs from 'node:fs';
        const input = JSON.parse(fs.readFileSync(0, 'utf8'));
        console.log(JSON.stringify(new Function('github', `return (${input.expr})`)(input.github)));
        """,
        {
            "expr": PAGES["jobs"]["deploy"]["if"],
            "github": {
                "event_name": event, "sha": "current",
                "event": {"workflow_run": {
                    "conclusion": conclusion, "event": ci_event, "head_sha": sha,
                }},
            },
        },
    )  # fmt: skip
    assert result is publish
