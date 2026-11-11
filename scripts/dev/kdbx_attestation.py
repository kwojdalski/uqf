#!/usr/bin/env python3
"""Record that a branch passed the KDB-X gates, and check that a PR did (#1014).

One protocol, one command (#2706): `attest` runs the KDB-X hooks this branch
needs on a contributor's machine and records the `KDB-X-gates:` trailer;
`check` reads it in CI, with no q, and holds it to the same needed set.

    uv run python scripts/dev/kdbx_attestation.py attest [--all] [--base REF]
    uv run --no-project python scripts/dev/kdbx_attestation.py check BASE HEAD [SUMMARY]

`check` needs only the standard library (CI runs it with `--no-project`):
everything this module imports at load is stdlib, and uqs is imported only
inside `attest`'s interpreter check.

ATTEST

Hosted CI cannot run KDB-X, so whether a pull request passed the KDB-X hooks
(HOOKS below, by their ids in .pre-commit-config.yaml so their definitions
stay the one source) is known only on the machine that ran them.

This runs the hooks the branch NEEDS: those whose `files:` scope (less
`exclude:`), read from the config by `check`,
takes a path changed in `origin/master...HEAD` - the three-dot diff, from the
merge base. Each one runs over the WHOLE tree, not just the files a commit
staged, and when all pass the tree is recorded as an empty commit carrying

    KDB-X-gates: <the tree's sha> hooks=<the hooks run, comma-sorted>
        q-tests=<what ran> q-coverage=<what ran>

as one line, which the check reads in CI and holds to the same needed set. `--all` runs
every hook, and the whole q suite, as this did before it was scoped. A branch
that needs none records nothing, and says so.

q-tests runs only the q suites the change reaches (#1690): the selection
scripts/dev/q_test_impact.py makes for `base...HEAD` from q-lint's dependency
graph, handed to the hook in UQF_Q_TESTS_SELECTION so the hook runs exactly
what is recorded. `q-tests=<sha256>` records that subset by its digest
(q_test_impact.digest: the sorted file names), and CI's check, with the qlinter
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
(q_test_impact.coverage_for) - or the whole suite, judging every function, when
q-tests runs the whole suite, when no suite or no production file is reached,
or when the change touches the coverage tool, its driver or its baseline. A
subset that finds a judged function never entered is no verdict:
tests/qrun/test_q_coverage.py then runs the whole suite and its verdict stands,
so a function only a suite outside the subset enters can never fail a change.
`q-coverage=<sha256>` records the subset (q_test_impact.coverage_digest: its
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
`scripts/gates/check.py hook-scopes`.

Run it last, after the final rebase or merge of master: either changes the
tree and the attestation with it. A branch need not merge master in just to
be current - only when master has changed a judged path since the branch
forked, which is when the check would refuse it.

Refuses a dirty tree, untracked files included since the hooks see them (what
is tested must be what is committed), and any interpreter that does not
identify as KDB-X.

CHECK

Hosted CI has no KDB-X licence, so the hooks `attest`
names (the ones .pre-commit-config.yaml tags `# kdbx-only`) run only on a
contributor's machine - and master needed a repair commit for them on almost
every day of a fortnight: a change merged on a
green CI that no KDB-X run had seen, and the next KDB-X session paid for it
inside an unrelated diff.

This binds a merge to a KDB-X run without needing q here. A KDB-X session
attests a tree with `attest`, which runs the hooks the
branch's change needs and records the result as an empty commit carrying

    KDB-X-gates: <the tree's sha> hooks=<the hooks it ran, comma-sorted>

and this check, given a PR's base and head, works out the same needed set -
each hook whose `files:` scope (less `exclude:`) takes a path changed since
the merge base - and says whether the head's exact tree carries trailers
covering it. A trailer without `hooks=`, written before attestations were
scoped, ran every hook and covers all of them. A rebase changes the tree and
needs a fresh attestation - which is the case that broke master.

A head that does not contain the base's tip (#1687) is judged by what the base
did since the merge base. If those commits touch no path any KDB-X hook judges
- the same `judged_scopes()` - the head passes on its attestation: the tree
that merges then has exactly the attested tree's judged paths, since nothing
the hooks read changed on the base. So the guarantee is "the merging tree's
judged paths equal an attested tree's", not "the merging tree was attested",
and the gap between the two is a gate whose result depends on a path outside
its scope. That is a hook-scope bug, which `check.py hook-scopes`
exists to catch. If the base side did touch a judged path, the head is
refused and the path named: merge the base in and attest again.

q-tests may have run a SUBSET of the q suites (#1690): those
scripts/dev/q_test_impact.py selects from q-lint's dependency graph for the
branch's change. The trailer then says `q-tests=<sha256>`, the digest of the
sorted file names (q_test_impact.digest), and this check makes the same
selection from the PR's diff and the head's tree - with the qlinter
.qlinter-version pins, which the workflow installs - and accepts the subset
only when the digests agree. So it depends on nothing but the diff, the tree
and that pinned binary; an attestation built with another graph binary that
selected differently is refused, never trusted. `q-tests=all`, and a trailer
from before subsets (no `q-tests=`), ran the whole suite and need no
selection. What the subset gives up is a test outside what the graph sees the
change reach: the full suite still runs on master, and catches it there.

q-coverage may have run a subset too (#1719): q-tests' suites and, per
tests/q/coverage_map.txt, a suite entering each function it judges,
instrumenting and judging only the functions of the production files the
change reaches. `q-coverage=<sha256>` (q_test_impact.coverage_digest, over those
suites and modules) is verified the same way, from q_test_impact.coverage_for
over this checkout - the head's tree, so the head's map; `all`, and a trailer
without the field, ran the whole suite. A subset that found a function never
entered handed its verdict to a whole run, which then records `all`.

It BLOCKS (#1543): CI fails a PR whose judged paths changed and whose head
tree is not attested. It judges PRs only - a push to master is not re-judged,
since failing a merge that already happened would block nothing.

CI passes its step summary as SUMMARY: the answer is appended there, and a
refusal is also printed as an error annotation for the PR.

The one way past a refusal is the PR label `kdbx-attestation-override`, for a
change the gates' scopes take but no KDB-X run can judge (a docs-only edit, a
contributor without KDB-X). CI sets KDBX_ATTESTATION_OVERRIDE=true when the
label is on the PR; a refusal then passes, but as a warning annotation and a
summary line naming the label and the paths, so the override is on the record
rather than a silent green. It changes nothing for an attested tree.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import os
import re
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


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _SOURCE / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # registered before it runs: a @dataclass looks its own module up there
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _hook_scopes():
    """The hook-scopes rule (scripts/gates/policy/hook_scopes.py), which reads
    the hook config."""
    return _load("hook_scopes", "scripts/gates/policy/hook_scopes.py")


#: The hooks that need a q, which hosted CI skips: the ones .pre-commit-config.yaml
#: tags `# kdbx-only` under their `- id:` - the one place the set is written
#: (#2170). ci.yml's no-q branch adds the same list to SKIP by asking
#: `scripts/gates/check.py hook-scopes --kdbx-only`, so a hook is added in one
#: place; test_kdbx_attestation.py holds that and the config together.
HOOKS = tuple(_hook_scopes().kdbx_only_hooks((REPO / ".pre-commit-config.yaml").read_text()))

#: The pre-commit CI runs the hooks with: ci.yml's, held equal to it by
#: `scripts/gates/check.py tool-pins`.
PRE_COMMIT = "pre-commit==4.6.2"

TRAILER = "KDB-X-gates"

#: What the branch is compared with, as CI compares a PR with its base.
BASE = "origin/master"

#: How a contributor runs the attestation, as every refusal names it.
ATTEST = "uv run python scripts/dev/kdbx_attestation.py attest"

#: The PR label that lets an unattested tree pass, and the variable CI sets
#: from it - "true" when the label is on the PR.
OVERRIDE_LABEL = "kdbx-attestation-override"
OVERRIDE_ENV = "KDBX_ATTESTATION_OVERRIDE"


def interpreter() -> str | None:
    """Which q $QCMD (else q on PATH) is, as the binary answers - `kdbx` or
    `peachq` - or None when there is no runnable q (uqs.interpreter, the rule
    pytest's `kdbx` fixture applies). A variable #1753 removed is refused."""
    from uqs.interpreter import identified, q_interpreter, refuse_removed

    refuse_removed()
    q = q_interpreter()
    return None if q is None else identified(q)


_git = _load("_git", "scripts/dev/_git.py")


def _q_test_impact():
    return _load("q_test_impact", "scripts/dev/q_test_impact.py")


def q_tests_plan(base: str, full: bool):
    """What q-tests runs: the whole suite with `full`, else q_test_impact's plan
    for `base...HEAD` - which falls back to the whole suite itself."""
    impact = _q_test_impact()
    if full:
        return impact.Plan(None, "--all")
    return impact.plan(base, REPO, with_uncommitted=False)


def q_coverage_plan(base: str, plan):
    """What q-coverage runs, given q-tests' `plan` for `base...HEAD`:
    q_test_impact.coverage_for - q-tests' suites, judging the production files
    the change reaches, or the whole suite."""
    impact = _q_test_impact()
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
    worked out as `check` works it out in CI, so the two cannot disagree."""
    return needed_hooks(changed_paths(base, "HEAD", cwd=REPO))


def hook_stage(hook: str, config: Path | None = None) -> str:
    """The stage `hook` runs at, read from its `stages:` in the config -
    `pre-commit` when it names none. q-tests and q-coverage run at pre-push
    (#1965), and `pre-commit run <id>` finds a hook only at the stage asked."""
    text = (config or REPO / ".pre-commit-config.yaml").read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"- id: {hook}":
            for following in lines[i + 1 :]:
                if following.strip().startswith("- id:") or following.startswith("  - repo:"):
                    break
                if following.strip().startswith("stages:"):
                    return following.split("[", 1)[1].split(",")[0].strip(" ]")
            break
    return "pre-commit"


def run_hook(hook: str, env: dict[str, str] | None = None) -> bool:
    """Run one pre-commit hook over the whole tree, at its own stage, with
    `env` added to this process's environment; whether it passed."""
    cmd = ["uv", "tool", "run", "--from", PRE_COMMIT, "pre-commit", "run", hook, "--all-files",
           "--hook-stage", hook_stage(hook)]  # fmt: skip
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
    lines = _git.git("status", "--porcelain", cwd=REPO).stdout.splitlines()
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


# ------------------------------------------------------------------ check


Scopes = dict[str, tuple[re.Pattern, re.Pattern | None]]


def judged_scopes(config_text: str | None = None) -> Scopes:
    """The (files, exclude) scope of each hook `attest` runs, by hook id.

    Read from .pre-commit-config.yaml, the way `check.py hook-scopes` reads
    it, for the hooks in HOOKS - not kept here as a second list. A
    hand-kept copy had already drifted narrower than the hooks: q-tests ran
    the ```q blocks in docs/ (`.md`), contract-surface reads Python and
    scripts/processes/, and a change to only those was reported as needing no
    attestation (#1334). A hook with no `files:` would match every path, and
    one that is not in the config at all is a rename this must not paper
    over, so both stop here rather than guess.
    """
    if config_text is None:
        config_text = (_SOURCE / ".pre-commit-config.yaml").read_text()
    scoped = {
        hook: (files, exclude) for hook, files, exclude in _hook_scopes().scoped_hooks(config_text)
    }
    missing = [hook for hook in HOOKS if hook not in scoped]
    if missing:
        raise SystemExit(
            f"kdbx_attestation: no `files:` scope for {', '.join(missing)} in "
            ".pre-commit-config.yaml - cannot tell which paths the KDB-X gates judge"
        )
    return {
        hook: (re.compile(files), re.compile(exclude) if exclude else None)
        for hook, (files, exclude) in ((hook, scoped[hook]) for hook in HOOKS)
    }


def hooks_judging(path: str, scopes: Scopes) -> set[str]:
    """The KDB-X hooks whose scope - `files:` less `exclude:` - takes `path`."""
    return {
        hook
        for hook, (files, ex) in scopes.items()
        if files.search(path) and not (ex and ex.search(path))
    }


def is_judged(path: str, scopes: Scopes) -> bool:
    """Whether any KDB-X hook's scope takes `path`."""
    return bool(hooks_judging(path, scopes))


def needed_hooks(paths: list[str], scopes: Scopes | None = None) -> list[str]:
    """The KDB-X hooks a change to `paths` must have passed, sorted.

    Only these: a hook whose scope the change does not touch reads exactly
    the inputs it read on the base, where it already passed - see ATTEST in
    this module's docstring for why that holds.
    """
    if scopes is None:
        scopes = judged_scopes()
    return sorted(set().union(*(hooks_judging(p, scopes) for p in paths)))


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def changed_paths(base: str, head: str, cwd: Path | None = None) -> list[str]:
    """What `head` changes since its merge base with `base` - the three-dot
    diff, so a commit on `base` the branch does not have is not counted.

    A rename is its deletion and its addition (`--no-renames`), so a judged
    file moved out of scope still counts; `-z` gives each name as it is,
    not C-quoted, so a non-ASCII name still matches a scope (#2406)."""
    out = git("diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", cwd=cwd)
    return [p for p in out.split("\0") if p]


def parse_trailer(line: str, tree: str) -> tuple[set[str], str, str] | None:
    """(the hooks one trailer line attests for `tree`, what q-tests ran, what
    q-coverage ran), or None if it is not one.

    `KDB-X-gates: <tree> hooks=a,b q-tests=<sha256|all> q-coverage=<sha256|all>`
    names its hooks and, for q-tests and q-coverage, the subset each ran by
    digest, or `all`. Without `q-tests=` - written before subsets - q-tests
    ran the whole suite; without `q-coverage=` - before #1719 - so did
    q-coverage. A bare `KDB-X-gates: <tree>`, written before attestations were
    scoped, ran every hook over the whole tree and counts as all of them.
    """
    prefix = f"{TRAILER}: {tree}"
    line = line.strip()
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix) :]
    if not rest.strip():
        return set(HOOKS), "all", "all"
    words = rest.split()
    if not rest.startswith(" ") or not words[0].startswith("hooks="):
        return None
    ran = {"q-tests": "all", "q-coverage": "all"}
    for word in words[1:]:
        key, eq, value = word.partition("=")
        if key not in ran or not eq or not re.fullmatch(r"all|[0-9a-f]{64}", value):
            return None
        ran[key] = value
    hooks = {h for h in words[0].removeprefix("hooks=").split(",") if h}
    return hooks, ran["q-tests"], ran["q-coverage"]


