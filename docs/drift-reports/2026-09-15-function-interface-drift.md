# UQF Function and Interface Drift Report — accumulating transcription

> **Provenance and status — read first.**
>
> This file accumulates a transcription of a *Function and Interface Drift
> Report* generated on the work machine, where the canonical Bitbucket `uqf`
> tree lives. That tree is not reachable from here, so the report reached this
> repository only as **photographs of a rotated monitor**.
>
> The source report is roughly **2,000 lines** of dense path-and-evidence
> entries. What is recorded below is what could be read **reliably**:
> structure, section taxonomy, disposition vocabulary, and the file inventory
> on each side. Individual per-callable entries are **not** transcribed,
> because at that resolution they cannot be read without guessing — and a
> guessed path or line number in a drift report is worse than an absent one.
>
> Anything marked `UNREAD` is legible-in-principle but was not captured.
> Anything marked `INFERRED` is a reading I am not certain of.
>
> **This is not the report.** Treat the report on the work machine as
> authoritative wherever the two differ.

## What the report compares

Two trees, named `primary` and `comparison` throughout:

- **`primary`** — appears to be the canonical Bitbucket tree. Paths are bare
  (`python/torq_orchestrator/...`, `src/etl/...`, `scripts/...`).
- **`comparison`** — appears to be the GitHub mirror, and therefore includes
  the work done in this session. Paths are prefixed `comparison/`.

INFERRED, from the path sets on each side. Worth confirming from the report's
own header, which was not legible.

## Section taxonomy

Headings observed, with their counts UNREAD:

```
## Function and Interface Drift Report
### Function Callable Inventory
### primary-only            (count UNREAD)
### comparison-only         (count UNREAD)
### name-differs            (count UNREAD)
### body-differs            (count UNREAD)
```

A per-entry line is shaped roughly:

```
**<category>**  [<side>]<path>:<line>  <symbol>  :  <finding>;
    Evidence: <kind> <path>:<line>, <kind> <path>:<line>
    Disposition: **<action>**
```

## Disposition vocabulary

These recur verbatim and are the actionable part of the report:

| Disposition / finding | Meaning as written |
|---|---|
| `Keep comparison side-local or port manually` | Exists only in `comparison`; no automatic path back |
| `Keep primary side-local` | Exists only in `primary` (INFERRED — the word after "primary" was not legible in every instance) |
| `Keep primary paradigm; callers must be migrated together` | Signature or contract differs; call sites move as a unit |
| `Callable exists only in primary; package entry points/imports and callers must be present in primary` | — |
| `Callable exists only in comparison; package entry points/imports and tests are not present in primary` | — |
| `Callable exists only in primary; its implementation and any published/state contract are primary-side behaviour` | — |
| `Pairing is unresolved because repeated name declarations are not ...` | Ambiguous match; needs a human (tail UNREAD) |

Evidence kinds observed: `caller/reference`, `test`, `doc/example/script`,
`package entry`.

## File inventory by side

### comparison-only — the frontend package written this session

Every module and test of `python/uqf_frontend/` appears as `comparison-only`,
which is expected: it does not exist upstream at all.

```
python/uqf_frontend/src/uqf_frontend/
  app.py  capture.py  catalog.py  config.py  coverage.py  errors.py
  fleet.py  gateway.py  health.py  models.py  ops.py  procfile.py  queries.py

python/uqf_frontend/tests/
  test_api.py  test_capture.py  test_catalog_drift.py  test_config.py
  test_coverage.py  test_health.py  test_ops.py  test_procfile.py
  test_q_programs.py  test_security.py
```

Also appearing on the `comparison` side:

```
scripts/check_hook_scopes.py
scripts/torq_pipeline.q            (INFERRED — the .q entries were harder to read)
```

### primary-only — upstream subsystems absent from the mirror

Consistent with the earlier drift comparison (`docs/migrations/`):

