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
impact = _load("test_impact", "scripts/dev/test_impact.py")


@pytest.fixture(autouse=True)
def _whole_q_suite(monkeypatch):
    """attest_kdbx.py runs the whole q suite unless a test says otherwise: the
    selector reads a real q tree with a graph-capable qlinter, and these
    throwaway repositories are neither. Its own tests are test_test_impact.py."""
    monkeypatch.setattr(attest, "q_tests_plan", lambda base, full: impact.Plan(None, "test"))


def recorded(env: dict | None, failures: tuple[str, ...] = ()) -> bool:
    """What tests/q/run_coverage.q does with attest's outcome variables: write
    every test it ran, and those that did not pass. True, as a passing hook."""
    if env and attest.FAILURES_ENV in env:
        Path(env[attest.RESULTS_ENV]).write_text(".qt.a\ttest_one\n.qt.b\ttest_two\n")
        Path(env[attest.FAILURES_ENV]).write_text("\n".join(failures) + "\n")
    return True


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


def attest_here(repo: Path, hooks: list[str] | None = None) -> str:
    """Record an attestation of HEAD's tree: for `hooks`, or - with None - the
    legacy trailer without `hooks=`, which ran every hook."""
    tree = sh(repo, "rev-parse", "HEAD^{tree}")
    trailer = f"KDB-X-gates: {tree}" + ("" if hooks is None else f" hooks={','.join(hooks)}")
    sh(repo, "commit", "-q", "--allow-empty", "-m", "attest", "-m", trailer)
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
        # q-docs runs the marked ```q blocks in docs/ (#1334).
        "docs/guides/new-pipeline.md",
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
    ok, why = check.report(base, head)
    assert ok and why.startswith("the KDB-X gates passed on exactly this tree")


def test_a_change_after_the_attestation_needs_a_new_one(repo):
    """The case that broke master: what merges is not what was run."""
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    attest_here(repo)
    head = commit(repo, "src/a.q", "a:2\n")
    assert not check.report(base, head)[0]


def _attested_pr_then_master(repo, *master_paths: str) -> tuple[str, str, str]:
    """An attested q PR forked from master, then `master_paths` committed on
    master after the fork: (fork point, master's tip, the PR's head)."""
    fork = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "-b", "pr")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    sh(repo, "checkout", "-q", "master")
    for path in master_paths:
        commit(repo, path, "m\n")
    return fork, sh(repo, "rev-parse", "HEAD"), head


def test_an_attested_head_behind_a_judged_change_on_its_base_is_refused(repo):
    """#1687: master changed a path a KDB-X hook reads, so the tree that
    merges is one no run has seen - refused, and the path named."""
    fork, base, head = _attested_pr_then_master(repo, "web/src/app.ts", "src/b.q")
    ok, why = check.report(base, head)
    assert not ok
    assert "does not contain the base's tip" in why and "src/b.q" in why
    assert "web/src/app.ts" not in why
    assert "Merge the base in" in why and "attest_kdbx.py" in why
    assert check.report(fork, head)[0]


def test_an_attested_head_behind_only_unjudged_changes_on_its_base_passes(repo):
    """#1687: master moved, but nothing any KDB-X hook judges - the merging
    tree's judged paths are the attested tree's, so no re-attestation."""
    _, base, head = _attested_pr_then_master(repo, "web/src/app.ts", "docs/guides/operations.md")
    assert not check.contains(head, base)
    ok, why = check.report(base, head)
    assert ok, why
    assert "the base has changed no path they judge" in why


def test_ci_refuses_a_head_behind_a_judged_change_and_names_it(repo, tmp_path, capsys):
    _, base, head = _attested_pr_then_master(repo, "python/uqs/x.py")
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 1 and "::error title=KDB-X attestation::" in out
    assert "python/uqs/x.py" in summary


def test_ci_passes_a_head_behind_only_unjudged_changes(repo, tmp_path, capsys):
    _, base, head = _attested_pr_then_master(repo, "web/src/app.ts")
    code, out, summary = _main(base, head, tmp_path, capsys)
    assert code == 0 and "::error" not in out
    assert "changed no path they judge" in summary


def test_a_head_behind_its_base_still_needs_its_own_attestation(repo):
    """Being behind an unjudged change excuses only the rebase, not the PR's
    own missing attestation."""
    sh(repo, "checkout", "-q", "-b", "pr")
    head = commit(repo, "src/a.q", "a:1\n")
    sh(repo, "checkout", "-q", "master")
    base = commit(repo, "web/src/app.ts", "m\n")
    ok, why = check.report(base, head)
    assert not ok and "no KDB-X attestation" in why


