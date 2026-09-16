# How the pipeline framework thinks

`src/etl/` has a shape, and the shape came from a small number of positions
held consistently. This page states them, because the requirements
(`docs/reference/etl-framework-requirements.md`) say *what* the code must do
and the gap register
([`pipeline-framework-gaps.md`](pipeline-framework-gaps.md)) says what is
missing, but neither says *why* any of it is arranged this way. Someone
changing `src/etl/` needs the why, or the next change will be locally
reasonable and globally wrong.

Where a principle is enforced, the enforcement is named. A principle nothing
enforces is an intention, and this page tries not to contain any.

---

## 1. A claim is true, or absent. It is never wrong.

This is the one every other position is downstream of.

The coverage ledger says "this window of this dataset is published at this
source release". Something reads that and decides not to re-fetch. So a
coverage row that is *wrong* does not cause an error — it causes a gap that
nobody ever looks for again. A coverage row that is *missing* causes work to
be redone, which is merely wasteful.

Those two failures are not symmetrical, and almost every ordering decision in
the framework falls out of preferring the second:

- **Publish, then record coverage, then checkpoint** (ETL-05, ETL-07).
  `.qwrt.finish_window` sequences all three, and the order is the
  requirement, not an implementation detail. Interrupt it anywhere and the
  result is an under-claim.
- **A failed data-quality check takes the same path as a failed fetch.** The
  window is not published, no coverage is staged, the run continues, and the
  next run plans that window again because coverage never claimed it. That is
  what makes a check safe to add to a worker that already exists: the worst
  case is work redone.
- **A window that published nothing is still recorded** (`rows_published=0`).
  An empty window is positive evidence that the range was examined and held
  nothing. Without the row, a reader cannot tell that from a range never
  attempted.
- **`run_id` is the null guid outside a run**, rather than an invented
  identity. Absent attribution is visibly absent; wrong attribution reads as
  correct.

The same instinct governs reporting outside the ledger. `torq-demo summary`
distinguishes three heartbeat states — a value, `-` (the collector is up and
has no row for this process), and `not collected` (nothing is collecting at
all) — because rendering the last two identically would turn a monitoring gap
into an all-clear.

## 2. State the weakest guarantee that is true.

ETL-13 could have said "exactly-once processing". It says the opposite, in as
many words: **do not assume exactly-once** — the framework establishes
retry-safe publication and coverage skipping, "which is a weaker and more
honest guarantee."

That sentence is doing real work. A caller who believes in exactly-once
writes code with no defence against a duplicate. A caller told "retries are
safe, and covered ranges are skipped" writes code that tolerates one. The
second caller is correct on a system that can actually be built; the first is
correct only on a system nobody has.

Consequently the framework advertises what it enforces and stays quiet about
what it merely usually does.

## 3. Nothing exists that does nothing.

A capability that is defined, tested, and reached from no live path is worse
than a missing one, because it reads as protection while asserting nothing.

This tree has produced the failure repeatedly and the examples are kept
rather than tidied away: `.qdqc` carried nine data-quality check functions
that nothing called, which meant the coverage ledger could record a window as
complete that had failed its own checks. `docs/man.q` was generated for
months and never loaded by anything. `.qwcfg.set_layers` implemented three
documented configuration layers with no production caller.

So: **a check that is not on the publish path is decoration**, and a
capability that works only under the test runner is not a capability. When
something is added here, the question asked first is which live path reaches
it.

The corollary is restraint. `.qio` defines `write` and nothing else — no
`read`, no `exists` — because nothing in this framework reads a target back
through an abstraction. Those go in when something calls them.

## 4. Derive; do not restate.

Anything written down twice will disagree eventually, and the disagreement is
silent. So the second copy is generated, or checked, or both:

| Derived thing | From | Held by |
|---|---|---|
| `src/etl/generated/pipeline_dag.q` | the pipeline registry | `generate_operational_docs.py --check` |
| `docs/man.q` | the qDoc blocks in `src/` | `generate_man_registry.py --check` |
| `docs/integrations/torq/processes.md` | the pipeline registry | `generate_operational_docs.py --check` |
| `docs/decisions/` | the GitHub issue comments | `build_decision_log.py --check` |
| `docs/reference/environment.md` | the code that reads each variable | `check_env_reference.py` |
| the contract surface | the loaded q tree | `contract_surface.py check` |

Where a thing genuinely must be declared by hand, the declaration is verified
against the code it describes: `verify_pipeline_edges` reads each pipeline's
`.sub.subscribe` and `.u.upd` calls back out of its own q script and fails if
they disagree with the registry. A hand-drawn diagram goes stale silently;
that one cannot.

The same reflex applies to borrowed values. The orchestrator extends
`monitor1`'s subscription list by *parsing the vendored list and appending to
it*, rather than pinning a copy made on the day it was written.

## 5. Refuse at the boundary. Never heal silently.

A component handed something malformed should stop, naming what is wrong,
before the first read that would depend on it:

- `.qcov.require_schema` refuses a ledger built to a different shape, because
  every read here would otherwise aggregate across whatever the unexpected
  column distinguishes, and a range covered for one value of it would report
  as covered for all.
- `.qbw.advanced_to` refuses a cursor that would stand still or move
  backwards, because `plan` uses the cursor as its *lower* bound — a window
  processed out of order pushes the cursor past windows still uncovered, and
  the next run never comes back for them.
