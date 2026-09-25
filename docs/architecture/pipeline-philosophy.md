# How the pipeline framework thinks

`src/etl/` has a shape, and the shape came from a small number of positions held
consistently. This page states them, because the framework assessment
([`pipeline-framework-gaps.md`](pipeline-framework-gaps.md)) says what each
piece replaced, but not *why* the whole is arranged this way. Someone changing
`src/etl/` needs the why, or the next change will be locally reasonable and
globally wrong.

Where a principle is enforced, the enforcement is named. A principle nothing
enforces is an intention, and this page tries not to contain any.

--------------------------------------------------------------------------------

## 1. A claim is true, or absent. It is never wrong.

This is the one every other position is downstream of.

The coverage ledger says "this window of this dataset is published at this
source release". Something reads that and decides not to re-fetch. So a coverage
row that is *wrong* does not cause an error --- it causes a gap that nobody ever
looks for again. A coverage row that is *missing* causes work to be redone,
which is merely wasteful.

Those two failures are not symmetrical, and almost every ordering decision in
the framework falls out of preferring the second:

- **Publish, then record coverage, then checkpoint.** `.qwrt.finish_window`
  sequences all three, and the order is the requirement, not an implementation
  detail. Interrupt it anywhere and the result is an under-claim.
- **A failed data-quality check takes the same path as a failed fetch.** The
  window is not published, no coverage is staged, the run continues, and the
  next run plans that window again because coverage never claimed it. That is
  what makes a check safe to add to a worker that already exists: the worst case
  is work redone.
- **A window that published nothing is still recorded** (`rows_published=0`). An
  empty window is positive evidence that the range was examined and held
  nothing. Without the row, a reader cannot tell that from a range never
  attempted.
- **`run_id` is the null guid outside a run**, rather than an invented identity.
  Absent attribution is visibly absent; wrong attribution reads as correct.

The same instinct governs reporting outside the ledger. `uqs summary`
distinguishes three heartbeat states --- a value, `-` (the collector is up and
has no row for this process), and `not collected` (nothing is collecting at all) ---
because rendering the last two identically would turn a monitoring gap into an
all-clear.

## 2. State the weakest guarantee that is true.

The framework could have promised "exactly-once processing". It promises the
opposite, in as many words: **do not assume exactly-once** --- it establishes
retry-safe publication and coverage skipping, which is a weaker and more honest
guarantee.

That sentence is doing real work. A caller who believes in exactly-once writes
code with no defence against a duplicate. A caller told "retries are safe, and
covered ranges are skipped" writes code that tolerates one. The second caller is
correct on a system that can actually be built; the first is correct only on a
system nobody has.

Consequently the framework advertises what it enforces and stays quiet about
what it merely usually does.

## 3. Nothing exists that does nothing.

A capability that is defined, tested, and reached from no live path is worse
than a missing one, because it reads as protection while asserting nothing.

This tree has produced the failure repeatedly and the examples are kept rather
than tidied away: `.qdqc` carried nine data-quality check functions that nothing
called, which meant the coverage ledger could record a window as complete that
had failed its own checks. `docs/man.q` was generated for months and never
loaded by anything. `.qwcfg.set_layers` implemented three documented
configuration layers with no production caller.

So: **a check that is not on the publish path is decoration**, and a capability
that works only under the test runner is not a capability. When something is
added here, the question asked first is which live path reaches it.

The corollary is restraint. `.qio` defines `write` and nothing else --- no
`read`, no `exists` --- because nothing in this framework reads a target back
through an abstraction. Those go in when something calls them.

## 4. Derive; do not restate.

Anything written down twice will disagree eventually, and the disagreement is
silent. So the second copy is generated, or checked, or both:

  | Derived thing                      | From                              | Held by                                |
  | ---                                | ---                               | ---                                    |
  | `src/etl/generated/pipeline_dag.q` | the pipeline registry             | `generate_operational_docs.py --check` |
  | `docs/man.q`                       | the qDoc blocks in `src/`         | `generate_man_registry.py --check`     |
  | `docs/reference/processes.md`      | the pipeline registry             | `generate_operational_docs.py --check` |
  | `docs/reference/environment.md`    | the code that reads each variable | `check_env_reference.py`               |
  | the contract surface               | the loaded q tree                 | `contract_surface.py check`            |

Where a thing genuinely must be declared by hand, the declaration is verified
against the code it describes: `verify_pipeline_edges` reads each pipeline's
`.sub.subscribe` and `.u.upd` calls back out of its own q script and fails if
they disagree with the registry. A hand-drawn diagram goes stale silently; that
one cannot.