def test_an_unjudged_pr_behind_a_judged_change_needs_nothing(repo):
    """A PR touching no judged path is unchanged by how far behind it is."""
    sh(repo, "checkout", "-q", "-b", "pr")
    head = commit(repo, "web/src/app.ts", "export const y = 1;\n")
    sh(repo, "checkout", "-q", "master")
    base = commit(repo, "src/b.q", "b:1\n")
    assert check.report(base, head) == (
        True,
        "no path the KDB-X gates judge changed - no attestation needed",
    )


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
    monkeypatch.setattr(
        attest, "run_hook", lambda hook, env=None: hook != "q-coverage" and recorded(env)
    )
    assert attest.main(["--base", base]) == 1
    assert not check.report(base, sh(repo, "rev-parse", "HEAD"))[0]
    monkeypatch.setattr(attest, "run_hook", lambda hook, env=None: recorded(env))
    assert attest.main(["--base", base]) == 0
    assert check.report(base, sh(repo, "rev-parse", "HEAD"))[0]


# --- scoped attestation: only the hooks whose scope the change touches ------

ALL = sorted(attest.HOOKS)


@pytest.mark.parametrize(
    ("paths", "want"),
    [
        # Python alone: contract-surface reads every .py; nothing runs q tests.
        (["python/uqs/src/uqs/cli.py"], ["contract-surface"]),
        (["python/uqs/src/uqs/cli.py", "scripts/gates/check_x.py"], ["contract-surface"]),
        # ... and q-facts too where it touches what q-facts generates.
        (["python/uqs/src/uqs/generated/q_facts.py"], ["contract-surface", "q-facts"]),
        # q: every KDB-X hook.
        (["src/foundation/stats.q"], ALL),
        (["python/uqs/src/uqs/cli.py", "src/a.q"], ALL),
        # Docs: only a document with a q-example block, and only q-docs.
        (["docs/guides/operations.md", "README.md"], []),
        (["docs/guides/new-pipeline.md"], ["q-docs"]),
        # What q-coverage compares against, alone.
        (["tests/q/coverage_baseline.txt"], ["q-coverage"]),
        (["web/src/app.ts"], []),
    ],
)
def test_the_needed_hooks_are_those_whose_scope_the_change_touches(paths, want):
    assert check.needed_hooks(paths) == want


def test_a_python_only_pr_passes_on_an_attestation_of_contract_surface_alone(repo):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "python/uqs/src/uqs/x.py", "x = 1\n")
    head = attest_here(repo, ["contract-surface"])
    ok, why = check.report(base, head)
    assert ok, why
    assert "needed: contract-surface;" in why and "covers: contract-surface" in why


def test_a_docs_only_pr_without_q_example_blocks_needs_nothing(repo):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "docs/guides/operations.md", "# words\n")
    assert check.report(base, head) == (
        True,
        "no path the KDB-X gates judge changed - no attestation needed",
    )


def test_a_trailer_that_misses_a_needed_hook_is_refused(repo, tmp_path, capsys):
    """A q change attested for the cheap hooks only: the q suite never ran."""
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo, ["contract-surface", "q-facts"])
    ok, why = check.report(base, head)
    assert not ok
    assert "does not cover q-coverage, q-docs, q-tests" in why
    assert f"needed: {', '.join(ALL)}" in why and "covers: contract-surface, q-facts" in why
    code, out, _ = _main(base, head, tmp_path, capsys)
    assert code == 1 and "::error title=KDB-X attestation::" in out


def test_a_trailer_for_one_tree_does_not_cover_another(repo):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "python/uqs/src/uqs/x.py", "x = 1\n")
    attest_here(repo, ALL)
    head = commit(repo, "python/uqs/src/uqs/x.py", "x = 2\n")
    assert check.attested(head) == set()
    assert not check.report(base, head)[0]


def test_a_legacy_trailer_counts_as_every_hook(repo):
    """Written before attestations were scoped, by a run of every hook."""
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    head = attest_here(repo)
    assert check.attested(head) == set(attest.HOOKS)
    assert check.report(base, head)[0]


def test_attestations_of_one_tree_add_up(repo):
    """Two runs on the same tree, each of some hooks, attest their union."""
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    attest_here(repo, ["q-tests", "q-coverage"])
    head = attest_here(repo, ["contract-surface", "q-docs", "q-facts"])
    assert check.attested(head) == set(attest.HOOKS)
    assert check.report(base, head)[0]


