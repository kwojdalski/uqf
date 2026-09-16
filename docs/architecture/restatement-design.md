# Restatements: what answering D-11 "yes" actually costs

**Status:** design note, awaiting review. **Nothing in this document is
implemented.**

**Decision being acted on:** D-11 (issue #72) was answered *"yes — rows can
be superseded in place"*, over the alternatives *"no, append-only, a
correction is a new `source_version`"* and *"yes, but only via a
full-window replace"*.

This note exists because that answer changes what `is_covered` **means**,
and sixteen files rest on the current meaning. Writing the code first and
discovering the semantics second is how a completeness ledger starts giving
confidently wrong answers — which is the one failure the ledger exists to
prevent.

---

## 1. What changes, precisely

Today the ledger answers one question:

> Has `[range_from, range_to)` of this dataset been published at this
> `source_version`?

It is a **yes/no about the past**, and it is decidable because coverage is
append-only: a row, once written, is true forever.

With supersession the honest question becomes:

> Has `[range_from, range_to)` of this dataset been published at this
> `source_version`, **as understood at time T**?

That is a different question, and the difference is not cosmetic:

| | today | with supersession |
|---|---|---|
| `is_covered` | a fact | a fact **relative to an as-of** |
| a coverage row | permanently true | true **until superseded** |
| "the range is complete" | unambiguous | ambiguous without an as-of |
| re-reading last week's answer | same answer | **may differ, legitimately** |

The last row is the one that matters. A report run twice over the same
range can correctly give two different answers, and today's API has no way
to express which one you asked for.

## 2. Three things must be decided before any code

### 2.1 The row key — and E-23 deliberately left it open

Supersession needs to know *which* row is being superseded. The framework
has no row-level identity: **E-23** is explicitly open on whether a bounded
worker may use an idempotency mechanism alongside versioned coverage, and
the requirements say the framework "does not define a universal row-level
idempotency-key interface".

So this decision comes first, and it is per-dataset:

- **Natural key** — e.g. `deal_id` for a deal source. Cheap, but only
  correct if the source guarantees uniqueness, and a source that reuses ids
  after a purge would silently merge unrelated rows.
- **Composite key** — e.g. `(deal_id, sym)`. Safer, more to declare.
- **Source-supplied revision** — the source itself says "this supersedes
  revision N". Best when available, absent from most.

Whichever it is, it belongs in the **source contract** (`.qsrc`
declaration), next to `fields`/`types`/`time_field`, because that is where
per-source facts already live and because `register` can then validate that
the declared key is among the declared fields.

### 2.2 The as-of dimension — two incompatible shapes

**Option A — bitemporal rows in the target.** Each row carries
`valid_from`/`valid_to`; a restatement closes the old row and inserts a new
one. Reads pick an as-of.

- Every read in the library must choose an as-of or get the latest by
  default. `markout_at_horizons`, `apply_fill` and friends currently assume
  one row per key.
- Storage grows with revisions, not rows.
- Auditable: the old value is still there, which is usually the point of a
  restatement.

**Option B — revision on the coverage row only.** The target is replaced
for the restated window; coverage records that the window was re-published
at revision N.

- Much smaller change: the target keeps one row per key, and nothing
  downstream has to learn about as-of.
- But the previous *values* are gone. If the reason for the restatement was
  an audit question, the answer has been overwritten.
- And it is really "full-window replace" — which was the third option D-11
  was offered and **not** chosen. If A's cost is unacceptable, that is a
  signal to revisit D-11 rather than to quietly implement B.

**This note does not pick.** A is what "superseded in place" most naturally
means; B is cheaper and arguably not what was asked for.

### 2.3 What `is_covered` returns

Three candidates, in increasing honesty and cost:

1. **Latest only.** `is_covered[ds;version;from;to]` keeps its signature and
   silently means "as of now". Zero migration. But every historical
   question becomes unanswerable, and the function's name now hides a
   parameter — the thing this codebase keeps getting bitten by.
2. **Required as-of.** `is_covered[ds;version;as_of;from;to]`. Every one of
   the call sites below must pass one, which makes the question explicit at
   every site. Follows the precedent of `source_version` in E-09, which is
   a *required* parameter precisely because an optional filter is one a
   caller forgets.
3. **Both, named apart.** `is_covered` (latest) and `is_covered_as_of`.
   Convenient, but the convenient one will be used everywhere and (1)'s
   problem returns.

**Recommendation: (2).** It is the only one where a wrong answer is
impossible rather than merely unlikely, and it matches how this tree
already treats `source_version`. The cost is real and is listed in §3.

## 3. Blast radius

Seven functions in `src/etl/core/coverage.q` change meaning:
`stage_completion`, `intervals`, `is_covered`, `missing`, `require_covered`,
and — depending on §2.2 — `compose` and `gaps`, since intervals from
different revisions must not merge the way same-version intervals do.

Call sites needing an as-of threaded through:

```
src/etl/core/worker_runtime.q   needs_fetch      -> .qcov.is_covered
src/etl/core/worker_runtime.q   remaining        -> .qcov.missing
src/etl/core/worker_runtime.q   require_upstream -> .qcov.require_covered
src/etl/core/worker_runtime.q   finish_window    -> .qcov.stage_completion
src/etl/core/continuous_state.q (comment only — deliberately no coverage path)
```

Also affected:

- **`.qcov.schema`** gains columns, which collides with **#60**: the shape
  is *already* unverified, and this would change it before the current one
  has ever been checked against the real ledger. See §5.
- **`python/uqf_frontend`** — `queries.py`'s `COVERAGE` program,
  `catalog.py`'s `etl_coverage` entry, `coverage.py`'s interval building,
  and `test_catalog_drift.py`'s q-side cross-check.
- **`python/uqf_airflow_provider`** — unaffected. It reads worker *status*,
  not coverage, which is E-15's split working as intended.
- **16 files total** currently depend on the assumed shape (8 source, 8
  test).

## 4. What this buys, stated fairly

It is worth being clear that the current model already handles corrections
— by bumping `source_version` and re-extracting. Supersession buys:

- **Granularity.** A one-row fix costs one row, not a whole window
  re-publish.
- **Provenance** (option A only). The old value survives, which a
  `source_version` bump also gives you, but at whole-range granularity.

And costs:

- The row key decision (§2.1), which E-23 left open on purpose.
- An as-of parameter at every completeness question (§2.3), or a silent
  default.
- `compose`/`gaps` needing revision-awareness, which is where subtle
  wrongness would live: merging intervals across revisions would report a
  range as complete using rows that have since been superseded.

## 5. Recommended sequence

Deliberately ordered so the riskiest thing is not first:

1. **Settle #60 first.** One `meta etl_coverage` on the work machine. It is
   perverse to add columns to a schema that has never been checked against
   the real one — and if it turns out to carry a partition key, that
   interacts with revisions in ways best known before designing them.
2. **Decide §2.1 (the key) and §2.2 (A or B).** Both are decisions, not
   discoveries; no amount of work here produces them.
3. **Implement the key in the source contract only.** Declarable,
   validated, unused. Zero blast radius, and it is a prerequisite either
   way. *This is the one step that is safe to take before the rest is
   settled.*
4. **Then coverage**, with `is_covered` taking a required as-of, migrating
   the five call sites in one change so no site is briefly using the old
   meaning.
5. **Then the frontend**, whose `COVERAGE` program and catalog entry follow
   mechanically once the q side is fixed.

## 6. The interaction with D-08, which is favourable

D-08 was answered *"leave the published rows, record no coverage, re-run
redoes the window"*, and I noted at the time that this **duplicates rows
unless the publish path dedupes** — E-13 promises retry-safe publication,
which is explicitly weaker than exactly-once.

A row key (§2.1) is exactly what makes that dedupe possible. So the two
answers fit together, and the key is worth having for D-08 alone even if
supersession is later deferred. That is an argument for step 3 above
independent of everything else in this note.

---

## Decisions taken (2026-09-16), and what was built

The three questions §2 said had to be settled before any code were answered
by the maintainer, and steps 3–5 of §5 are implemented:

| Question | Decision |
|---|---|
| §2.1 row key | **Natural key, per-source.** Already declared and validated in the `.qsrc` contract; it now has a purpose rather than being reserved. |
| §2.2 as-of shape | **Option A — bitemporal.** A restatement closes the old claim rather than overwriting it, so the earlier belief survives. |
| §2.3 `is_covered` | **Required as-of.** `is_covered[ds;version;as_of;from;to]`, following `source_version`'s precedent. |

**What that looks like in the ledger.** One new column, `superseded_at`,
carrying `0Wp` while a claim is current. Not two columns: `recorded_at`
already records when a claim began, so `valid_from` would have duplicated it.
And `0Wp` rather than a null because `as_of<0Np` is *false* in q — a null
would have dropped every current row from an as-of read and reported a fully
published range as empty, which is a plausible wrong answer rather than an
error.

**What changed.** `.qcov.valid_at` filters to the claims true at an instant,
and `intervals`/`is_covered`/`missing`/`require_covered` all take an as-of.
`.qcov.supersede` withdraws overlapping claims; `.qcov.history` is the audit
view. The five call sites migrated in one change, so no site was briefly
using the old meaning. `.qbw.plan` captures **one** `.z.p` for a whole
planning pass rather than reading it per call — otherwise a range could be
reported both covered and uncovered within a single pass.

**Frontend.** `queries.COVERAGE` takes the as-of; `/coverage` sends one taken
at request time. The parameter is named `at`, not `asof`: **`asof` is a q
builtin**, and `test_q_programs.py` caught it before it shipped.

**Still open, and deliberately so.** Questions 4 and 5 below — whether
restatements arrive as a feed or an operator action, and whether there is a
bound on how far back one may reach. Neither blocks what is built: `supersede`
is a function an operator or a feed can call, and nothing yet assumes either.

## Open questions for review

1. **§2.2: A or B?** Bitemporal rows, or full-window replace recorded as a
   revision? B is cheaper but is the option D-11 was offered and not
   chosen.
2. **§2.1: what is the row key** for a deal-shaped source — natural,
   composite, or source-supplied revision?
3. **§2.3: required as-of, or latest-by-default?** I recommend required,
   following `source_version`'s precedent.
4. **Do restatements arrive as a feed, or as an operator action?** A feed
   needs the worker to recognise a supersession in-flight; an operator
   action can be a separate tool. Nothing above assumes either.
5. **Is there a bound on how far back a restatement may reach?** Unbounded
   means every historical answer is provisional forever.