def trailer_hooks(line: str, tree: str) -> set[str] | None:
    """The hooks one trailer line attests for `tree`, or None if it is not one."""
    parsed = parse_trailer(line, tree)
    return None if parsed is None else parsed[0]


def _attestations(head: str) -> list[tuple[set[str], str, str]]:
    """Every trailer for head's tree - on head itself, or the empty commits
    attestations add on top of the same tree - parsed."""
    tree = git("rev-parse", f"{head}^{{tree}}").strip()
    found: list[tuple[set[str], str, str]] = []
    log = git("log", "--format=%T%x00%B%x01", head)
    for entry in log.split("\x01"):
        entry = entry.strip("\n")
        if not entry:
            continue
        commit_tree, _, body = entry.partition("\x00")
        if commit_tree != tree:
            break
        for line in body.splitlines():
            parsed = parse_trailer(line, tree)
            if parsed is not None:
                found.append(parsed)
    return found


def attested(head: str) -> set[str]:
    """The hooks attested for head's tree, together. Empty when none is."""
    return set().union(*(hooks for hooks, _, _ in _attestations(head)))


def q_tests_runs(head: str) -> set[str]:
    """What the q-tests attestations of head's tree ran: `all` and digests."""
    return {q for hooks, q, _ in _attestations(head) if "q-tests" in hooks}