```
python/uqf_airflow_provider/src/uqf_airflow_provider/
  operators.py  hooks.py  ...                       (further files UNREAD)

python/torq_orchestrator/src/torq_orchestrator/
  ...                                               (specific modules UNREAD)

src/etl/core/    src/etl/workers/                   (per-file entries UNREAD)

scripts/
  generate_operational_docs.py  generate_diagrams.py
  check_doc_examples.py  install-git-hooks.sh
```

## Not captured

- Every per-callable entry: symbol names, line numbers, and the specific
  evidence lists. This is the bulk of the report.
- All section counts.
- The report header: generation command, timestamp, and the two tree
  revisions being compared. **This is the single most valuable missing
  piece** — without the two revisions, the report cannot be re-derived or
  even dated against a commit.

## Appending further instalments

More photographs are expected. Each instalment should be added as its own
section below, dated, rather than merged into the above — so that a later
reader can see what was captured when, and from what.

## Instalment 2 — 2026-09-15, further photographs

This batch was more legible in places. It also revealed that the material is
**not one report but at least three**, which changes how this file should be
organised.

### There are three distinct reports

| Report | Heading seen | What it compares |
|---|---|---|
| 1 | `UQF Function and Interface Drift Report` | callables: primary-only / comparison-only / name-differs / body-differs |
| 2 | `File Drift Report` | paths: moves, residual primary-only, residual comparison-only |
| 3 | `Capability Drift` | user- or operator-visible capability, with a confidence rating |

The earlier instalment described only report 1.

### Report 3: `Capability Drift` — the classification vocabulary

This is the most valuable thing captured so far, because it is the layer at
which a human decides anything. Read reliably:

- **`primary only`** / **`comparison only`** — an evidenced user or operator
  capability exists on **one side only**.
- **`partial drift`** — *both* sides do the related work, but "a contract,
  implementation, wiring, default, or validation differs". This is the
  category that matters: it is where the two trees silently disagree rather
  than one simply lacking something.
- **`superseded`** — (definition not legible).
- **`primary-edge`** / **`comparison-edge`** — an evidenced advantage on one
  side.

Each row carries a **confidence** of `high` or `low`. Column order appears to
be capability, primary state, comparison state, classification, evidence and
synchronisation action, confidence. Individual rows are UNREAD.

### Report 2: `File Drift Report` — dispositions read verbatim

The disposition sentences are long-form prose and were the most legible text
in the batch:

