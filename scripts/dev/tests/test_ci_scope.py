"""scripts/dev/ci_scope.py and the workflow that obeys it (#1879, #1955).

Two properties carry the weight: a verification group runs for every change
that can alter its result, and full mode - a push, `ci:full`, a dispatch with
`full: true` - runs every group. The workflow half is that a skipped job counts
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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "dev" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


scope = _load("ci_scope")
sys.modules["ci_scope"] = scope

CI = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())

ALL = (True, True, True, True)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        # (docs, q, frontend, python)
        # -- the browser application ------------------------------------
        ([("M", "web/src/App.tsx")], (False, False, True, False)),
        ([("M", "web/vite.config.ts")], (False, False, True, False)),
        ([("M", "web/package-lock.json")], (False, False, True, False)),
        ([("A", "web/src/Chart.tsx")], (False, False, True, False)),
        # a web/ file a Python file names: the API types uqf_frontend's tests
        # hold to its models, and the Node pin the tool-pins gate reads
        ([("M", "web/src/api.ts")], (False, False, True, True)),
        ([("M", "web/package.json")], (False, False, True, True)),
        ([("M", "web/README.md")], (False, False, True, True)),
        # -- Python ------------------------------------------------------
        ([("M", "python/uqf_frontend/src/uqf_frontend/app.py")], (False, False, False, True)),
        ([("M", "python/uqf_frontend/src/uqf_frontend/models.py")], (False, False, False, True)),
        ([("M", "scripts/gates/check_hook_scopes.py")], (False, False, False, True)),
        # -- q: the q suite, and the Python tests that start a q ----------
        ([("M", "src/pricing/forwards.q")], (True, True, False, True)),
        ([("M", "tests/q/test_forwards.q")], (False, True, False, True)),
        ([("M", "lib/torq/torq.q")], (False, True, False, True)),
        ([("M", "scripts/torqconfig/dataaccess/tableproperties.csv")], (False, True, False, True)),
        # the q job's own Python: its driver, and what conftest imports
        ([("M", "tests/qrun/test_q_suite.py")], (False, True, False, True)),
        ([("M", "python/uqs/src/uqs/interpreter.py")], (False, True, False, True)),
        ([("M", "scripts/portable/flatten_contexts.py")], (False, True, False, True)),
        # -- executable docs: rendered, and their q blocks run as Python tests
        ([("M", "docs/guides/deploy.md")], (True, False, False, True)),
        ([("M", "tests/qrun/test_doc_blocks.py")], (False, False, False, True)),
        # the render's tooling
        ([("M", "scripts/generate/q_api_docs.py")], (True, False, False, True)),
        ([("M", "scripts/gates/check_doc_links.py")], (True, False, False, True)),
        ([("M", "scripts/peachq.json")], (True, True, False, True)),
        # -- deletions -------------------------------------------------------
        # a deletion a document links to
        ([("D", "scripts/dev/odbc_rosetta.sh")], (True, True, False, True)),
        ([("D", "web/src/api.ts")], (False, False, True, True)),
        ([("D", "web/src/App.tsx")], (False, False, True, False)),
        ([("D", "src/pricing/forwards.q")], (True, True, False, True)),
        # -- shared test configuration: every group --------------------------
        ([("M", ".github/workflows/ci.yml")], ALL),
        ([("M", "scripts/dev/ci_scope.py")], ALL),
        ([("M", "pyproject.toml")], ALL),
        ([("M", "uv.lock")], ALL),
        ([("M", "conftest.py")], ALL),
        ([("M", "python/uqs/tests/conftest.py")], ALL),
        ([("M", "scripts/dev/test_reach.py")], ALL),
        ([("M", "scripts/dev/python_tests_hook.py")], ALL),
        # -- unknown paths: every group --------------------------------------
        ([("A", "Makefile")], ALL),
        ([("A", "mobile/src/app.kt")], ALL),
        ([("M", "web/src/App.tsx"), ("A", "rust/lib.rs")], ALL),
        # -- a mix is the union ----------------------------------------------
        ([("M", "web/src/App.tsx"), ("M", "src/pricing/forwards.q")], (True, True, True, True)),
        ([("M", "web/src/App.tsx"), ("M", "docs/guides/deploy.md")], (True, False, True, True)),
    ],
)
def test_each_group_runs_for_what_can_change_its_result(changes, expected):
    s = scope.decide(changes, None)
    assert (s.docs, s.q, s.frontend, s.python) == expected, s.why
    assert not s.full


def test_every_decision_to_run_says_why_path_by_path():
    changes = [("M", "src/pricing/forwards.q"), ("M", "web/src/App.tsx"), ("M", "README.md")]
    s = scope.decide(changes, None)
    assert s.why["q"] == ["src/pricing/forwards.q: q"]
    assert s.why["frontend"] == ["web/src/App.tsx: the browser application"]
    assert s.why["python"] == [
        "src/pricing/forwards.q: q, which the Python tests start",
        "README.md: outside web/, where a Python test or a gate's tests can read it",
    ]
    for group in scope.GROUPS:
        assert all(r.split(":")[0] in {p for _, p in changes} for r in s.why[group]), s.why[group]


def test_a_skipped_group_says_why_it_was_skipped():
    s = scope.decide([("M", "web/src/App.tsx")], None)
    text = scope.report(s, 1)
    assert f"**PeachQ Python tests**: skipped - {scope.SKIPPED['python']}" in text
    assert "**PeachQ q tests**: skipped - " in text
    assert "**Docs links**: skipped - " in text
    assert "**Browser application**: runs" in text
    assert "**Hooks and static gates**: runs" in text


def test_a_python_reader_of_a_web_file_is_named():
    """The cross-language edge: web/src/api.ts mirrors the API models, and
    uqf_frontend's tests read it to hold the two together."""
    named = scope.named_by_python(["web/src/api.ts", "web/src/App.tsx"])
    assert set(named) == {"web/src/api.ts"}
    s = scope.decide([("M", "web/src/api.ts")], None)
    assert s.why["python"] == [f"web/src/api.ts: named by {named['web/src/api.ts']}"]