def q_coverage_runs(head: str) -> set[str]:
    """What the q-coverage attestations of head's tree ran: `all` and digests."""
    return {c for hooks, _, c in _attestations(head) if "q-coverage" in hooks}


def pr_q_tests_plan(base: str, head: str):
    """The q suites q_test_impact.py selects for `base...head` in this checkout
    (CI checks the head out): a q_test_impact.Plan, its files None for all."""
    return _plan_for(base, head)


@functools.cache
def _plan_for(base: str, head: str):
    # once per PR: q-tests' and q-coverage's checks both need it, and drawing
    # the graph is most of the check's time
    impact = _q_test_impact()
    return impact.plan_for(impact.changed_paths(base, REPO, head), REPO)


def pr_q_coverage_plan(base: str, head: str):
    """What q-coverage runs for `base...head`: q_test_impact.coverage_for over
    q-tests' plan - a CoveragePlan, its files None for the whole suite."""
    impact = _q_test_impact()
    return impact.coverage_for(
        pr_q_tests_plan(base, head), impact.changed_paths(base, REPO, head), REPO
    )


def q_tests_refusal(base: str, head: str) -> str | None:
    """Why head's q-tests attestation does not cover the PR, or None.

    The whole suite covers any change. A subset covers it only when the
    selection made here, from the diff and the tree, has the same digest."""
    runs = q_tests_runs(head)
    if "all" in runs:
        return None
    plan = pr_q_tests_plan(base, head)
    full = f"`UQF_Q_TESTS_FULL=1 {ATTEST}`"
    if plan.files is None:
        return (
            f"q-tests ran a subset of the q suites, but here the selection is the whole "
            f"suite ({plan.why}). Run {full} on this tree, then push."
        )
    if plan.record not in runs:
        return (
            f"q-tests ran a subset of the q suites ({', '.join(sorted(runs))}) that is not "
            f"the one selected here ({len(plan.files)} files, {plan.record}). Was the "
            "attestation's qlinter the release .qlinter-version pins? Install that, "
            f"or run {full}, on this tree, then push."
        )
    return None


