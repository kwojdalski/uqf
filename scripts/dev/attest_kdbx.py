#!/usr/bin/env python3
"""Run the KDB-X gates this branch needs and record that they passed (#1014).

Hosted CI cannot run KDB-X, so whether a pull request passed the KDB-X hooks
(HOOKS below, by their ids in .pre-commit-config.yaml so their definitions
stay the one source) is known only on the machine that ran them.

This runs the hooks the branch NEEDS: those whose `files:` scope (less
`exclude:`), read from the config by scripts/gates/check_kdbx_attestation.py,
takes a path changed in `origin/master...HEAD` - the three-dot diff, from the
merge base. Each one runs over the WHOLE tree, not just the files a commit
staged, and when all pass the tree is recorded as an empty commit carrying

    KDB-X-gates: <the tree's sha> hooks=<the hooks run, comma-sorted>
        q-tests=<what ran> q-coverage=<what ran>

as one line, which the check reads in CI and holds to the same needed set. `--all` runs
every hook, and the whole q suite, as this did before it was scoped. A branch
that needs none records nothing, and says so.

q-tests runs only the q suites the change reaches (#1690): the selection
scripts/dev/test_impact.py makes for `base...HEAD` from q-lint's dependency
graph, handed to the hook in UQF_Q_TESTS_SELECTION so the hook runs exactly
what is recorded. `q-tests=<sha256>` records that subset by its digest
(test_impact.digest: the sorted file names), and CI's check, with the qlinter
.qlinter-version pins, makes the same selection from the PR's diff and
accepts the trailer only when the digests agree. `q-tests=all` records the
whole suite, which needs no recomputation: what ran when the selector said
ALL, could not run here (no graph-capable qlinter), or UQF_Q_TESTS_FULL=1 was
set - the way to an attestation CI accepts whatever its selector says. The
full suite still runs on master, and a test only a full run would have caught
is caught there.

The q suite runs ONCE (#1704). q-coverage runs every test under
instrumentation anyway, so when both hooks are needed it runs first, writing
its per-test outcomes (tests/lib/support.q's write_outcomes), and q-tests'
q-unit verdict is read from them: any test that did not pass there - inside
the selection or not - fails q-tests, and a run that recorded no outcome is
no verdict. The q-tests hook is then told (UQF_Q_UNIT_FROM_COVERAGE=1) to run
only the documented and worked examples (tests/qrun/test_documented_examples.py,
test_worked_examples.py).
The trailer is unchanged: q-tests=<digest>
still names the selection, and every suite in it ran and passed - in the
whole-suite process, as on master, rather than alone. That stands for a
plain run because run_coverage.q is seeded as run_tests.q is, and on KDB-X
the two gave every test the same outcome.

q-coverage runs only what the change reaches too (#1719): q-tests' suites,
plus, for each function it judges, a suite file that enters it according to
tests/q/coverage_map.txt, instrumenting and judging only the functions
defined in the production files the change reaches
(test_impact.coverage_for) - or the whole suite, judging every function, when
q-tests runs the whole suite, when no suite or no production file is reached,
or when the change touches the coverage tool, its driver or its baseline. A
subset that finds a judged function never entered is no verdict:
tests/qrun/test_q_coverage.py then runs the whole suite and its verdict stands,
so a function only a suite outside the subset enters can never fail a change.
`q-coverage=<sha256>` records the subset (test_impact.coverage_digest: its
suite files and its modules) and CI's check recomputes it from the same map;
`q-coverage=all` records a whole run, including one a subset handed its
verdict to. The whole suite's coverage, judging every function, runs on
master's tip: scripts/dev/master_coverage.py, which scripts/dev/ship.sh
starts after each merge; this prints its last verdict.

Why skipping a hook the change does not touch still means "every KDB-X hook
passed on the judged paths that merge": the check accepts a head only if it
contains the base's tip or the base changed no judged path since the merge
base (#1687). Either way the merge base's judged files are master's tip's,
and a hook whose scope the diff does not touch reads on the head exactly the
files it read on master's tip. That tip's result for the hook is the one the
last PR to touch its scope attested on its own exact tree, and every merge
since left those files as they were. It rests on the same assumption the
check always made for paths outside every scope: a hook's result depends only
on the files its scope takes - which is why a scope must be complete, and why
q-docs' scope is held to the documents with q-example blocks by
scripts/gates/check_hook_scopes.py.

Run it last, after the final rebase or merge of master: either changes the
tree and the attestation with it. A branch need not merge master in just to
be current - only when master has changed a judged path since the branch
forked, which is when the check would refuse it.

Refuses a dirty tree, untracked files included since the hooks see them (what
is tested must be what is committed), and any interpreter that does not
identify as KDB-X.

    uv run python scripts/dev/attest_kdbx.py [--all] [--base REF]
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: Where this script's sibling modules are loaded from - REPO's value at
#: import, kept apart so a test that points REPO at a scratch repository
#: still loads the real check.
_SOURCE = REPO

#: The hooks that need a q, which hosted CI skips - exactly the ones ci.yml's
#: no-q branch adds to SKIP, which test_check_kdbx_attestation.py holds equal
#: in both directions: one skipped there and missing here ran nowhere that was
#: checked (plant-schema, #1794).
HOOKS = ("q-tests", "q-coverage", "q-docs", "contract-surface", "q-facts", "plant-schema")

#: The pre-commit CI runs the hooks with.
PRE_COMMIT = "pre-commit==4.6.2"

TRAILER = "KDB-X-gates"

#: What the branch is compared with, as CI compares a PR with its base.
BASE = "origin/master"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _SOURCE / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # registered before it runs: a @dataclass looks its own module up there
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def interpreter() -> str | None:
    """Which q $QCMD (else q on PATH) is, as the binary answers - `kdbx` or
    `peachq` - or None when there is no runnable q (uqs.interpreter, the rule
    pytest's `kdbx` fixture applies). A variable #1753 removed is refused."""
    from uqs.interpreter import identified, q_interpreter, refuse_removed

    refuse_removed()
    q = q_interpreter()
    return None if q is None else identified(q)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def _test_impact():
    return _load("test_impact", "scripts/dev/test_impact.py")


def q_tests_plan(base: str, full: bool):
    """What q-tests runs: the whole suite with `full`, else test_impact's plan
    for `base...HEAD` - which falls back to the whole suite itself."""
    impact = _test_impact()
    if full:
        return impact.Plan(None, "--all")
    return impact.plan(base, REPO, with_uncommitted=False)


def q_coverage_plan(base: str, plan):
    """What q-coverage runs, given q-tests' `plan` for `base...HEAD`:
    test_impact.coverage_for - q-tests' suites, judging the production files
    the change reaches, or the whole suite."""
    impact = _test_impact()
    if plan.files is None:
        return impact.CoveragePlan(None, None, f"q-tests runs the whole suite ({plan.why})")
    return impact.coverage_for(plan, impact.changed_paths(base, REPO), REPO)


#: Where tests/qrun/test_q_coverage.py writes what decided it - `subset` or `all`
#: (a subset that found something hands its verdict to the whole suite).
COVERAGE_RAN_ENV = "UQF_Q_COVERAGE_RAN"


def master_coverage_status() -> str:
    """One line on the whole-suite coverage run on master's tip, which a
    subset leaves the rest of the tree to (scripts/dev/master_coverage.py).
    Informational: it never stops an attestation."""
    spec = importlib.util.spec_from_file_location(
        "master_coverage", Path(__file__).resolve().parent / "master_coverage.py"
    )
    try:
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.status_line(REPO)
    except Exception as exc:  # noqa: BLE001 - a status line must not fail the run
        return f"cannot read the whole-suite coverage run's last verdict: {exc}"


def coverage_ran(path: Path) -> str | None:
    """`subset` or `all`, as the q-coverage lane wrote it at `path`, or None."""
    try:
        ran = path.read_text().strip()
    except OSError:
        return None
    return ran if ran in ("subset", "all") else None


#: tests/run_tests.q's outcome files (tests/lib/support.q writes both), which
#: tests/q/run_coverage.q writes too: every test the run ran, and those that
#: did not pass.
RESULTS_ENV = "UQF_TEST_RESULTS"
FAILURES_ENV = "UQF_TEST_FAILURES"

#: Set for the q-tests hook when its q-unit verdict has already been taken from
#: the q-coverage run (#1704): the hook then runs the documented and worked examples only.
FROM_COVERAGE_ENV = "UQF_Q_UNIT_FROM_COVERAGE"


def run_order(hooks: list[str]) -> list[str]:
    """`hooks` in the order they run: q-coverage before q-tests, whose q-unit
    verdict it supplies; the rest as given."""
    late = [h for h in hooks if h == "q-tests"]
    return [h for h in hooks if h != "q-tests"] + late


def outcome_env(outcomes: Path) -> dict[str, str]:
    """What makes the coverage run record its tests in `outcomes`."""
    return {
        RESULTS_ENV: str(outcomes / "results.tsv"),
        FAILURES_ENV: str(outcomes / "failures.tsv"),
    }


def unit_verdict(outcomes: Path) -> str | None:
    """Why the q suite, as the q-coverage run ran it, did not pass - or None.

    One instrumented run of the whole suite stands for q-tests' q-unit run
    (#1704): what q-tests selects is a subset of it, so a test that failed
    there fails q-tests, whether or not the selection names its suite. A run
    that recorded nothing - no outcome files, or no test - is no verdict, and
    is refused rather than read as a pass."""
    env = outcome_env(outcomes)
    results, failures = Path(env[RESULTS_ENV]), Path(env[FAILURES_ENV])
    if not results.is_file() or not failures.is_file():
        return "the q-coverage run recorded no test outcomes"
    if not [line for line in results.read_text().splitlines() if line.strip()]:
        return "the q-coverage run ran no test"
    bad = [line for line in failures.read_text().splitlines() if line.strip()]
    if not bad:
        return None
    shown = "\n  ".join(bad[:20]) + (f"\n  and {len(bad) - 20} more" if len(bad) > 20 else "")
    return f"{len(bad)} test(s) did not pass in the q-coverage run:\n  {shown}"


def needed(base: str) -> list[str]:
    """The hooks whose scope takes a path changed in `base...HEAD`, sorted -
    worked out by the check CI runs, so the two cannot disagree."""
    check = _load("check_kdbx_attestation", "scripts/gates/check_kdbx_attestation.py")
    changed = git("diff", "--name-only", f"{base}...HEAD").split()
    return check.needed_hooks(changed)


def run_hook(hook: str, env: dict[str, str] | None = None) -> bool:
    """Run one pre-commit hook over the whole tree, with `env` added to this
    process's environment; whether it passed."""
    cmd = ["uv", "tool", "run", "--from", PRE_COMMIT, "pre-commit", "run", hook, "--all-files"]
    merged = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=REPO, env=merged, check=False).returncode == 0