Prose cannot be generated, so it is checked instead --- the split settled:
generate what is mechanical, check what is not. `check_doc_references.py` reads
every living document and confirms that each `.q*` function it names exists, and
that any call it shows passes no more arguments than the function takes. The
gate's first run found two references left behind by renames: a design note
citing `.qmicro.require_sorted_tape` (the function is `require_tape`) and an
agent citing `.qcoer.coerce` (it is `coerce_column`). Neither was caught by
anything else, because a name that appears in no generated artifact has nothing
to disagree with.

The same reflex applies to borrowed values. The orchestrator extends
`monitor1`'s subscription list by *parsing the vendored list and appending to
it*, rather than pinning a copy made on the day it was written.

## 5. Refuse at the boundary. Never heal silently.

A component handed something malformed should stop, naming what is wrong, before
the first read that would depend on it:

- `.qmatz.require_schema` refuses a ledger built to a different shape, because
  every read here would otherwise aggregate across whatever the unexpected
  column distinguishes, and a range covered for one value of it would report as
  covered for all.
- `.qbw.advanced_to` refuses a cursor that would stand still or move backwards,
  because a run's progress cursor is what tells a stuck run from a finished one.
  (It used to be `plan`'s lower bound as well, which made a restatement behind
  the cursor unreachable; coverage decides what to plan now, and the cursor
  tracks progress within a run.)
- `.qbw.define` refuses two workers declaring the same dataset *and* partition,
  because their coverage rows would then be indistinguishable, and refuses a
  worker that supplies its own `ns`, because the namespace is derived from the
  worker's name --- `.qwrk.<worker name>`.
- `.qmatz.require_interval` refuses a zero-width or reversed window, because
  recording one claims completeness for no data.

Each refusal names a specific failure it prevents. A guard whose comment cannot
name one is usually guarding nothing.

The inverse --- silent healing --- is treated as a bug even in test scaffolding.
`.testutil.reset_coverage_ledger` carries a comment about a version that
returned an empty *symbol vector* instead of an empty table, which every suite
then healed on first use, hiding the fault until something called `meta`
directly.

## 6. Required where the value is a choice; ambient where it is a fact.

`source_version` is a required parameter of `.qmatz.stage_completion`, not an
optional filter, on the grounds that an optional filter is one a caller forgets ---
and forgetting this one merges coverage across releases.

`run_id` looks like the same case and is not. `source_version` is a *choice* the
caller makes, and the wrong choice is silent corruption. `run_id` is a *fact
about the executing process*, like `recorded_at`'s `.z.p`, with exactly one
correct value at any instant. Threading it through five signatures would create
the opportunity to pass a wrong one --- a failure mode that otherwise does not
exist.

So the test is not "is this important" but **"can the caller be wrong about
it?"** If yes, demand it. If no, read it.

## 7. History accumulates; it is not overwritten.

The coverage ledger is append-only. A restatement does not edit the row it
replaces --- it stamps `superseded_at`, and every read takes an as-of instant
(see [`.qmatz.supersede`](../../src/etl/core/materialisation.q)). A claim is
therefore *true until superseded* rather than *true or gone*, and "what did we
believe on Tuesday" stays answerable.

Current rows carry a far-future sentinel (`0Wp`) rather than a null, so an as-of
comparison needs no special case. `.qrun` uses the same device for a run that
has not ended.

There is exactly one mutation in the ETL tree --- `.qrun.finish` updating the
row `begin` wrote --- and it is argued for in place: a run's outcome is not
known when it starts, and appending a second row would make "how many runs were
there" ambiguous.

## 8. Authority is split, and the split is written down.

The split of who owns what is explicit. q and TorQ own process startup, source
reads, query failures, checkpoints, run and window counts, and coverage events.
Airflow owns task ordering, scheduling, retries, timeouts, concurrency and alert
routing. The two exchange **structured status** --- neither infers the other's
facts from its log text.

This is why there is no scheduler here, and why adding one would be a regression
rather than a feature: it would create a second authority for ordering and
retries, and the two would disagree.

The same rule governs the vendored trees. `lib/torq` and
`lib/torq-finance-starter-pack` are never edited; they are extended through
overlays, overrides, and command-line configuration that the framework applies
*after* every vendored layer. An edit would work until the next upgrade and then
be silently lost.

## 9. The declaration and the thing declared live apart, but arrive together.

`src/etl/core/` defines the contract. `sources/` and `workers/` declare against
it. The core never depends on a declaration --- `check_etl_layering.py` enforces
that in CI --- so the framework can be reasoned about without knowing what is
declared on top of it.

But a declaration *registers itself as it loads*, which is why `src/etl/init.q`
loads declarations last. There is deliberately no way to have a declaration
without its implementation, or a registry entry describing something that is not
there.

## 10. Two frameworks, one adapter, and the adapter is the only thing that knows TorQ.