def q_coverage_refusal(base: str, head: str) -> str | None:
    """Why head's q-coverage attestation does not cover the PR, or None.

    A whole run covers any change. A subset - q-tests' suites, judging the
    production files the change reaches (#1719) - covers it only when the
    one worked out here, from the diff and the tree, has the same digest."""
    runs = q_coverage_runs(head)
    if "all" in runs:
        return None
    cov = pr_q_coverage_plan(base, head)
    full = f"`UQF_Q_TESTS_FULL=1 {ATTEST}`"
    if cov.files is None:
        return (
            f"q-coverage ran a subset, but here it is the whole suite ({cov.why}). "
            f"Run {full} on this tree, then push."
        )
    if cov.record not in runs:
        return (
            f"q-coverage ran a subset ({', '.join(sorted(runs))}) that is not the one "
            f"worked out here ({len(cov.files)} suite files, {len(cov.modules)} modules, "
            f"{cov.record}). Was the attestation's graph qlinter the commit "
            f".qlinter-graph-rev pins? Install that, or run {full}, on this tree, then push."
        )
    return None


def contains(head: str, base: str) -> bool:
    return (
        subprocess.run(["git", "merge-base", "--is-ancestor", base, head], check=False).returncode
        == 0
    )


def base_judged_changes(base: str, head: str, scopes: Scopes) -> list[str]:
    """The judged paths `base` changed since its merge base with `head` - the
    base side's three-dot diff. Empty means a head behind `base` merges into
    a tree whose judged paths are the head's own."""
    return [p for p in changed_paths(head, base) if is_judged(p, scopes)]