def dirt() -> str | None:
    """What makes the working tree differ from HEAD's tree, or None.

    Untracked files count. The hooks run q over the WORKING TREE, so a new
    `src/foo.q` that a tracked file loads but nobody `git add`ed passes them
    here and is absent from the tree the trailer names - which then fails
    wherever it is checked out (#1340). Ignored files do not count:
    `git status --porcelain` leaves them out, so build output and the venv
    are no obstacle.
    """
    lines = git("status", "--porcelain").splitlines()
    untracked = [line[3:] for line in lines if line.startswith("??")]
    if len(untracked) < len(lines):
        return "uncommitted changes - commit them, so what is tested is what is pushed"
    if untracked:
        shown = ", ".join(untracked[:5]) + (
            f" and {len(untracked) - 5} more" if len(untracked) > 5 else ""
        )
        return (
            f"untracked files ({shown}) - add or remove them, so what is tested is "
            "what is pushed: the hooks see them, the attested tree would not"
        )
    return None


def refusal() -> str | None:
    """Why this tree cannot be attested here, or None."""
    why = dirt()
    if why:
        return why
    try:
        impl = interpreter()
    except Exception as exc:  # noqa: BLE001 - a refusal names what was wrong
        return f"cannot tell which q this is: {exc}"
    if impl is None:
        return "no q interpreter ($QCMD, else q on PATH)"
    if impl != "kdbx":
        return f"the interpreter is {impl}, not KDB-X - only a KDB-X run attests"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--all", action="store_true", help="run every KDB-X hook")
    parser.add_argument("--base", default=BASE, help=f"compare with this (default {BASE})")
    args = parser.parse_args(argv)

    why = dirt()
    if why:
        print(f"attest_kdbx: refused: {why}", file=sys.stderr)
        return 1
    hooks = sorted(HOOKS) if args.all else needed(args.base)
    if not hooks:
        print(
            f"attest_kdbx: nothing in {args.base}...HEAD is in a KDB-X hook's scope - "
            "no attestation needed, nothing recorded"
        )
        return 0
    why = refusal()
    if why:
        print(f"attest_kdbx: refused: {why}", file=sys.stderr)
        return 1
    tree = git("rev-parse", "HEAD^{tree}").strip()
    print(f"attest_kdbx: needed: {', '.join(hooks)}", flush=True)
    cov = None
    cov_ran = "all"
    envs: dict[str, dict[str, str]] = {}
    needs_plan = "q-tests" in hooks or "q-coverage" in hooks
    plan = q_tests_plan(args.base, args.all) if needs_plan else None
    impact = _test_impact()
    if "q-tests" in hooks and plan is not None:
        envs["q-tests"] = {impact.DECIDED_ENV: plan.line}
        print(f"attest_kdbx: q-tests: {plan.why}", flush=True)
    one_run = "q-tests" in hooks and "q-coverage" in hooks
    with tempfile.TemporaryDirectory(prefix="attest_kdbx.") as tmp:
        outcomes = Path(tmp)
        if "q-coverage" in hooks:
            cov = q_coverage_plan(args.base, plan)
            envs["q-coverage"] = {
                impact.COVERAGE_DECIDED_ENV: cov.line,
                COVERAGE_RAN_ENV: str(outcomes / "coverage_ran"),
            }
            print(f"attest_kdbx: q-coverage: {cov.why}", flush=True)
            if cov.files is not None:
                print(f"attest_kdbx: {master_coverage_status()}", flush=True)
        if one_run:
            envs["q-coverage"].update(outcome_env(outcomes))
            envs["q-tests"][FROM_COVERAGE_ENV] = "1"
            print("attest_kdbx: q-tests: its q-unit verdict is the q-coverage run's", flush=True)
        for hook in run_order(hooks):
            print(f"attest_kdbx: {hook} ...", flush=True)
            started = time.monotonic()
            passed = run_hook(hook, envs.get(hook))
            print(f"attest_kdbx: {hook}: {time.monotonic() - started:.0f}s", flush=True)
            if not passed:
                print(f"attest_kdbx: {hook} failed - nothing recorded", file=sys.stderr)
                return 1
            if hook == "q-coverage" and one_run:
                why = unit_verdict(outcomes)
                if why:
                    print(f"attest_kdbx: q-tests failed: {why} - nothing recorded", file=sys.stderr)
                    return 1
            if hook == "q-coverage":
                ran_ = coverage_ran(outcomes / "coverage_ran")
                if ran_ is None:
                    print(
                        "attest_kdbx: q-coverage passed but did not say what it ran "
                        f"(${COVERAGE_RAN_ENV}) - nothing recorded",
                        file=sys.stderr,
                    )
                    return 1
                cov_ran = ran_
                if cov_ran == "all" and cov is not None and cov.files is not None:
                    print(
                        "attest_kdbx: q-coverage: the subset found something, and the "
                        "whole suite decided",
                        flush=True,
                    )
    if git("rev-parse", "HEAD^{tree}").strip() != tree or dirt():
        print(
            "attest_kdbx: a hook changed the tree - commit that, then run this again",
            file=sys.stderr,
        )
        return 1
    why_these = "every KDB-X hook (--all)" if args.all else f"the hooks {args.base}...HEAD needs"
    trailer = f"{TRAILER}: {tree} hooks={','.join(hooks)}"
    ran = ""
    if "q-tests" in hooks and plan is not None:
        trailer += f" q-tests={plan.record}"
        ran = f"\n\nq-tests: {plan.why}" + (
            f"\nq-tests ran: {', '.join(plan.files) or 'none'}" if plan.files is not None else ""
        )
    if cov is not None:
        subset = cov_ran == "subset"
        trailer += f" q-coverage={cov.record if subset else 'all'}"
        ran += f"\n\nq-coverage: {cov.why}" + (
            f"\nq-coverage ran: {', '.join(cov.files)}"
            + (f"\nq-coverage added from the map: {', '.join(cov.added)}" if cov.added else "")
            + f"\nq-coverage judged: {', '.join(cov.modules)}"
            if subset
            else (
                "\nq-coverage: the subset found something; the whole suite decided"
                if cov.files is not None
                else ""
            )
        )
        if one_run:
            ran += "\nq-tests' q-unit verdict: the q-coverage run of " + (
                "the suites q-tests selected" if subset else "the whole suite"
            )
    try:
        git(
            "commit",
            "--allow-empty",
            "-m",
            f"KDB-X gates pass on tree {tree[:12]}",
            "-m",
            f"{', '.join(hooks)} - {why_these} - run on KDB-X over the whole tree."
            f"{ran}\n\n{trailer}",
        )
    except subprocess.CalledProcessError as exc:
        # The commit runs the repository's hooks too; one that refuses (a
        # qlinter off its pin, say) is named here rather than as a traceback.
        print(
            f"attest_kdbx: the gates passed but the commit was refused:\n{exc.stdout}{exc.stderr}",
            file=sys.stderr,
        )
        return 1
    print(f"attest_kdbx: recorded {trailer} - push to show it on the PR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