def test_a_malformed_trailer_attests_nothing():
    tree = "a" * 40
    assert check.trailer_hooks(f"KDB-X-gates: {tree}x hooks=q-tests", tree) is None
    assert check.trailer_hooks(f"KDB-X-gates: {tree} q-tests", tree) is None
    assert check.trailer_hooks(f"KDB-X-gates: {tree} hooks=a,b", tree) == {"a", "b"}


def test_the_needed_set_is_computed_from_the_merge_base(repo):
    """A .q change master gained after the branch forked is not the branch's:
    once merged in, the branch still needs only what its own diff touches -
    and the q hooks' inputs are then master's tip's, which they passed on."""
    sh(repo, "checkout", "-q", "-b", "pr")
    commit(repo, "python/uqs/src/uqs/x.py", "x = 1\n")
    sh(repo, "checkout", "-q", "master")
    base = commit(repo, "src/b.q", "b:1\n")
    sh(repo, "checkout", "-q", "pr")
    assert check.needed_hooks(check.changed_paths(base, "pr")) == ["contract-surface"]
    sh(repo, "merge", "-q", "--no-edit", "master")
    head = attest_here(repo, ["contract-surface"])
    assert check.needed_hooks(check.changed_paths(base, head)) == ["contract-surface"]
    ok, why = check.report(base, head)
    assert ok, why


def _attest(repo, monkeypatch, *argv: str) -> tuple[int, list[str]]:
    """attest_kdbx.main() in `repo` with every hook passing: (code, hooks run)."""
    ran: list[str] = []
    monkeypatch.setattr(attest, "REPO", repo)
    monkeypatch.setattr(attest, "refusal", lambda: None)
    monkeypatch.setattr(
        attest, "run_hook", lambda hook, env=None: ran.append(hook) or recorded(env)
    )
    return attest.main(list(argv)), ran


def test_attest_runs_only_the_needed_hooks_and_names_them(repo, monkeypatch):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "python/uqs/src/uqs/x.py", "x = 1\n")
    code, ran = _attest(repo, monkeypatch, "--base", base)
    assert code == 0 and ran == ["contract-surface"]
    tree = sh(repo, "rev-parse", "HEAD^{tree}")
    assert f"KDB-X-gates: {tree} hooks=contract-surface" in sh(repo, "log", "-1", "--format=%B")
    assert check.report(base, sh(repo, "rev-parse", "HEAD"))[0]


def test_attest_all_runs_every_hook(repo, monkeypatch):
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "python/uqs/src/uqs/x.py", "x = 1\n")
    code, ran = _attest(repo, monkeypatch, "--all", "--base", base)
    assert code == 0 and ran == ALL
    assert check.attested(sh(repo, "rev-parse", "HEAD")) == set(attest.HOOKS)


def test_attest_records_nothing_when_nothing_is_needed(repo, monkeypatch, capsys):
    base = sh(repo, "rev-parse", "HEAD")
    head = commit(repo, "docs/guides/operations.md", "# words\n")
    code, ran = _attest(repo, monkeypatch, "--base", base)
    assert code == 0 and ran == []
    assert sh(repo, "rev-parse", "HEAD") == head
    assert "no attestation needed, nothing recorded" in capsys.readouterr().out


def test_attest_needs_what_the_check_needs(repo, monkeypatch):
    """One computation, so the script cannot attest less than CI asks for."""
    monkeypatch.setattr(attest, "REPO", repo)
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    head = commit(repo, "python/uqs/src/uqs/generated/q_facts.py", "x = 1\n")
    assert attest.needed(base) == check.needed_hooks(check.changed_paths(base, head)) == ALL


def test_ci_skips_every_kdbx_hook_where_it_has_no_q():
    """A KDB-X hook CI forgot to SKIP would fail every hosted run."""
    ci = (_ROOT / ".github/workflows/ci.yml").read_text()
    skip = next(line for line in ci.splitlines() if "SKIP=$SKIP," in line)
    named = skip.split("SKIP=$SKIP,")[1].split('"')[0].split(",")
    assert set(attest.HOOKS) <= set(named)


# --- q-tests ran a subset of the q suites (#1690 step 4) --------------------

SUBSET = ["test_a.q", "test_b.q"]