- `.qbw.define` refuses two workers declaring the same dataset, because
  coverage has no partition dimension and their rows would be
  indistinguishable.
- `.qcov.require_interval` refuses a zero-width or reversed window, because
  recording one claims completeness for no data.

Each refusal names a specific failure it prevents. A guard whose comment
cannot name one is usually guarding nothing.

The inverse — silent healing — is treated as a bug even in test scaffolding.
`.testutil.reset_coverage_ledger` carries a comment about a version that
returned an empty *symbol vector* instead of an empty table, which every
suite then healed on first use, hiding the fault until something called
`meta` directly.

## 6. Required where the value is a choice; ambient where it is a fact.

ETL-09 makes `source_version` a required parameter of
`.qcov.stage_completion`, not an optional filter, on the grounds that an
optional filter is one a caller forgets — and forgetting this one merges
coverage across releases.

`run_id` looks like the same case and is not. `source_version` is a *choice*
the caller makes, and the wrong choice is silent corruption. `run_id` is a
*fact about the executing process*, like `recorded_at`'s `.z.p`, with exactly
one correct value at any instant. Threading it through five signatures would
create the opportunity to pass a wrong one — a failure mode that otherwise
does not exist.

So the test is not "is this important" but **"can the caller be wrong about
it?"** If yes, demand it. If no, read it.

## 7. History accumulates; it is not overwritten.

The coverage ledger is append-only. A restatement does not edit the row it
replaces — it stamps `superseded_at`, and every read takes an as-of instant
(D-11, [`restatement-design.md`](restatement-design.md)). A claim is
therefore *true until superseded* rather than *true or gone*, and "what did
we believe on Tuesday" stays answerable.

Current rows carry a far-future sentinel (`0Wp`) rather than a null, so an
as-of comparison needs no special case. `.qrun` uses the same device for a
run that has not ended.

There is exactly one mutation in the ETL tree — `.qrun.finish` updating the
row `begin` wrote — and it is argued for in place: a run's outcome is not
known when it starts, and appending a second row would make "how many runs
were there" ambiguous.

## 8. Authority is split, and the split is written down.

ETL-15 is explicit about who owns what. q and TorQ own process startup,
source reads, query failures, checkpoints, run and window counts, and
coverage events. Airflow owns task ordering, scheduling, retries, timeouts,
concurrency and alert routing. The two exchange **structured status** —
neither infers the other's facts from its log text.

This is why there is no scheduler here, and why adding one would be a
regression rather than a feature: it would create a second authority for
ordering and retries, and the two would disagree.

The same rule governs the vendored trees. `lib/torq` and
`lib/torq-finance-starter-pack` are never edited (H-01); they are extended
through overlays, overrides, and command-line configuration that the
framework applies *after* every vendored layer. An edit would work until the
next upgrade and then be silently lost.

## 9. The declaration and the thing declared live apart, but arrive together.

`src/etl/core/` defines the contract. `sources/` and `workers/` declare
against it. The core never depends on a declaration — `check_etl_layering.py`
enforces that in CI — so the framework can be reasoned about without knowing
what is declared on top of it.

But a declaration *registers itself as it loads*, which is why
`src/etl/init.q` loads declarations last. There is deliberately no way to
have a declaration without its implementation, or a registry entry describing
something that is not there.

## 10. This repository is public, and the real sources are not.

Every source, dataset and table here is a generic analogue — `demo_deals`,
`demo_events`, `event_tape`. No bank table name, hostname, schema shape or
business rule appears in code, tests, comments or commit messages (A-04).

Credentials come from the environment only (`UQF_SOURCE_CRED_*`), with no
file fallback and no vault, because a file fallback is how a credential ends
up committed (bank E-07). Source queries are parameterised q lambdas rather than
concatenated strings; where a driver genuinely cannot parameterise, there is
exactly one escape function and everything routes through it (bank E-08).

---

## On the borrowed vocabulary

The concepts are lifted from asset-oriented orchestrators — Dagster most
directly. Asset, op, resource, IO manager, partition, asset check, run
identity, config schema: the words mean here roughly what they mean there,
and `pipeline-framework-gaps.md` is an explicit audit of this tree against
that model.

What was taken is the **asset-oriented framing**: the unit of work is *a
dataset being made correct for a window*, not *a script that runs*. That is
why the ledger records materialisations rather than executions, why coverage
is expressed as intervals that compose, and why a worker declares its inputs
and outputs so a graph can be derived instead of drawn.

What was deliberately **not** taken is scheduling, for the reason in §8.

And the implementation is not a port. q makes some of this cheaper than it
would be elsewhere — a table is a first-class value, so the ledger is an
ordinary table that round-trips to disk with `set`/`get` and is queried with
qSQL, rather than a service to be called; names bind at call time, so a mutual reference between
two modules needs no dependency injection to resolve. It also makes some of
it harder, which the code records where it bit: reserved builtins that shadow
at load time, an empty-vector-versus-empty-table distinction that heals
itself, and a random number generator seeded identically in every process, so
the obvious way to mint a unique id returns the same value everywhere.

## See also

- [`pipeline-framework-gaps.md`](pipeline-framework-gaps.md) — what this
  framework has and lacks, gap by gap.
- [`restatement-design.md`](restatement-design.md) — the bitemporal coverage
  design behind §7.
- [`../reference/etl-framework-requirements.md`](../reference/etl-framework-requirements.md)
  — ETL-01..ETL-24, the contract CI holds the code to.
