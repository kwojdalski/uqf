# Documentation map

## The system, on one page

<!-- Source: docs/diagrams/repo-overview.d2. Rendered by
     scripts/generate/render_diagrams.py, which CI runs with --check: edit the .d2
     without re-rendering and the build fails. Do not edit the .svg. -->

![The q pipeline, top to bottom: declarations, the framework in src/etl/core, the TorQ adapter in scripts, and the running stack - with the outside world and the Python and web surfaces alongside it](diagrams/repo-overview.svg)

Read it in two columns. The left is the pipeline, top to bottom: a
declaration in `src/etl/` names a source, a worker or a streaming job; the
framework in `src/etl/core/` runs it; a script in `scripts/` puts it on the
tickerplant. The right is everything that talks to that pipeline without
being part of it.

The line worth knowing is the one between the first two bands. **No q file
under `src/` knows TorQ exists** — that is what lets a worker be tested
against a recorder instead of a tickerplant — and exactly one namespace is
allowed to, `.qpipe` in `scripts/`. `scripts/gates/check_etl_layering.py` fails
the build if anything under `src/etl/` reaches for it.

For the *running* stack — who connects to whom, with ports — see
[`integrations/torq/README.md`](integrations/torq/README.md); this diagram
deliberately stops at the shape.

## Where a document goes

Five directories, one question each. The rule is what a document *is for*,
not what it is about — a page about the ETL framework can be a guide, a
reference or an architecture note, and only its purpose decides where it goes.

| Directory | Answers | Audience (J-08) |
|---|---|---|
| [`guides/`](guides/) | *How do I do this?* | operators and new developers |
| [`architecture/`](architecture/) | *Why is it shaped this way?* | developers changing it |
| [`reference/`](reference/) | *What is the contract?* | anyone integrating, and CI |
| [`decisions/`](decisions/) | *What was decided, and when?* | reviewers and future maintainers |
| [`integrations/`](integrations/) | *How does this meet something external?* | operators |

## What is where

**`guides/`** — [`uqf-stack.md`](guides/uqf-stack.md) (running the stack),
[`new-pipeline.md`](guides/new-pipeline.md) (adding a source and a bounded
worker, end to end), [`metatables.md`](guides/metatables.md) (partition
profiling), [`ci.md`](guides/ci.md) (what the gates do and how to run them
locally).

**`architecture/`** — [`pipeline-philosophy.md`](architecture/pipeline-philosophy.md)
(the positions `src/etl/` is built on, and what enforces each),
[`restatement-design.md`](architecture/restatement-design.md)
(D-11's bitemporal design, now built),
[`event-tape.md`](architecture/event-tape.md),
[`pipeline-framework-gaps.md`](architecture/pipeline-framework-gaps.md) (what
`src/etl/` has and lacks relative to a Dagster-shaped framework),
[`cryptorust-discovery.md`](architecture/cryptorust-discovery.md) (why a
non-listening process belongs in the client table, not the server one).

**`reference/`** — [`environment.md`](reference/environment.md) (every
variable, machine-checked),
[`etl-framework-requirements.md`](reference/etl-framework-requirements.md)
(ETL-nn), [`frontend-requirements.md`](reference/frontend-requirements.md)
(FE-nn). These are the pages CI holds the code to, which is why they are a
category rather than prose.

**`decisions/`** — [the register](decisions/README.md) plus one page per
decision, both **generated** by `scripts/generate/build_decision_log.py` from the
GitHub issue comments that are the authority. Do not edit either by hand:
`--check` runs in CI and reports a stale page, a missing one, *and* a page
that corresponds to no answer.

**`integrations/`** — [`torq/`](integrations/torq/), including the generated
process table and dataflow diagram.

## What deliberately stays at the top level

Not everything is one of the five, and forcing it would be worse than the
exception:

- **[`ROADMAP.md`](ROADMAP.md)** — a plan, which is neither a guide nor a
  reference. It describes what does not exist yet, so filing it under either
  would mislead.
- **`audits/`**, **`prompts/`** — agent output and agent input, dated and
  append-only. They are a record of a run rather than documentation of the
  system.
- **`drift-reports/`** — provenance. Closed as a document under A-03, when
  canonical was frozen and this tree became the primary lineage; kept because
  deleting it would erase the history of how this repository got here.
- **`migrations/`** — plans for restructurings, written before the work and
  kept afterwards as the reasoning behind it.
- **`diagrams/`** — d2 sources and their rendered SVGs. Not one of the five
  because a diagram is not a document: it illustrates one, and the page it
  illustrates is where the words live. Edit the `.d2` and run
  `python3 scripts/generate/render_diagrams.py`; never edit the `.svg`, which
  `--check` re-renders in CI.
