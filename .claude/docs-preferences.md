# Documentation preferences

What the maintainer has asked for, and asked against, when documentation
changed. Read by `docs-maintainer` before it edits anything.

**This file holds PREFERENCES, never facts about the code.** A preference is
something only the maintainer can tell you — how the repository should be
framed, what belongs in a heading, which files are off-limits. A fact — what
a function is called, what a command's arguments are, whether a gate exists —
is re-verified against the tree every time, because a remembered fact is
weaker evidence than the source and goes stale silently. Every wrong claim
this repository's docs have carried came from asserting a fact from memory or
a grep instead of reading the path.

**Entries are deleted when superseded, not accumulated.** A rule nobody
applies is worse than no rule: it makes the file long enough that the next
reader skims it, which costs the rules that do matter.

Each entry names the instance that prompted it, so a reader can judge whether
it still applies rather than obeying it blindly.

---

## Framing

**The repository is several components, not one library.** Lead with the span
— data engineering, the quant library, data processing, operational tooling —
and let the quant modules be one section among several.

> *2026-09-16.* The README opened "A q/kdb+ library of quantitative-finance
> functions strictly scoped to eFX" with a table of the twelve pricing
> modules as its third section. Asked for: "there should be rather implication
> that this repo consists of many different components for data engineering,
> quant, data processing, data science etc". `Modules` became `Quant modules`
> and a `Components` section was added above it.

**Examples cover the components, not just the quant library.** A Quick start
showing three pricing calls tells someone who came for the pipeline framework
that it does not exist.

> *2026-09-17.* Quick start was three `.qopt`/`.qfwd`/`.qexec` calls. Now four
> entry points: price something, run the fleet, run a backfill, run the tests.

**Say what is verified, not that it is the only possibility.** A requirement
stated as "you need X" and "this tree targets X alone" claims two things and
establishes one. Separate them: what the tree is verified against, and what it
could plausibly run on, with the evidence for each.

> *2026-09-17.* Asked: "suggest that kdb-x is preferred but there are also
> other free implementation that could possibly work". `## Requirements` said
> "You need KDB-X". Checking found the quant library loads on a third-party q
> while the full suite does not complete, and that `scripts/test.sh` had read
> `$Q`/`$QHOME` all along — so the override existed and was simply
> undocumented.

## Structure

**A heading names a section; it does not restate its content.** If the first
paragraph already says it, the heading should not.

> *2026-09-16.* `## Config generation: never edit the vendored tree` → `##
> Config generation`. The section's own first paragraph already said the
> vendored files are never written to.

**Reference material goes in its own file, not inline.** Detail someone
consults deliberately should not sit in the path of someone reading past it.

> *2026-09-17.* 95 lines of per-dependency licensing moved from `README.md`
> to `LICENSING.md`, leaving three lines and a link.

**No prescriptive meta-rules about the documentation process itself.**
Describe what is, not how future documents must be filed.

> *2026-09-17.* `docs/README.md`'s "Adding a document" section — which told
> the reader to ask which of five questions their document answered — was
> removed on request: "remove this part plx".

## Scope

**Name the dependencies a documented command needs**, including the ones that
are missing, rather than assuming the reader has them.

> *2026-09-17.* Asked: "could you add qcon to dependencies". `torq-demo raw --
> qcon` was documented while `qcon` ships with some kdb+ distributions and not
> the KDB-X personal edition. Requirements now carries a per-component table
> saying which tool each part needs and whether it is required.

**Every command in a document is run before it is written down.**

> *2026-09-17.* Two of the Quick start commands were wrong: `query` had its
> argument form inverted, and the backfill invocation died at
> `.servers.startup[]` because a backfill registers with discovery and needs
> the fleet. Neither was catchable by a gate — `check_doc_references` verifies
> that `.q*` functions exist, not that a shell command works.

## What is not touched

**Vendored trees.** `lib/torq`, `lib/torq-finance-starter-pack` — never
edited, including their own scripts and their own names (H-01).

**Historical records.** `CHANGELOG.md`, `docs/migrations/`,
`docs/drift-reports/drift-ledger.md`. A changelog entry states what shipped on
a date and a drift-ledger row states what canonical had; rewriting either
makes the record claim something that never happened.

> *2026-09-17.* During the `torq-demo` → `uqf-stack` rename the drift ledger
> was rewritten by the first pass and reverted for this reason, while the
> other historical files were left alone from the start. The maintainer was
> told which files still carry the old name and why, rather than the boundary
> being applied silently.

**Generated files.** `docs/man.q`, `docs/decisions/`,
`docs/integrations/torq/processes.md`, `src/etl/generated/pipeline_dag.q`,
`docs/migrations/surfaces/uqf-local/`. Edit the source and regenerate; CI
fails on a hand-edit.