def test_a_trailer_says_what_q_tests_ran():
    tree = "a" * 40
    d = impact.digest(SUBSET)
    assert check.parse_trailer(f"KDB-X-gates: {tree} hooks=q-tests q-tests={d}", tree) == (
        {"q-tests"},
        d,
    )
    assert check.parse_trailer(f"KDB-X-gates: {tree} hooks=q-tests q-tests=all", tree)[1] == "all"
    # before subsets, and before scoping: the whole suite
    assert check.parse_trailer(f"KDB-X-gates: {tree} hooks=q-tests", tree)[1] == "all"
    assert check.parse_trailer(f"KDB-X-gates: {tree}", tree)[1] == "all"
    # a record that is neither is not an attestation of anything
    assert check.parse_trailer(f"KDB-X-gates: {tree} hooks=q-tests q-tests=abc", tree) is None
    assert check.parse_trailer(f"KDB-X-gates: {tree} hooks=q-tests other=1", tree) is None


def _attest_subset(repo, monkeypatch, files: list[str] | None) -> tuple[str, dict]:
    """attest_kdbx.main() over a .q change, its selection `files` (None: all):
    (the base, the env each hook was run with)."""
    envs: dict[str, dict | None] = {}
    monkeypatch.setattr(attest, "REPO", repo)
    monkeypatch.setattr(attest, "refusal", lambda: None)
    monkeypatch.setattr(
        attest, "run_hook", lambda hook, env=None: envs.update({hook: env}) or recorded(env)
    )
    monkeypatch.setattr(
        attest, "q_tests_plan", lambda base, full: impact.Plan(None if full else files, "test")
    )
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    assert attest.main(["--base", base]) == 0
    return base, envs


def test_attest_hands_its_selection_to_q_tests_and_records_its_digest(repo, monkeypatch):
    _, envs = _attest_subset(repo, monkeypatch, SUBSET)
    assert envs["q-tests"] == {
        impact.DECIDED_ENV: "-files test_a.q,test_b.q",
        attest.FROM_COVERAGE_ENV: "1",
    }
    assert set(envs["q-coverage"]) == {attest.RESULTS_ENV, attest.FAILURES_ENV}
    body = sh(repo, "log", "-1", "--format=%B")
    assert f"q-tests={impact.digest(SUBSET)}" in body
    assert "q-tests ran: test_a.q, test_b.q" in body


def test_attest_records_the_whole_suite_as_all(repo, monkeypatch):
    _, envs = _attest_subset(repo, monkeypatch, None)
    assert envs["q-tests"][impact.DECIDED_ENV] == "ALL"
    assert " q-tests=all" in sh(repo, "log", "-1", "--format=%B")


def test_a_subset_passes_when_the_check_selects_the_same_one(repo, monkeypatch):
    base, _ = _attest_subset(repo, monkeypatch, SUBSET)
    monkeypatch.setattr(check, "q_tests_plan", lambda b, h: impact.Plan(list(SUBSET), "here"))
    ok, why = check.report(base, sh(repo, "rev-parse", "HEAD"))
    assert ok, why


def test_a_subset_the_check_does_not_select_is_refused(repo, monkeypatch):
    base, _ = _attest_subset(repo, monkeypatch, SUBSET)
    monkeypatch.setattr(check, "q_tests_plan", lambda b, h: impact.Plan(["test_a.q"], "here"))
    ok, why = check.report(base, sh(repo, "rev-parse", "HEAD"))
    assert not ok
    assert "not the one selected here" in why and ".qlinter-version" in why
    assert "UQF_Q_TESTS_FULL=1" in why


def test_a_subset_where_the_check_selects_everything_is_refused(repo, monkeypatch):
    """Including where the check cannot run the selector: never trusted."""
    base, _ = _attest_subset(repo, monkeypatch, SUBSET)
    monkeypatch.setattr(
        check, "q_tests_plan", lambda b, h: impact.Plan(None, "the selector cannot run: x")
    )
    ok, why = check.report(base, sh(repo, "rev-parse", "HEAD"))
    assert not ok and "the whole suite (the selector cannot run: x)" in why


def test_the_whole_suite_needs_no_selection_to_verify(repo, monkeypatch):
    base, _ = _attest_subset(repo, monkeypatch, None)

    def refuse(b, h):
        raise AssertionError("q-tests=all needs no selector")

    monkeypatch.setattr(check, "q_tests_plan", refuse)
    ok, why = check.report(base, sh(repo, "rev-parse", "HEAD"))
    assert ok, why


