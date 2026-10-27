#!/usr/bin/env python3
"""Report whether a pull request's tree has passed the KDB-X gates (#1014).

Hosted CI has no KDB-X licence, so the hooks scripts/dev/attest_kdbx.py
names (q-tests, q-coverage, q-docs, contract-surface, q-facts) run only on
a contributor's machine - and master needed a repair commit for them on
almost every day of a fortnight: a change merged on a green CI that no KDB-X
run had seen, and the next KDB-X session paid for it inside an unrelated
diff.

This binds a merge to a KDB-X run without needing q here. A KDB-X session
attests a tree with scripts/dev/attest_kdbx.py, which runs the hooks the
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
its scope. That is a hook-scope bug, which scripts/gates/check_hook_scopes.py
exists to catch. If the base side did touch a judged path, the head is
refused and the path named: merge the base in and attest again.

q-tests may have run a SUBSET of the q suites (#1690): those
scripts/dev/test_impact.py selects from q-lint's dependency graph for the
branch's change. The trailer then says `q-tests=<sha256>`, the digest of the
sorted file names (test_impact.digest), and this check makes the same
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
change reaches. `q-coverage=<sha256>` (test_impact.coverage_digest, over those
suites and modules) is verified the same way, from test_impact.coverage_for
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

    python3 scripts/gates/check_kdbx_attestation.py BASE HEAD [SUMMARY]
"""

from __future__ import annotations

import functools
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

TRAILER = "KDB-X-gates"

#: The PR label that lets an unattested tree pass, and the variable CI sets
#: from it - "true" when the label is on the PR.
OVERRIDE_LABEL = "kdbx-attestation-override"
OVERRIDE_ENV = "KDBX_ATTESTATION_OVERRIDE"


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # registered before it runs: a @dataclass looks its own module up there
    sys.modules[Path(rel).stem] = module
    spec.loader.exec_module(module)
    return module


Scopes = dict[str, tuple[re.Pattern, re.Pattern | None]]


def judged_scopes(config_text: str | None = None) -> Scopes:
    """The (files, exclude) scope of each hook attest_kdbx.py runs, by hook id.

    Read from .pre-commit-config.yaml, the way check_hook_scopes.py reads it,
    for the hooks attest_kdbx.py names - not kept here as a second list. A
    hand-kept copy had already drifted narrower than the hooks: q-tests ran
    the ```q blocks in docs/ (`.md`), contract-surface reads Python and
    scripts/processes/, and a change to only those was reported as needing no
    attestation (#1334). A hook with no `files:` would match every path, and
    one that is not in the config at all is a rename this must not paper
    over, so both stop here rather than guess.
    """
    hooks = _load("scripts/dev/attest_kdbx.py").HOOKS
    scopes_mod = _load("scripts/gates/check_hook_scopes.py")
    if config_text is None:
        config_text = scopes_mod.CONFIG.read_text()
    scoped = {
        hook: (files, exclude) for hook, files, exclude in scopes_mod.scoped_hooks(config_text)
    }
    missing = [hook for hook in hooks if hook not in scoped]
    if missing:
        raise SystemExit(
            f"check_kdbx_attestation: no `files:` scope for {', '.join(missing)} in "
            ".pre-commit-config.yaml - cannot tell which paths the KDB-X gates judge"
        )
    return {
        hook: (re.compile(files), re.compile(exclude) if exclude else None)
        for hook, (files, exclude) in ((hook, scoped[hook]) for hook in hooks)
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
    the inputs it read on the base, where it already passed - see
    scripts/dev/attest_kdbx.py for why that holds.
    """
    if scopes is None:
        scopes = judged_scopes()
    return sorted(set().union(*(hooks_judging(p, scopes) for p in paths)))


ATTEST = "uv run python scripts/dev/attest_kdbx.py"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def changed_paths(base: str, head: str) -> list[str]:
    """What `head` changes since its merge base with `base` - the three-dot
    diff, so a commit on `base` the branch does not have is not counted."""
    return git("diff", "--name-only", f"{base}...{head}").split()


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
        return set(_load("scripts/dev/attest_kdbx.py").HOOKS), "all", "all"
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


def q_tests_plan(base: str, head: str):
    """The q suites test_impact.py selects for `base...head` in this checkout
    (CI checks the head out): a test_impact.Plan, its files None for all."""
    return _plan_for(base, head)


@functools.cache
def _plan_for(base: str, head: str):
    # once per PR: q-tests' and q-coverage's checks both need it, and drawing
    # the graph is most of the check's time
    impact = _load("scripts/dev/test_impact.py")
    return impact.plan_for(impact.changed_paths(base, REPO, head), REPO)


def q_coverage_plan(base: str, head: str):
    """What q-coverage runs for `base...head`: test_impact.coverage_for over
    q-tests' plan - a CoveragePlan, its files None for the whole suite."""
    impact = _load("scripts/dev/test_impact.py")
    return impact.coverage_for(
        q_tests_plan(base, head), impact.changed_paths(base, REPO, head), REPO
    )


def q_tests_refusal(base: str, head: str) -> str | None:
    """Why head's q-tests attestation does not cover the PR, or None.

    The whole suite covers any change. A subset covers it only when the
    selection made here, from the diff and the tree, has the same digest."""
    runs = q_tests_runs(head)
    if "all" in runs:
        return None
    plan = q_tests_plan(base, head)
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
    cov = q_coverage_plan(base, head)
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


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    ok, message = report(argv[1], argv[2])
    overridden = not ok and os.environ.get(OVERRIDE_ENV, "").lower() == "true"
    if overridden:
        message = f"OVERRIDDEN by the `{OVERRIDE_LABEL}` label - {message}"
    print(f"check_kdbx_attestation: {message}")
    if len(argv) == 4:
        with open(argv[3], "a") as summary:
            summary.write(f"{message}\n")
        if overridden:
            print(f"::warning title=KDB-X attestation overridden::{message}")
        elif not ok:
            print(f"::error title=KDB-X attestation::{message}")
    return 0 if ok or overridden else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
