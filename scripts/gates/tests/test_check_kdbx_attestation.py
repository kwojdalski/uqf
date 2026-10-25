"""Tests for `scripts/gates/check_kdbx_attestation.py` and
`scripts/dev/attest_kdbx.py` (#1014), against a throwaway git repository."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("check_kdbx_attestation", "scripts/gates/check_kdbx_attestation.py")
attest = _load("attest_kdbx", "scripts/dev/attest_kdbx.py")


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(repo: Path, path: str, text: str, msg: str = "change") -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    sh(repo, "add", path)
    sh(repo, "commit", "-q", "-m", msg)
    return sh(repo, "rev-parse", "HEAD")


def attest_here(repo: Path) -> str:
    tree = sh(repo, "rev-parse", "HEAD^{tree}")
    sh(repo, "commit", "-q", "--allow-empty", "-m", "attest", "-m", f"KDB-X-gates: {tree}")
    return sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    sh(tmp_path, "init", "-q", "-b", "master")
    sh(tmp_path, "config", "user.email", "t@example.com")
    sh(tmp_path, "config", "user.name", "t")
    commit(tmp_path, "README.md", "x\n", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_change_the_gates_do_not_judge_needs_nothing(repo):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "web/src/app.ts", "export const y = 1;\n")
    ok, why = check.report(base, head)
    assert ok and "no attestation needed" in why


@pytest.mark.parametrize(
    "path",
    [
        # q-tests runs the ```q blocks in docs/ (#1334).
        "docs/guide.md",
        # contract-surface reads the process registry and Python (#1334).
        "scripts/processes/process.csv",
        "python/uqs/x.py",
        # What the hooks compare against, not only what they run.
        "tests/q/coverage_baseline.txt",
        "docs/reference/surfaces/current/functions.csv",
    ],
)
def test_a_change_a_kdbx_hook_runs_on_needs_an_attestation(repo, path):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, path, "x\n")
    ok, why = check.report(base, head)
    assert not ok
    assert path in why and "attest_kdbx.py" in why


def test_what_is_judged_is_read_from_the_hooks_own_scopes():
    """Every attested hook's `files:` less `exclude:` is judged - and nothing
    else - so the scope is the hooks', not a second list kept here."""
    scopes = check.judged_scopes(
        "repos:\n  - repo: local\n    hooks:\n"
        + "".join(
            f"      - id: {hook}\n        files: '^only/{hook}/'\n        exclude: 'skip'\n"
            for hook in attest.HOOKS
        )
    )
    for hook in attest.HOOKS:
        assert check.is_judged(f"only/{hook}/a.txt", scopes)
        assert not check.is_judged(f"only/{hook}/skip.txt", scopes)
    assert not check.is_judged("src/a.q", scopes)


def test_an_attested_hook_without_a_scope_is_refused_not_guessed():
    config = "repos:\n  - repo: local\n    hooks:\n      - id: q-tests\n        files: 'x'\n"
    with pytest.raises(SystemExit, match="no `files:` scope for q-coverage"):
        check.judged_scopes(config)


def test_a_q_change_without_an_attestation_is_reported(repo):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "src/a.q", "a:1\n")
    ok, why = check.report(base, head)
    assert not ok
    assert "src/a.q" in why and "attest_kdbx.py" in why


def test_an_attestation_of_exactly_this_tree_passes(repo):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    assert check.report(base, head) == (True, "the KDB-X gates passed on exactly this tree")


def test_a_change_after_the_attestation_needs_a_new_one(repo):
    """The case that broke master: what merges is not what was run."""
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    attest_here(repo)
    head = commit(repo, "src/a.q", "a:2\n")
    assert not check.report(base, head)[0]


def test_an_attested_head_behind_its_base_is_reported(repo):
    fork = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "-b", "pr")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    sh(repo, "checkout", "-q", "master")
    base = commit(repo, "src/b.q", "b:1\n")
    ok, why = check.report(base, head)
    assert not ok and "does not contain the base's tip" in why
    assert check.report(fork, head)[0]


def _main(base: str, head: str, tmp_path, capsys) -> tuple[int, str, str]:
    """main() as CI's step runs it: (exit code, stdout, step summary)."""
    summary = tmp_path / "summary.md"
    code = check.main(["check_kdbx_attestation.py", base, head, str(summary)])
    return code, capsys.readouterr().out, summary.read_text() if summary.exists() else ""


def test_ci_fails_a_q_pr_with_no_attestation(repo, tmp_path, capsys):
    """#1543: the check blocks - an exit code CI does not swallow, an error
    annotation on the PR rather than a warning, and the reason on the summary."""
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "src/a.q", "a:1\n")
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 1
    assert "::error title=KDB-X attestation::" in out and "src/a.q" in out
    assert "attest_kdbx.py" in summary