def test_attest_all_runs_the_whole_q_suite(repo, monkeypatch):
    monkeypatch.setattr(attest, "REPO", repo)
    monkeypatch.setattr(attest, "refusal", lambda: None)
    envs: dict = {}
    monkeypatch.setattr(
        attest, "run_hook", lambda hook, env=None: envs.update({hook: env}) or recorded(env)
    )
    monkeypatch.setattr(
        attest, "q_tests_plan", lambda base, full: impact.Plan(None if full else SUBSET, "t")
    )
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    assert attest.main(["--all", "--base", base]) == 0
    assert envs["q-tests"][impact.DECIDED_ENV] == "ALL"


def test_the_real_check_selects_with_test_impact(repo, monkeypatch):
    """check.q_tests_plan is test_impact's plan_for over the PR's diff."""
    seen = {}
    fake = type(
        "M",
        (),
        {
            "changed_paths": staticmethod(lambda base, root, head: [f"{base}...{head}"]),
            "plan_for": staticmethod(lambda changed, root: seen.setdefault("c", changed)),
        },
    )
    monkeypatch.setattr(check, "_load", lambda rel: fake)
    check.q_tests_plan("B", "H")
    assert seen["c"] == ["B...H"]


def test_ci_installs_the_pinned_graph_qlinter_for_the_check():
    wf = _attestation_workflow()
    assert ".qlinter-version" in wf and "releases/download" in wf
    assert "UQF_QLINTER:" in wf and "analyze graph" in wf


def test_the_q_tests_hook_runs_the_selection():
    config = (_ROOT / ".pre-commit-config.yaml").read_text()
    hook = config.split("- id: q-tests", 1)[1].split("- id:", 1)[0]
    assert "scripts/dev/test_impact.py --hook" in hook
    assert "scripts/test.py q-unit -files" in hook


@pytest.mark.parametrize(
    "script", ["scripts/dev/attest_kdbx.py", "scripts/gates/check_kdbx_attestation.py"]
)
def test_the_selector_loads_from_a_fresh_interpreter(script):
    """As CI and attest_kdbx.py run it: nothing loaded test_impact beforehand
    (its @dataclass needs its module registered, which once failed here)."""
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', {str(_ROOT / script)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "loader = getattr(m, '_test_impact', None) or "
        "(lambda: m._load('scripts/dev/test_impact.py'))\n"
        "print(loader().Plan(['test_a.q'], 'x').line)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "-files test_a.q"


# --- one q run per attestation: q-tests' verdict from q-coverage (#1704) ----


def _attest_once(repo, monkeypatch, files, failures=(), record=True):
    """attest_kdbx.main() over a .q change whose q-coverage run records
    `failures` (or, without `record`, nothing): (exit code, hooks run in order,
    the env each got, the base)."""
    ran: list[str] = []
    envs: dict = {}

    def hook(name, env=None):
        ran.append(name)
        envs[name] = env
        return recorded(env, failures) if record else True

    monkeypatch.setattr(attest, "REPO", repo)
    monkeypatch.setattr(attest, "refusal", lambda: None)
    monkeypatch.setattr(attest, "run_hook", hook)
    monkeypatch.setattr(attest, "q_tests_plan", lambda base, full: impact.Plan(files, "test"))
    base = sh(repo, "rev-parse", "HEAD")
    commit(repo, "src/a.q", "a:1\n")
    return attest.main(["--base", base]), ran, envs, base


@pytest.mark.parametrize("files", [None, SUBSET, []])
def test_a_failure_the_coverage_run_saw_fails_q_tests(repo, monkeypatch, capsys, files):
    """Whatever q-tests selected: the coverage run ran every test, and a test
    that did not pass there is a q-tests failure - recorded nowhere."""
    code, ran, _, base = _attest_once(
        repo, monkeypatch, files, failures=(".qt.b.test_two\tfail\texpected 1 got 2",)
    )
    assert code == 1
    assert "q-tests" not in ran and "q-coverage" in ran
    err = capsys.readouterr().err
    assert "q-tests failed" in err and ".qt.b.test_two" in err and "nothing recorded" in err
    assert check.TRAILER not in sh(repo, "log", "-1", "--format=%B")
    assert not check.report(base, sh(repo, "rev-parse", "HEAD"))[0]


