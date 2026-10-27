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
`cargo install --git https://github.com/kwojdalski/q-lint --tag v0.14.14 --locked`.
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
   `uv run python scripts/dev/attest_kdbx.py` after the LAST rebase and push
   again. Hosted CI cannot run the KDB-X gates (q-tests, q-coverage, q-docs,
   contract-surface, q-facts). This runs only the ones whose `files:` scope the
   branch's diff from `origin/master` touches - each over the whole tree - and
   records a `KDB-X-gates: <tree> hooks=<ids>` trailer; `--all` runs every one,
   and a branch that needs none records nothing. A Python-only change needs only
   contract-surface, plus q-facts where it touches what q-facts generates -
   seconds, not the q suite; a `.q` change needs all of them; a doc needs q-docs
   only if it has a `q-example:` block. CI's "KDB-X attestation" check works out
   the same set from the PR's merge base and fails the PR unless its head tree
   carries trailers covering every hook in it (#1014, #1543); a trailer without
   `hooks=` counts as all of them. A rebase changes the tree, so it needs a
   fresh run. A head behind master still passes while master's commits since the
   merge base touch no judged path; the check names the path when one does, and
   only then must master be merged in and the tree attested again (#1687). The
   only way past it is the `kdbx-attestation-override` PR label, for a change no
   KDB-X run can judge (docs-only, no KDB-X available): the check then passes
   with a warning that names the label, so the override stays on the record.

   `scripts/dev/ship.sh <pr> issue-<number>` does the rest once a PR may merge:
   it attests only when the check needs it, merges master in only for a conflict
   or a judged change on master, waits for "Repository checks" to pass, and
   merges.

Only triage without edits (reading code, commenting, labelling) may stay in the
main checkout.