- **On the client package — this is the packaging conflict already flagged in
  this repository.** The report states that the primary's Airflow
  orchestration and **underscore-named client package** "cannot be copied
  over the comparison's frontend and **hyphen-named** package without
  resolving packaging and import contracts". Locally that is
  `python/uqf-client/` versus upstream `python/uqf_client/` — the same rename
  that made the pre-commit lint scope go narrow (closed here in PR #52) and
  that issue #60's sibling concerns were raised against.
- **On agent definitions** — `Merge manually` with the primary agent set;
  "these are side-local workflow rules". Consistent with the
  `.claude/agents/` versus `.github/agents/*.agent.md` question already open.
- **On generated documentation** — "regenerate from the selected source and
  toolchain rather than copying them".
- **On retained context** — some paths "are not ordinary pruned additions"
  but are "intentionally retained here so they do not disappear from the
  inventory".
- **On non-equivalent scripts and tests** — they "may contain
  comparison-side behavior that must be explicitly ported". That phrasing
  matters: it is the report declining to auto-classify the work done in this
  session, and asking for a human decision per item.

### Numbers read reliably

- **20 exact move destinations** are excluded from the residual primary-only
  inventory, on the grounds that an exact move is not drift.

### A note on comparison identity

The report states it is **not** claiming two identical snapshots: the primary
already held an untracked report before the run, and the new report is written
under its own path deliberately. So the report is aware it is part of the tree
it describes.

### Still not captured

Everything per-row, in all three reports. Specifically: the `Capability
Drift` table rows (the actionable layer), the per-callable entries, all
section counts, and the report headers naming the two revisions compared.

## Instalment 3 — focused second pass over the more legible photographs

A deliberate re-read of the lower-density screens, which carry prose and
table headers rather than packed path lists. This yields one correction and
several things worth more than the per-row data.

### Correction to instalment 1: which two trees are being compared

Instalment 1 recorded, as an INFERENCE, that `comparison` was the GitHub
mirror. The report's own identity block appears to say otherwise:

```
primary:     /home/s6999648/repos/uqf        @ <hash>  (dirty)
comparison:  /home/s6999648/repos/uqf-copy   @ <hash>  (without origin)
```

So both sides are **local checkouts on the work machine**, and `comparison`
is **`uqf-copy`** — the snapshot that the earlier drift comparison put at
`b62464e3`, which is exactly the GitHub mirror's `origin/master` at that
time.

That reconciles with `python/uqf_frontend/` appearing on the comparison side:
`uqf-copy` must have been refreshed from the GitHub mirror **after** this
session's work was merged there. It is the mirror's content, reached through a
local clone, rather than the mirror directly.

Confidence: **medium**. The two paths read cleanly; the `(dirty)` and
`(without origin)` annotations are less certain, and the revisions themselves
are still UNREAD.

### The report's own scope caveat — read reliably, and important

> no live testing, dynamic, Airflow service, or TorQ deployment was
> instanced

So all three reports are **static analysis of two filesystems**. Nothing in
them is evidence that anything runs. That matters directly for how the
`Capability Drift` rows should be read: a capability marked present is
*declared* present, not *demonstrated*.

The comparison boundary is stated as the current filesystem in both
directions, covering: q source, Python, lock files, scripts, tests, examples,
schemas, configuration, process registries, and documentation.

### Report 1: the Function Callable Inventory taxonomy, more precisely

Six categories, not the four recorded in instalment 1:

| Category | Meaning as written |
|---|---|
| `identical` | paired callable, same normalised body |
| `primary-only` | declared only by primary |
| `comparison-only` | declared only by comparison |
| `name-differs` | paired callable, names differ |
| `body-differs` | paired callable, normalised body differs |
| `visibility-differs` | export/private evidence differs |
| `unresolved` | repeated declarations, or parser pairing cannot establish a boundary |

`visibility-differs` and `identical` were both missed earlier. `unresolved` is
the one that needs a human by construction — the report says so rather than
guessing a pairing.

### Report 2: `File Drift Report` section list

```
#### Comparison Identity
### Primary Python paths
### Comparison Python paths
### Comparison agent paths
### Primary q source and tests
### Comparison q source and tests
### Extended and Ignored Filesystem context
### Abstract and Risk Review
```

Counts appear alongside several of these (numbers in the low hundreds were
visible but could not be read with confidence, so none are recorded).

### Report 3: `Capability Drift` column order and capability names

Column order, read from the header row:

```
| Category | Capability | Primary state | Comparison state | Classification | Evidence and synchronization action | Confidence |
```

Capability names legible in the first column, with their classification
**not** reliably paired to them and therefore omitted:

- Ingestion
- ETL / backfill coverage
- Airflow provider and orchestration
- Frontend API layer
- Crypto fills recorder
- Gateway as default query endpoint
- multitail log viewing
- Process registry
- Repository structure and renames
- Lint and hook scoping
- Docs tree and generated diagrams
- Bounded versus continuous worker contract

Classifications `partial drift`, `primary only`, `comparison only` and
confidences `high` / `low` all appear repeatedly, but pairing a
classification to its row requires reading across a wide table at this
resolution and would be guesswork.

### Still unread after three instalments

- **Every `Capability Drift` row as a row** — the capability names above are
  the first column only. The classification, the synchronisation action, and
  the confidence for each are the actionable content and remain uncaptured.
- Every per-callable entry in report 1.
- All section counts, in all three reports.
- The two revisions being compared.

## Instalment 4 — derived inventory, and the boilerplate transcribed

Two different kinds of content here. The first is **derived from source in
this repository, not read from the photographs** — and is labelled as such,
because presenting a reconstruction as a transcription would be the worst
possible failure in a drift report. The second is genuine transcription of
the report's repeated boilerplate, which is legible precisely because it
repeats.

### Derived: the comparison-only Python inventory, with checkable totals

The report places the whole of `python/uqf_frontend/` on the comparison side.
That package was written in this session, so its callable inventory can be
extracted from source exactly rather than squinted at. **This is a
derivation. The report's own numbers are the authority; these exist so the
two can be compared in about thirty seconds.**

| Group | classes | public fns | private fns | tests | total |
|---|---|---|---|---|---|
| `uqf_frontend/src` — comparison-only | 41 | 50 | 25 | 0 | **116** |
| `uqf_frontend/tests` — comparison-only | 1 | 10 | 5 | 118 | **134** |
| `scripts/check_hook_scopes.py` — comparison-only | 0 | 3 | 0 | 0 | **3** |
| **comparison-only Python, this session** | | | | | **253** |
| `torq_orchestrator/src` — present both sides | 3 | 84 | 55 | 0 | 142 |
| `torq_orchestrator/tests` — present both sides | 0 | 2 | 0 | 64 | 66 |

q side, comparison-only or body-differs — named lambdas only, excluding
lambdas passed inline:

| File | named lambdas |
|---|---|
| `scripts/torq_pipeline.q` | 8 |
| `scripts/torq_markout_etl.q` | 2 |
| `scripts/torq_posbook_etl.q` | 3 |
| `scripts/torq_fx_trades_feed.q` | 1 |
| total | **14** |

**How to use this.** If the report's `comparison-only` count for Python is
near **253**, the two views agree and the inventory can be trusted without
transcribing it. If it is materially different, that gap is itself the
finding — most likely a definition mismatch: whether nested functions,
pydantic model classes (there are 15 in `models.py` alone), dataclasses, or
test functions each count as a callable.

`torq_orchestrator` is the interesting case: it exists on **both** sides, so
its 208 callables should appear as `identical`, `body-differs` or
`visibility-differs` rather than as one-sided. The `Pipeline` registry added
here replaced nine literal row dicts and rewrote the schema generator, so
`body-differs` on `core.py` is expected — and `_pipeline_rows`,
`_resolved_offsets`, `Pipeline` and `Process` should show as
`comparison-only` within an otherwise-shared file.

### Transcribed: the report's repeated boilerplate

These sentences recur verbatim across hundreds of entries, which is why they
are legible where the surrounding paths are not:

- "Callable exists only in primary; package entry points/imports and callers
  must be present in primary."
- "Callable exists only in comparison; package entry points/imports and tests
  are not present in primary."
- "Callable exists only in primary; its implementation and any
  published/state contract are primary-side behaviour."
- "Keep primary side-local."
- "Keep comparison side-local or port manually."
- "Keep primary paradigm; callers must be migrated together."
- "Pairing is unresolved because repeated name declarations are not …"
  (tail UNREAD)

Evidence kinds, each followed by a `path:line`:

```
caller/reference    test    doc/example/script    package entry
```

### What a reader should take from four instalments

The three reports' **structure, taxonomy and dispositions are now captured
well enough to act on**, and the comparison-only inventory can be verified
against source. What remains missing is per-row detail — above all the
`Capability Drift` rows, where classification, synchronisation action and
confidence are the whole point.

Those rows are prose-shaped rather than path-shaped, so unlike the callable
lists they *are* photographable at a readable density: roughly 30–40 rows per
screen rather than 2,000 lines. That is the one remaining capture worth
attempting by photograph.

<!-- INSTALMENT 5 — append here -->