def test_shared_configuration_is_the_python_tests_hook_s_own_list():
    """One rule for "this shapes every test", read from the hook rather than copied."""
    hook = _load("python_tests_hook")
    assert scope.shapes_every_test() == hook.SHAPES_EVERY_TEST
    for path in hook.SHAPES_EVERY_TEST:
        assert scope.shared(path), path


def test_every_tracked_file_is_in_a_known_area():
    """An unknown path runs everything, so a new area must be classified on
    purpose: add it to KNOWN once its groups are clear."""
    files = scope._test_impact().tracked(REPO)
    unknown = [f for f in files if not scope.KNOWN.search(f)]
    assert not unknown, f"not in ci_scope.KNOWN (each would run every group): {unknown[:10]}"


def _reach_record() -> dict | None:
    """The local record of what each Python test reads (the python-tests hook
    writes it; CI has none): this checkout's, else the one its clone shares."""
    test_reach = _load("test_reach")
    record = test_reach.load()
    if record is None:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip
        if common:
            record = test_reach.load(Path(common) / "uqf-test-records" / test_reach.RECORD.name)
    return record


def test_the_reach_record_agrees_about_which_web_files_python_reads():
    """CI's rule against the hooks' record, so the two never contradict.

    A web/ file the planner calls Python-free may be read by a Python test only
    as part of SCANNING web/ - reading every tracked web/ file of its kind
    (the whole tree; a gate over every .ts). Those are whole-tree gates, which
    "Hooks and static gates" runs over every file on every run, and snapshots
    of the tree whose result turns on no file's content. A test reading the
    file by itself names it, and the Python tests must run for it."""
    record = _reach_record()
    if record is None:
        pytest.skip("no .testreach.json: the python-tests hook writes one at commit")
    test_reach = _load("test_reach")
    web = [f for f in scope._test_impact().tracked(REPO) if f.startswith("web/")]
    reads = {t: test_reach.reach(record, t).get("reads", set()) for t in record["tests"]}
    for path in web:
        kind = [w for w in web if Path(w).suffix == Path(path).suffix]
        scan = kind if len(kind) > 1 else web
        specific = [t for t, r in reads.items() if path in r and not set(scan) <= r]
        if specific:
            s = scope.decide([("M", path)], None)
            assert s.python, f"{path} is read by {specific[:3]}, but CI would skip the Python tests"


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
        s = scope.decide([("M", "web/src/App.tsx")], why)
        assert (s.full, s.docs, s.q, s.frontend, s.python) == (True,) * 5
        assert all(s.why[g] == [why] for g in scope.GROUPS)


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
    assert out.read_text().splitlines() == [
        "full=true", "docs=true", "q=true", "frontend=true", "python=true",
    ]  # fmt: skip
    assert all(title in summary.read_text() for title in scope.GROUPS.values())


# --- the workflow -------------------------------------------------------------


def test_the_workflow_offers_every_full_switch():
    on = CI.get("on", CI.get(True))  # YAML 1.1 reads a bare `on` as True
    assert "labeled" in on["pull_request"]["types"]
    assert on["workflow_dispatch"]["inputs"]["full"]["default"] is False
    assert on["workflow_call"]["inputs"]["full"]["default"] is True, "a release runs everything"
    assert on["push"]["branches"] == ["master"]
    step = CI["jobs"]["scope"]["steps"][-1]
    assert "ci_scope.py" in step["run"] and step["env"]["FULL"] == "${{ inputs.full }}"