def _names(hooks) -> str:
    return ", ".join(sorted(hooks)) or "none"


def report(base: str, head: str) -> tuple[bool, str]:
    """(attested, what to tell the PR)."""
    scopes = judged_scopes()
    changed = changed_paths(base, head)
    judged = [p for p in changed if is_judged(p, scopes)]
    if not judged:
        return True, "no path the KDB-X gates judge changed - no attestation needed"
    needed = needed_hooks(judged, scopes)
    shown = ", ".join(judged[:5]) + (f" and {len(judged) - 5} more" if len(judged) > 5 else "")
    covered = attested(head)
    scope = f"needed: {_names(needed)}; this tree's attestation covers: {_names(covered)}"
    if not covered:
        return False, (
            f"this tree has no KDB-X attestation, and it changes {shown} ({scope}). "
            f"On a machine with KDB-X, with the branch clean: `{ATTEST}`, then push."
        )
    missing = [hook for hook in needed if hook not in covered]
    if missing:
        return False, (
            f"this tree's KDB-X attestation does not cover {_names(missing)}, which "
            f"{shown} needs ({scope}). Run `{ATTEST}` again on this tree, then push."
        )
    if "q-tests" in needed:
        why = q_tests_refusal(base, head)
        if why:
            return False, why
    if "q-coverage" in needed:
        why = q_coverage_refusal(base, head)
        if why:
            return False, why
    if contains(head, base):
        return True, f"the KDB-X gates passed on exactly this tree ({scope})"
    moved = base_judged_changes(base, head, scopes)
    if moved:
        theirs = ", ".join(moved[:5]) + (f" and {len(moved) - 5} more" if len(moved) > 5 else "")
        return False, (
            "this tree is attested, but it does not contain the base's tip, and the "
            f"base has since changed {theirs}, which the KDB-X gates judge - so the "
            f"tree that merges is not the one attested ({scope}). "
            f"Merge the base in, then `{ATTEST}` again."
        )
    return True, (
        "the KDB-X gates passed on this tree, and the base has changed no path they "
        f"judge since the merge base, so the tree that merges has the same judged paths ({scope})"
    )


