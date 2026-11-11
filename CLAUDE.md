# uqf

## Reach for qlinter before tracing q code by hand

[q-lint](https://github.com/kwojdalski/q-lint) is a suggested tool, not a
required one (see the README's Requirements), and it is worth running at the
*start* of q debugging rather than the end. It reads source without executing
it, so pointing it at a file costs nothing and risks nothing.

```bash
qlinter path/to/affected.q          # one file
qlinter src/ tests/q/               # a baseline over the tree
qlinter --explain QF005             # what a diagnostic means
```

Several of this tree's most expensive recurring bugs are things it checks for: a
q builtin used as a parameter name, a line containing only `/` opening a block
comment, a legacy `datetime` where a timestamp was meant.

**Assert the message, not just the throw.** `assertError` passes on *any* error,
including an undefined-name error from a typo in the test itself - so a test
that meant to prove a refusal can pass while proving nothing. Use `assertThrows`
with the expected text where the message is part of what the code promises.

**Its findings are clues, not proof.** They are heuristics over text: verify
each against the code and, where it matters, against a running q. A clean run
means no rule matched, which is much weaker than "this is correct" - it is never
a substitute for `q tests/run_tests.q`.

Installed separately, because the linter is its own project:
`cargo install --git https://github.com/kwojdalski/q-lint --tag v0.14.21 --locked`.
The tag must match `.qlinter-version`, the one place the pin is set: the q-traps
hook refuses any other installed version.

## Test suites live under `.qt`, defined from the root

A q test suite is a namespace `.qt.<production-owner>[.<suite>]` that mirrors
what it tests - `.qt.qetl.coverage`, `.qt.qpipe.job.fx_markout` - and the runner
finds it by the `test_*` functions it holds: there is no list to add it to.
Define every name with its whole path (`.qt.qetl.io.batch:{[] ...}`), and call a
suite's own helpers by their whole path too. Never `\d .qt...` or
`system "d ..."`: q 4.0 rejects a nested context, and a leading dot is absolute,
so only the full path means the same on every version. Shared helpers go in
`.qt.support` (`tests/lib/support.q`) and doubles or fixture workers in
`.qt.fixture.*`; a namespace under `.qt` that holds functions but no test is
refused at load. `uqs job new` names a job's suite for you
(`.qt.qpipe.job.<job>`); `tests/lib/discovery.q` has the rules and
`tests/q/test_suite_layout.q` the cases.

## Filing a GitHub issue: always labelled

Every issue gets a kind label (`bug`, `enhancement`, `documentation`,
`deployment`, or the finding skill's own: `bugfinder`, `architecture`,
`antipattern`, `brainstorm`) and one rank label:
`severity:critical|high|medium|low` for a defect or structural finding, matching
any **Severity:** line in the body; `priority:high|medium|low` for an idea or
chore. A severity written only in the body is not enough: the triage skills pick
work by label.

## Each check runs in one place

Hooks are targeted; a pull request's CI runs the gates and attestation, and
master and the nightly run run the full suites (#1879, #2167). A commit runs
only the fast set - format, lint, static gates, the generated-file checks - and
takes seconds. A push runs the test suites (#1965): `python-tests` only the
Python tests the push reaches, `q-tests` and `q-coverage` only the q suites the
branch reaches, and `peachq-push` the q test files the branch adds or changes on
PeachQ - plus `test_docstring_examples.q` when an `@eg` line changes (#1978) -
with `tests/q/peachq_known_gaps.txt` expected. That last is an early catch, not
a second home: CI's PeachQ job still runs the whole suite and decides. A pull
request's CI runs the author check, every hook and static gate, the KDB-X
attestation check and, for a `web/` change, the browser application; the whole
Python suite, the whole q suite on PeachQ and the docs render (with the docs' q
blocks on PeachQ) run on every push to master and nightly
(`scripts/dev/ci_scope.py`), so a regression they catch surfaces on master
within a run. That is a switch, not a removal: the repository variable
`CI_PR_TESTS` (Settings -> Secrets and variables -> Actions -> Variables) unset
or `lean` is the above, and `CI_PR_TESTS=full` has every pull request run each
test group its change reaches again, as before #2167; tag
`ci/full-pr-tests-baseline` is master's `ci.yml` from before the change. Full
mode is one switch away: `UQF_HOOKS_FULL=1 git commit` or `git push`, or
`python3 scripts/dev/hooks_full.py --all` for both stages without either
(`UQF_Q_TESTS_FULL=1` still runs the whole q suite alone); in CI, the `ci:full`
PR label, `gh workflow run ci.yml --ref <branch> -f full=true`, every push to
master and the nightly schedule. A generated file never stops a merge:
`.gitattributes` keeps one side, the post-merge hook regenerates it, and its
`--check` gate still decides - register the driver once per clone with
`python3 scripts/dev/merge_generated.py install` and install the push and merge
hooks with `pre-commit install`. README's Testing section says where each check
runs.

## Working on a GitHub issue: always in a new worktree

Several Claude Code sessions work in this repository at once. Whenever you pick
up a GitHub issue to work on, you MUST do that work in a new git worktree, never
in the main checkout.

1. `/claim <number>` first. If the issue is already claimed, stop.

2. Create a worktree on a new branch off an up-to-date `master`:
   ```bash
   git fetch origin
   git worktree add .claude/worktrees/issue-<number> -b kwojdalski/issue-<number>-<slug> origin/master
   ```
   (or use the `EnterWorktree` tool). `.claude/worktrees/` is gitignored.

3. Do all edits, tests and commits inside that worktree. Never run
   `git checkout`, `git switch`, `git stash` or `git reset` in the main
   checkout: another session is probably using it.

4. When done: push the branch, open a PR, `/release <number>`, then
   `git worktree remove .claude/worktrees/issue-<number>`.

   If the PR touches q (a `.q` file, `tests/q/`, a generator or what it
   generates, any `.py`) and you have KDB-X, run
   `uv run python scripts/dev/kdbx_attestation.py attest` after the LAST rebase
   and push again. Hosted CI cannot run the KDB-X gates (q-tests, q-coverage,
   q-docs, contract-surface, q-facts). This runs only the ones whose `files:`
   scope the branch's diff from `origin/master` touches - each over the whole
   tree - and records a `KDB-X-gates: <tree> hooks=<ids>` trailer, with
   `q-tests=` and `q-coverage=` each a digest of the subset the change reaches
   or `all`; `--all` runs every hook, and a branch that needs none records
   nothing. A Python-only change needs only contract-surface, plus q-facts where
   it touches what q-facts generates - seconds, not the q suite; a `.q` change
   needs all of them; a doc needs q-docs only if it has a `q-example:` block.
   CI's "KDB-X attestation" check works out the same set from the PR's merge
   base and fails the PR unless its head tree carries trailers covering every
   hook in it (#1014, #1543); a trailer without `hooks=` counts as all of them.
   A rebase changes the tree, so it needs a fresh run. A head behind master
   still passes while master's commits since the merge base touch no judged
   path; the check names the path when one does, and only then must master be
   merged in and the tree attested again (#1687). The only way past it is the
   `kdbx-attestation-override` PR label, for a change no KDB-X run can judge
   (docs-only, no KDB-X available): the check then passes with a warning that
   names the label, so the override stays on the record.

   `scripts/dev/ship.sh <pr> issue-<number>` does the rest once a PR may merge:
   it attests only when the check needs it, merges master in only for a conflict
   or a judged change on master, waits for "Repository checks" to pass, and
   merges, then starts the whole-suite coverage run on master.

Only triage without edits (reading code, commenting, labelling) may stay in the
main checkout.