There are two kinds of ETL process here, and they are deliberately *two*
frameworks of the same shape rather than one framework with a flag:

  |             | bounded (batch)                                              | streaming                                                                             |
  | ---         | ---                                                          | ---                                                                                   |
  | framework   | `.qbw` — `core/bounded_worker.q`                             | `.qstream` — `core/stream_job.q`                                                      |
  | an instance | `.qwrk.<worker>`, `workers/`                                 | `.qsub.<job>`, `streaming/`                                                           |
  | declaration | `.qbw.define[name; source dataset width transform …]`        | `.qstream.register[name; procname subscribes publishes on_batch on_timer …]`          |
  | runner      | `scripts/processes/torq_backfill.q`                          | `scripts/processes/torq_stream.q`                                                     |
  | lifecycle   | init → plan → fetch → transform → publish → cover → **done** | wire `publish` → subscribe → `on_batch` per tick, `on_timer` per period → **forever** |

A bounded worker covers a stated range and finishes, so it can carry a contract
(`.qbfstate.require_contract`) and a coverage claim. A streaming job never
finishes, so it carries neither (§2: state the weakest guarantee that is true ---
for a tailer that is freshness, not completion). Forcing the two under one
abstraction would give every instance a lifecycle half of which is null. The
instance namespace is derived from the name in both (`.qwrk.x`, `.qsub.x`), and
an instance file is its own logic plus one declaration: on the bounded side
`define` stamps the inherited lifecycle methods into the namespace (#227), on
the streaming side the declaration carries the callbacks and the runner wires
only `publish`.

**Nothing under `src/etl/` knows TorQ exists**. That is what lets a worker or
job load in a plain q process and be tested with a recorder in place of a
tickerplant. The one namespace allowed to know TorQ is `.qpipe`
(`scripts/processes/torq_pipeline.q`), the adapter: find the tickerplant, open
the access-listed handle, reshape rows for `.u.upd`, trap a timer so it is not
silently deactivated. It is called by the runners and by nothing in `src/`. The
arrow points one way --- `src/` never reaches into `scripts/` --- and the day it
pointed the other way the symptom was a try-with-fallback around
`.qpipe.status_dir` in `backfill_state.q`, guarding against a namespace that
might not be loaded (#229). A dependency you have to guard against being absent
is a dependency pointing the wrong way.

What both halves share and the outside world reads --- the status file the
Airflow sensor and the frontend poll --- lives in `core/status.q` (`.qstatus`),
not in the adapter, because it is not TorQ plumbing: it is a cross-repository
contract, and its header names its readers.

*Enforced by* `scripts/gates/check_etl_layering.py`, twice: `core/` may not
reference a declaring namespace (§9), and nothing under `src/etl/` may reference
`.qpipe`.

## 11. This repository is public, and the real sources are not.

Every source, dataset and table here is a generic analogue --- `demo_deals`,
`demo_events`, `event_tape`. No bank table name, hostname, schema shape or
business rule appears in code, tests, comments or commit messages.

Credentials come from the environment only (`UQF_SOURCE_CRED_*`), with no file
fallback and no vault, because a file fallback is how a credential ends up
committed. Source queries are parameterised q lambdas rather than concatenated
strings; where a driver genuinely cannot parameterise, there is exactly one
escape function and everything routes through it.

--------------------------------------------------------------------------------

## On the borrowed vocabulary

The concepts are lifted from asset-oriented orchestrators --- Dagster most
directly. Asset, op, resource, IO manager, partition, asset check, run identity,
config schema: the words mean here roughly what they mean there, and
`pipeline-framework-gaps.md` is an explicit audit of this tree against that
model.

What was taken is the **asset-oriented framing**: the unit of work is *a dataset
being made correct for a window*, not *a script that runs*. That is why the
ledger records materialisations rather than executions, why coverage is
expressed as intervals that compose, and why a worker declares its inputs and
outputs so a graph can be derived instead of drawn.

What was deliberately **not** taken is scheduling, for the reason in §8.

And the implementation is not a port. q makes some of this cheaper than it would
be elsewhere --- a table is a first-class value, so the ledger is an ordinary
table that round-trips to disk with `set`/`get` and is queried with qSQL, rather
than a service to be called; names bind at call time, so a mutual reference
between two modules needs no dependency injection to resolve. It also makes some
of it harder, which the code records where it bit: reserved builtins that shadow
at load time, an empty-vector-versus-empty-table distinction that heals itself,
and a random number generator seeded identically in every process, so the
obvious way to mint a unique id returns the same value everywhere.

## See also

- [`pipeline-framework-gaps.md`](pipeline-framework-gaps.md) --- the closed
  assessment against Dagster: what each piece replaced, and the four differences
  that are decisions.