#: Each job Scope decides for, and the output it obeys.
FILTERED = {"doc-links": "docs", "peachq-q": "q", "web": "frontend", "peachq-python": "python"}


@pytest.mark.parametrize(("job", "output"), FILTERED.items())
def test_each_filtered_job_runs_on_scope_s_word(job, output):
    assert CI["jobs"][job]["needs"] == "scope"
    assert CI["jobs"][job]["if"] == f"${{{{ needs.scope.outputs.{output} == 'true' }}}}"
    assert CI["jobs"]["scope"]["outputs"][output] == f"${{{{ steps.scope.outputs.{output} }}}}"
    assert output in scope.GROUPS and CI["jobs"][job]["name"].startswith(scope.GROUPS[output])


def test_static_gates_run_on_every_run():
    assert "if" not in CI["jobs"]["checks"]
    assert "needs" not in CI["jobs"]["checks"]


def test_repository_checks_needs_every_job_and_excuses_only_a_scoped_skip():
    agg = CI["jobs"]["repository-checks"]
    assert agg["if"] == "${{ always() }}"
    assert set(agg["needs"]) == set(CI["jobs"]) - {"repository-checks"}
    step = agg["steps"][-1]
    run, env = step["run"], step["env"]
    assert '[ "$2" = skipped ] && [ "$3" = false ]' in run
    assert 'need "Scope" "$SCOPE"' in run and 'need "Hooks and static gates" "$CHECKS"' in run
    for job, output in FILTERED.items():
        # the line that judges this job reads its result and Scope's word for it
        line = next(
            ln.strip()
            for ln in run.splitlines()
            if ln.strip().startswith(f'filtered "{scope.GROUPS[output]}"')
        )
        result, needed = line.rsplit(" ", 2)[-2:]
        assert env[result.strip('"$')] == f"${{{{ needs.{job}.result }}}}"
        assert env[needed.strip('"$')] == f"${{{{ needs.scope.outputs.{output} }}}}"


OK = dict(SCOPE="success", CHECKS="success")
NONE_NEEDED = dict(DOCS_NEEDED="false", Q_NEEDED="false", WEB_NEEDED="false", PYTHON_NEEDED="false")
ALL_NEEDED = dict(DOCS_NEEDED="true", Q_NEEDED="true", WEB_NEEDED="true", PYTHON_NEEDED="true")
ALL_SKIPPED = dict(DOCS="skipped", Q="skipped", WEB="skipped", PYTHON="skipped")
ALL_PASSED = dict(DOCS="success", Q="success", WEB="success", PYTHON="success")
FRONTEND_ONLY = {**NONE_NEEDED, "WEB_NEEDED": "true", **ALL_SKIPPED, "WEB": "success"}


@pytest.mark.parametrize(
    ("env", "ok"),
    [
        ({**OK, **NONE_NEEDED, **ALL_SKIPPED}, True),
        ({**OK, **ALL_NEEDED, **ALL_PASSED}, True),
        ({**OK, **FRONTEND_ONLY}, True),
        # skipped although Scope said it was needed: never green
        ({**OK, **FRONTEND_ONLY, "PYTHON_NEEDED": "true"}, False),
        ({**OK, **FRONTEND_ONLY, "WEB": "skipped"}, False),
        ({**OK, **ALL_NEEDED, **ALL_PASSED, "DOCS": "skipped"}, False),
        # Scope failed: its outputs are empty, and a skip is no pass
        ({**OK, "SCOPE": "failure", **ALL_SKIPPED}, False),
        ({**OK, "SCOPE": "cancelled", **ALL_SKIPPED}, False),
        # a job that ran and failed, needed or not
        ({**OK, **ALL_NEEDED, **ALL_PASSED, "PYTHON": "failure"}, False),
        ({**OK, **ALL_NEEDED, **ALL_PASSED, "WEB": "failure"}, False),
        ({**OK, **NONE_NEEDED, **ALL_SKIPPED, "DOCS": "failure"}, False),
        # the static gates never skip
        ({**OK, "CHECKS": "skipped", **NONE_NEEDED, **ALL_SKIPPED}, False),
    ],
)
def test_the_aggregator_step_itself(env, ok):
    step = CI["jobs"]["repository-checks"]["steps"][-1]
    code = subprocess.run(
        ["bash", "-c", step["run"]], env={**env, "PATH": "/usr/bin:/bin"}, capture_output=True
    ).returncode
    assert (code == 0) == ok
