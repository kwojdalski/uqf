# Documentation map

## The system, on one page

<!-- Source: docs/diagrams/repo-overview.d2. Rendered by
     scripts/generate/render_diagrams.py; edit the .d2 and re-render, never the
     .svg. Its --check only compares where the pinned d2 is installed, which CI
     is not. -->

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

| Directory | Answers | Audience |
|---|---|---|
| [`guides/`](guides/) | *How do I do this?* | operators and new developers |
| [`services/`](services/) | *What does this running service do, and how do I run it?* | operators and developers of that service |
| [`architecture/`](architecture/) | *Why is it shaped this way?* | developers changing it |
| [`reference/`](reference/) | *What is the contract?* | anyone integrating, and CI |
| [`integrations/`](integrations/) | *How does this meet something external?* | operators |

## What is where

**`guides/`** — [`uqs.md`](guides/uqs.md) (running the stack),
[`new-pipeline.md`](guides/new-pipeline.md) (adding a pipeline with
`uqs new-job`: a streaming job, or a source and a bounded worker, end to end),
[`config-audit.md`](guides/config-audit.md) (recording runtime
configuration changes, and joining them to who made them),
[`metatables.md`](guides/metatables.md) (partition profiling),
[`ci.md`](guides/ci.md) (what the gates do and how to run them locally).

**`services/`** — one page per running service, indexed in
[`services/README.md`](services/README.md):
[`synthetic-feeds.md`](services/synthetic-feeds.md) (`fxfeed1` and
`quotesfeed1`), [`superbook.md`](services/superbook.md) (the direct FX
superbook and its arbitrage), [`cross-arbitrage.md`](services/cross-arbitrage.md)
(the direct book against a synthetic route),
[`fx-positions.md`](services/fx-positions.md),
[`databento.md`](services/databento.md) (a live market-data feed),
[`crypto-recorder.md`](services/crypto-recorder.md) (the cryptorust
recorders) and [`tap.md`](services/tap.md) (`tap1`, printing every row that
lands). A service page is where its design decisions live too;
`architecture/` is for decisions that cut across services.

**`architecture/`** — [`pipeline-philosophy.md`](architecture/pipeline-philosophy.md)
(the positions `src/etl/` is built on, and what enforces each),
[`restatement-design.md`](architecture/restatement-design.md)
(the bitemporal design, now built),
[`event-tape.md`](architecture/event-tape.md) (the order/trade event tape:
its contract, and the microstructure features it unblocked),
[`pipeline-framework-gaps.md`](architecture/pipeline-framework-gaps.md) (a
closed assessment against a Dagster-shaped framework: what closed each gap,
and what is deliberately absent),
[`cryptorust-discovery.md`](architecture/cryptorust-discovery.md) (why a
non-listening process belongs in the client table, not the server one),
[`example-architecture.md`](architecture/example-architecture.md) (a desk
system composed from the implemented services, in six bands).

**`reference/`** — [`quant-modules.md`](reference/quant-modules.md) (what
each module under `src/` is for, its namespace, and the conventions all of
them follow), [`environment.md`](reference/environment.md) (every
variable; `scripts/gates/check_env_reference.py` fails CI when the page and
the code disagree, in either direction),
[`etl-framework-requirements.md`](reference/etl-framework-requirements.md)
(ETL-nn) and [`frontend-requirements.md`](reference/frontend-requirements.md)
(FE-nn), the requirement ids the tests and code cite.

**`integrations/`** — [`torq/README.md`](integrations/torq/README.md) (the
running stack: process topology, the data pipeline table by table, and config
generation) and [`torq/processes.md`](integrations/torq/processes.md) (the
process table, generated from the job declarations).

### Also in `docs/`

- [`man.q`](man.q) — the function registry, generated from the qDoc comments
  under `src/` by `scripts/generate/generate_man_registry.py`. CI fails when
  it is out of date.
- [`diagrams/`](diagrams/) — the d2 source of every diagram in these pages
  and its rendered SVG, each embedded in the page it illustrates.
  `scripts/generate/render_diagrams.py --check` reports an SVG that no longer
  matches its source, but only where the pinned d2 version is installed; CI
  has no d2, so there it skips.
- [`migrations/`](migrations/) — migration plans, written before the work
  they plan and kept as its reasoning, and `surfaces/uqf-local/`, this tree's exported
  contract surface (`scripts/generate/contract_surface.py`), which
  `check_doc_references.py` uses as its list of functions that exist.

### Outside `docs/`

- At the root: [`README.md`](../README.md) (what the repository is, and a
  quick start), [`CHANGELOG.md`](../CHANGELOG.md),
  [`LICENSING.md`](../LICENSING.md) (per-dependency licensing) and
  [`CLAUDE.md`](../CLAUDE.md) (working rules for coding agents in this repository).
- Component READMEs: [`python/uqs/`](../python/uqs/README.md) (the `uqs`
  CLI and MCP server that runs the stack),
  [`python/uqf_frontend/`](../python/uqf_frontend/README.md) (the
  backend-for-frontend over the gateway), [`web/`](../web/README.md) (the
  browser application on top of it),
  and [`python/uqf_airflow_provider/`](../python/uqf_airflow_provider/README.md)
  (the backfill status files as Airflow sensors).
  [`python/uqf_client/`](../python/uqf_client/), the Python/Polars client
  over kdb+ IPC, has no README yet.