# ---------------------------------------------------------------- commands


def attest(args: argparse.Namespace) -> int:
    """Run the KDB-X hooks `args.base...HEAD` needs (every one with
    `args.all`) and, when all pass, record the trailer as an empty commit."""
    why = dirt()
    if why:
        print(f"attest: refused: {why}", file=sys.stderr)
        return 1
    hooks = sorted(HOOKS) if args.all else needed(args.base)
    if not hooks:
        print(
            f"attest: nothing in {args.base}...HEAD is in a KDB-X hook's scope - "
            "no attestation needed, nothing recorded"
        )
        return 0
    why = refusal()
    if why:
        print(f"attest: refused: {why}", file=sys.stderr)
        return 1
    tree = _git.git("rev-parse", "HEAD^{tree}", cwd=REPO).stdout.strip()
    print(f"attest: needed: {', '.join(hooks)}", flush=True)
    cov = None
    cov_ran = "all"
    envs: dict[str, dict[str, str]] = {}
    needs_plan = "q-tests" in hooks or "q-coverage" in hooks
    plan = q_tests_plan(args.base, args.all) if needs_plan else None
    impact = _q_test_impact()
    if "q-tests" in hooks and plan is not None:
        envs["q-tests"] = {impact.DECIDED_ENV: plan.line}
        print(f"attest: q-tests: {plan.why}", flush=True)
    one_run = "q-tests" in hooks and "q-coverage" in hooks
    with tempfile.TemporaryDirectory(prefix="kdbx_attestation.") as tmp:
        outcomes = Path(tmp)
        if "q-coverage" in hooks:
            cov = q_coverage_plan(args.base, plan)
            envs["q-coverage"] = {
                impact.COVERAGE_DECIDED_ENV: cov.line,
                COVERAGE_RAN_ENV: str(outcomes / "coverage_ran"),
            }
            print(f"attest: q-coverage: {cov.why}", flush=True)
            if cov.files is not None:
                print(f"attest: {master_coverage_status()}", flush=True)
        if one_run:
            envs["q-coverage"].update(outcome_env(outcomes))
            envs["q-tests"][FROM_COVERAGE_ENV] = "1"
            print("attest: q-tests: its q-unit verdict is the q-coverage run's", flush=True)
        for hook in run_order(hooks):
            print(f"attest: {hook} ...", flush=True)
            started = time.monotonic()
            passed = run_hook(hook, envs.get(hook))
            print(f"attest: {hook}: {time.monotonic() - started:.0f}s", flush=True)
            if not passed:
                print(f"attest: {hook} failed - nothing recorded", file=sys.stderr)
                return 1
            if hook == "q-coverage" and one_run:
                why = unit_verdict(outcomes)
                if why:
                    print(f"attest: q-tests failed: {why} - nothing recorded", file=sys.stderr)
                    return 1
            if hook == "q-coverage":
                ran_ = coverage_ran(outcomes / "coverage_ran")
                if ran_ is None:
                    print(
                        "attest: q-coverage passed but did not say what it ran "
                        f"(${COVERAGE_RAN_ENV}) - nothing recorded",
                        file=sys.stderr,
                    )
                    return 1
                cov_ran = ran_
                if cov_ran == "all" and cov is not None and cov.files is not None:
                    print(
                        "attest: q-coverage: the subset found something, and the "
                        "whole suite decided",
                        flush=True,
                    )
    if _git.git("rev-parse", "HEAD^{tree}", cwd=REPO).stdout.strip() != tree or dirt():
        print(
            "attest: a hook changed the tree - commit that, then run this again",
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
        _git.git(
            "commit",
            "--allow-empty",
            "-m",
            f"KDB-X gates pass on tree {tree[:12]}",
            "-m",
            f"{', '.join(hooks)} - {why_these} - run on KDB-X over the whole tree."
            f"{ran}\n\n{trailer}",
            cwd=REPO,
        )
    except subprocess.CalledProcessError as exc:
        # The commit runs the repository's hooks too; one that refuses (a
        # qlinter off its pin, say) is named here rather than as a traceback.
        print(
            f"attest: the gates passed but the commit was refused:\n{exc.stdout}{exc.stderr}",
            file=sys.stderr,
        )
        return 1
    print(f"attest: recorded {trailer} - push to show it on the PR")
    return 0


def check(args: argparse.Namespace) -> int:
    """Whether `args.head`'s tree is attested for what it changes since its
    merge base with `args.base`; the answer is appended to `args.summary`
    when given, with an annotation for the PR."""
    ok, message = report(args.base, args.head)
    overridden = not ok and os.environ.get(OVERRIDE_ENV, "").lower() == "true"
    if overridden:
        message = f"OVERRIDDEN by the `{OVERRIDE_LABEL}` label - {message}"
    print(f"kdbx_attestation: {message}")
    if args.summary is not None:
        with open(args.summary, "a") as summary:
            summary.write(f"{message}\n")
        if overridden:
            print(f"::warning title=KDB-X attestation overridden::{message}")
        elif not ok:
            print(f"::error title=KDB-X attestation::{message}")
    return 0 if ok or overridden else 1


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="scripts/dev/kdbx_attestation.py", description=__doc__.strip().splitlines()[0]
    )
    sub = ap.add_subparsers(dest="command", required=True, metavar="COMMAND")
    one = sub.add_parser(
        "attest",
        help="run the KDB-X hooks this branch needs and record that they passed",
        description="Run the KDB-X hooks this branch needs, on KDB-X, and record the trailer.",
    )
    one.add_argument("--all", action="store_true", help="run every KDB-X hook")
    one.add_argument("--base", default=BASE, help=f"compare with this (default {BASE})")
    one.set_defaults(run=attest)
    two = sub.add_parser(
        "check",
        help="report whether a PR's head tree is attested for its change (CI, no q)",
        description="Report whether HEAD's tree carries the attestation BASE...HEAD needs.",
    )
    two.add_argument("base", metavar="BASE")
    two.add_argument("head", metavar="HEAD")
    two.add_argument(
        "summary", metavar="SUMMARY", nargs="?", type=Path, help="CI's step summary to append to"
    )
    two.set_defaults(run=check)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
