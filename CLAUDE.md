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

Several of this tree's most expensive recurring bugs are things it checks
for: a q builtin used as a parameter name, a line containing only `/` opening
a block comment, a legacy `datetime` where a timestamp was meant.

**Assert the message, not just the throw.** `assertError` passes on *any*
error, including an undefined-name error from a typo in the test itself - so
a test that meant to prove a refusal can pass while proving nothing. Use
`assertThrows` with the expected text where the message is part of what the
code promises.

**Its findings are clues, not proof.** They are heuristics over text: verify
each against the code and, where it matters, against a running q. A clean run
means no rule matched, which is much weaker than "this is correct" - it is
never a substitute for `q tests/run_tests.q`.

Installed separately, because the linter is its own project:
`cargo install --git https://github.com/kwojdalski/q-lint --locked`.

## Working on a GitHub issue: always in a new worktree

Several Claude Code sessions work in this repository at once. Whenever you pick
up a GitHub issue to work on, you MUST do that work in a new git worktree,
never in the main checkout.

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

Only triage without edits (reading code, commenting, labelling) may stay in
the main checkout.