def test_a_coverage_run_that_recorded_nothing_is_no_verdict(repo, monkeypatch, capsys):
    code, ran, _, _ = _attest_once(repo, monkeypatch, None, record=False)
    assert code == 1 and "q-tests" not in ran
    assert "recorded no test outcomes" in capsys.readouterr().err


def test_a_coverage_run_that_ran_no_test_is_no_verdict(tmp_path):
    env = attest.outcome_env(tmp_path)
    Path(env[attest.RESULTS_ENV]).write_text("")
    Path(env[attest.FAILURES_ENV]).write_text("\n")
    assert attest.unit_verdict(tmp_path) == "the q-coverage run ran no test"


@pytest.mark.parametrize("files", [None, SUBSET])
def test_a_clean_run_passes_both_with_the_suite_run_once(repo, monkeypatch, files):
    code, ran, envs, base = _attest_once(repo, monkeypatch, files)
    assert code == 0
    # q-coverage first, q-tests told its q-unit verdict is taken
    assert ran.index("q-coverage") < ran.index("q-tests")
    assert envs["q-tests"][attest.FROM_COVERAGE_ENV] == "1"
    body = sh(repo, "log", "-1", "--format=%B")
    assert "q-unit verdict: the q-coverage run of the whole suite" in body
    if files is not None:
        monkeypatch.setattr(check, "q_tests_plan", lambda b, h: impact.Plan(list(files), "here"))
    ok, why = check.report(base, sh(repo, "rev-parse", "HEAD"))
    assert ok, why


@pytest.mark.parametrize(("files", "record"), [(None, "all"), (SUBSET, impact.digest(SUBSET))])
def test_the_trailer_is_the_one_1703_defines(repo, monkeypatch, files, record):
    """One run instead of two changes what ran, not what is recorded: the
    trailer is byte for byte the one #1703 wrote, so CI's check is unchanged."""
    assert _attest_once(repo, monkeypatch, files)[0] == 0
    tree = sh(repo, "rev-parse", "HEAD^{tree}")
    trailer = sh(repo, "log", "-1", f"--format=%(trailers:key={check.TRAILER},valueonly)").strip()
    assert trailer == f"{tree} hooks={','.join(ALL)} q-tests={record}"


def test_q_tests_without_q_coverage_runs_its_own_q_unit(repo, monkeypatch):
    """Not a case the scopes produce (q-coverage's takes every .q q-tests'
    does), but if it were: no coverage run, so q-tests runs the suite."""
    monkeypatch.setattr(attest, "needed", lambda base: ["q-tests"])
    code, ran, envs, _ = _attest_once(repo, monkeypatch, SUBSET)
    assert code == 0 and ran == ["q-tests"]
    assert envs["q-tests"] == {impact.DECIDED_ENV: "-files test_a.q,test_b.q"}


def test_q_coverage_runs_before_q_tests():
    assert attest.run_order(["q-tests", "q-coverage", "q-docs"]) == [
        "q-coverage",
        "q-docs",
        "q-tests",
    ]
    assert attest.run_order(ALL)[-1] == "q-tests"


def test_the_q_tests_hook_skips_q_unit_only_when_attest_says_so():
    config = (_ROOT / ".pre-commit-config.yaml").read_text()
    hook = config.split("- id: q-tests", 1)[1].split("- id:", 1)[0]
    guard = f'if [ "${{{attest.FROM_COVERAGE_ENV}:-}}" = 1 ]; then'
    assert guard in hook
    skipped, rest = hook.split(guard, 1)[1].split("else", 1)
    assert "scripts/test.py" not in skipped  # nothing runs in its place
    assert "scripts/test.py q-unit" in rest.split("fi\n", 1)[0]
    # q-examples and q-scripts run either way
    tail = rest.split("fi\n", 1)[1]
    assert "scripts/test.py q-examples" in tail and "scripts/test.py q-scripts" in tail


def test_the_coverage_run_records_outcomes_and_seeds_as_the_plain_run_does():
    """Its pass stands for run_tests.q's, so it runs on the same random draws
    and writes outcomes through the same writer."""
    cov = (_ROOT / "tests/q/run_coverage.q").read_text().splitlines()
    plain = (_ROOT / "tests/run_tests.q").read_text().splitlines()
    for directive in ("\\S ", "\\c "):
        assert [ln for ln in cov if ln.startswith(directive)] == [
            ln for ln in plain if ln.startswith(directive)
        ]
    assert any(".qt.support.write_outcomes res" in ln for ln in cov)
    assert any(".qt.support.write_outcomes res" in ln for ln in plain)
