"""scripts/dev/ci_scope.py and the workflow that obeys it (#1879).

Two properties carry the weight: a path-filtered job runs for every change
that can alter its result, and full mode - a push, `ci:full`, a dispatch with
`full: true` - runs every job. The workflow half is that a skipped job counts
as passed for "Repository checks" only when Scope said to skip it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "ci_scope", Path(__file__).resolve().parents[1] / "ci_scope.py"
)
assert _SPEC and _SPEC.loader
scope = importlib.util.module_from_spec(_SPEC)
sys.modules["ci_scope"] = scope
_SPEC.loader.exec_module(scope)

CI = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())


@pytest.mark.parametrize(
    ("changes", "docs", "q"),
    [
        ([("M", "docs/guides/deploy.md")], True, False),
        ([("M", "python/uqf_frontend/src/uqf_frontend/app.py")], False, False),
        ([("M", "scripts/gates/check_hook_scopes.py")], False, False),
        ([("M", "src/pricing/forwards.q")], True, True),
        ([("M", "tests/q/test_forwards.q")], False, True),
        ([("M", "lib/torq/torq.q")], False, True),
        ([("M", "scripts/torqconfig/dataaccess/tableproperties.csv")], False, True),
        # the q job's own Python: its driver, and what conftest imports
        ([("M", "tests/qrun/test_q_suite.py")], False, True),
        ([("M", "python/uqs/src/uqs/interpreter.py")], False, True),
        ([("M", "scripts/portable/flatten_contexts.py")], False, True),
        ([("M", "uv.lock")], False, True),
        # the render's tooling
        ([("M", "scripts/generate/q_api_docs.py")], True, False),
        ([("M", "scripts/gates/check_doc_links.py")], True, False),
        ([("M", "scripts/peachq.json")], True, True),
        # a deletion a document links to
        ([("D", "scripts/dev/odbc_rosetta.sh")], True, True),
        # this decision and the workflow it decides for
        ([("M", ".github/workflows/ci.yml")], True, True),
        ([("M", "scripts/dev/ci_scope.py")], True, True),
    ],
)
def test_a_path_filtered_job_runs_for_what_can_change_its_result(changes, docs, q):
    s = scope.decide(changes, None)
    assert (s.docs, s.q, s.full) == (docs, q, False), s.why


def test_every_path_says_why_it_ran_a_job():
    s = scope.decide([("M", "src/pricing/forwards.q"), ("M", "README.md")], None)
    assert s.why["q"] == ["src/pricing/forwards.q: q"]
    assert all(r.startswith("src/pricing/forwards.q") for r in s.why["docs"])


@pytest.mark.parametrize(
    ("event", "labels", "full_input", "full"),
    [
        ("push", set(), False, True),
        ("pull_request", set(), False, False),
        ("pull_request", {"ci:full"}, False, True),
        ("pull_request", {"enhancement"}, False, False),
        ("workflow_dispatch", set(), True, True),
        ("workflow_dispatch", set(), False, False),
        ("schedule", set(), False, True),
    ],
)
def test_full_mode_is_a_push_the_label_or_the_dispatch_input(event, labels, full_input, full):
    why = scope.full_reason(event, labels, full_input)
    assert (why is not None) == full
    if full:
        s = scope.decide([], why)
        assert (s.full, s.docs, s.q) == (True, True, True)


def test_the_q_job_s_python_is_read_from_its_imports():
    """A list of files would go stale; the imports cannot."""
    found = scope.imported(scope.Q_ENTRIES)
    assert {
        "conftest.py",
        "tests/qrun/test_q_suite.py",
        "tests/qrun/qrun_support.py",
        "python/uqs/src/uqs/interpreter.py",
        "python/uqs/src/uqs/paths.py",
    } <= found
    assert not any(f.startswith("python/uqf_frontend/") for f in found)


def test_the_cli_writes_the_outputs_and_the_summary(tmp_path):
    out, summary = tmp_path / "out", tmp_path / "summary.md"
    subprocess.run(
        [sys.executable, str(REPO / "scripts/dev/ci_scope.py"), "--event", "push",
         "--output", str(out), "--summary", str(summary)],
        check=True, capture_output=True, cwd=REPO,
    )  # fmt: skip
    assert out.read_text().splitlines() == ["full=true", "docs=true", "q=true"]
    assert "Docs links" in summary.read_text()


# --- the workflow -------------------------------------------------------------


def test_the_workflow_offers_every_full_switch():
    on = CI.get("on", CI.get(True))  # YAML 1.1 reads a bare `on` as True
    assert "labeled" in on["pull_request"]["types"]
    assert on["workflow_dispatch"]["inputs"]["full"]["default"] is False
    assert on["workflow_call"]["inputs"]["full"]["default"] is True, "a release runs everything"
    assert on["push"]["branches"] == ["master"]
    step = CI["jobs"]["scope"]["steps"][-1]
    assert "ci_scope.py" in step["run"] and step["env"]["FULL"] == "${{ inputs.full }}"


@pytest.mark.parametrize(("job", "output"), [("doc-links", "docs"), ("peachq-q", "q")])
def test_each_filtered_job_runs_on_scope_s_word(job, output):
    assert CI["jobs"][job]["needs"] == "scope"
    assert CI["jobs"][job]["if"] == f"${{{{ needs.scope.outputs.{output} == 'true' }}}}"


def test_repository_checks_needs_every_job_and_excuses_only_a_scoped_skip():
    agg = CI["jobs"]["repository-checks"]
    assert agg["if"] == "${{ always() }}"
    gated = {j for j in CI["jobs"] if j not in ("repository-checks", "web")}
    assert set(agg["needs"]) == gated
    run = agg["steps"][-1]["run"]
    assert 'filtered "Docs links" "$DOCS" "$DOCS_NEEDED"' in run
    assert 'filtered "PeachQ q tests" "$Q" "$Q_NEEDED"' in run
    assert '[ "$2" = skipped ] && [ "$3" = false ]' in run


@pytest.mark.parametrize(
    ("results", "docs_needed", "q_needed", "ok"),
    [
        (
            dict(SCOPE="success", CHECKS="success", PYTHON="success", DOCS="skipped", Q="skipped"),
            "false",
            "false",
            True,
        ),  # noqa: E501
        (
            dict(SCOPE="success", CHECKS="success", PYTHON="success", DOCS="success", Q="success"),
            "true",
            "true",
            True,
        ),  # noqa: E501
        # skipped although Scope said it was needed: never green
        (
            dict(SCOPE="success", CHECKS="success", PYTHON="success", DOCS="skipped", Q="skipped"),
            "true",
            "false",
            False,
        ),  # noqa: E501
        # Scope failed: its outputs are empty, and a skip is no pass
        (
            dict(SCOPE="failure", CHECKS="success", PYTHON="success", DOCS="skipped", Q="skipped"),
            "",
            "",
            False,
        ),  # noqa: E501
        (
            dict(SCOPE="success", CHECKS="success", PYTHON="failure", DOCS="skipped", Q="skipped"),
            "false",
            "false",
            False,
        ),  # noqa: E501
        (
            dict(SCOPE="success", CHECKS="success", PYTHON="success", DOCS="failure", Q="skipped"),
            "true",
            "false",
            False,
        ),  # noqa: E501
    ],
)
def test_the_aggregator_step_itself(results, docs_needed, q_needed, ok):
    step = CI["jobs"]["repository-checks"]["steps"][-1]
    env = {**results, "DOCS_NEEDED": docs_needed, "Q_NEEDED": q_needed, "PATH": "/usr/bin:/bin"}
    code = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True).returncode
    assert (code == 0) == ok