def test_ci_fails_a_q_pr_whose_attestation_is_stale(repo, tmp_path, capsys):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    attest_here(repo)
    head = commit(repo, "src/a.q", "a:2\n")
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 1
    assert "::error title=KDB-X attestation::" in out
    assert "no KDB-X attestation" in summary


def test_ci_passes_a_q_pr_attested_on_its_tree(repo, tmp_path, capsys):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 0 and "::error" not in out
    assert "passed on exactly this tree" in summary


def test_ci_passes_a_pr_that_touches_no_q(repo, tmp_path, capsys):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "web/src/app.ts", "export const y = 1;\n")
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 0 and "::error" not in out
    assert "no attestation needed" in summary


def test_an_override_passes_an_unattested_pr_and_says_so(repo, tmp_path, capsys, monkeypatch):
    """#1543: the one way past the check is a labelled, visible override -
    a pass that names itself, not a silent success."""
    monkeypatch.setenv(check.OVERRIDE_ENV, "true")
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "src/a.q", "a:1\n")
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 0
    assert "::error" not in out
    assert "::warning title=KDB-X attestation overridden::" in out
    assert check.OVERRIDE_LABEL in summary and "src/a.q" in summary


def test_an_override_changes_nothing_on_an_attested_pr(repo, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(check.OVERRIDE_ENV, "true")
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 0 and "::warning" not in out
    assert "passed on exactly this tree" in summary


@pytest.mark.parametrize("value", ["", "false", "0"])
def test_no_override_label_still_blocks(repo, tmp_path, capsys, monkeypatch, value):
    monkeypatch.setenv(check.OVERRIDE_ENV, value)
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "src/a.q", "a:1\n")
    code, out, _ = _main(base, head, tmp_path, capsys)
    assert code == 1 and "::error title=KDB-X attestation::" in out


def _attestation_workflow() -> str:
    return (_ROOT / ".github/workflows/kdbx-attestation.yml").read_text()


def test_ci_runs_the_check_as_a_blocking_step():
    """The step's exit code is the script's: #1490 merged unattested on a
    "success" because the workflow caught the failure and only warned."""
    job = _attestation_workflow()
    assert "name: KDB-X attestation\n" in job
    assert "advisory" not in job.lower() and "::warning" not in job
    assert "continue-on-error" not in job
    run = 'python3 scripts/gates/check_kdbx_attestation.py "$BASE" "$HEAD" "$GITHUB_STEP_SUMMARY"'
    assert f"run: {run}\n" in job
    assert "kdbx-attestation:" not in (_ROOT / ".github/workflows/ci.yml").read_text()


def test_the_override_is_the_label_and_relabelling_reruns_the_check():
    """Adding or removing the label must re-judge the PR, or a label added
    after a red run changes nothing and one removed leaves a stale green."""
    job = _attestation_workflow()
    for event in ("labeled", "unlabeled", "synchronize", "opened", "reopened"):
        assert event in job
    labels = "github.event.pull_request.labels.*.name"
    want = f"{check.OVERRIDE_ENV}: ${{{{ contains({labels}, '{check.OVERRIDE_LABEL}') }}}}"
    assert want in job


def test_attest_refuses_a_dirty_tree(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    (repo / "README.md").write_text("changed\n")
    assert "uncommitted" in attest.refusal()


def test_attest_refuses_an_untracked_file(repo, monkeypatch):
    """The hooks run q over the working tree, so an untracked file they used
    would be missing from the tree the trailer names (#1340)."""
    monkeypatch.setattr(attest, "REPO", repo)
    (repo / "src").mkdir()
    (repo / "src" / "untracked_new.q").write_text("a:1\n")
    why = attest.refusal()
    assert why is not None
    assert "untracked files (src/" in why


def test_attest_allows_an_ignored_file(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    commit(repo, ".gitignore", "build/\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.q").write_text("a:1\n")
    fake = type("T", (), {"check_interpreter": staticmethod(lambda: "kdbx")})
    monkeypatch.setattr(attest, "_test_py", lambda: fake)
    assert attest.refusal() is None


def test_attest_refuses_an_interpreter_that_is_not_kdbx(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    fake = type("T", (), {"check_interpreter": staticmethod(lambda: "peachq")})
    monkeypatch.setattr(attest, "_test_py", lambda: fake)
    assert "not KDB-X" in attest.refusal()


def test_attest_records_the_tree_only_when_every_hook_passes(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    monkeypatch.setattr(attest, "refusal", lambda: None)
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    monkeypatch.setattr(attest, "run_hook", lambda hook: hook != "q-coverage")
    assert attest.main() == 1
    assert not check.report(base, sh(repo, "rev-parse", "HEAD"))[0]
    monkeypatch.setattr(attest, "run_hook", lambda hook: True)
    assert attest.main() == 0
    assert check.report(base, sh(repo, "rev-parse", "HEAD"))[0]
