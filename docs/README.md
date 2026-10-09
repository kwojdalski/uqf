# Introduction

## System overview

<!-- Source: docs/diagrams/repo-overview.d2. Rendered by
     scripts/generate/render_diagrams.py; edit the .d2 and re-render, never the
     .svg. Its --check only compares where the pinned d2 is installed, which CI
     is not. -->

![uqf on one page, top to bottom: the outside world (live feeds, batch sources, Airflow, the browser); src/, plain q that never loads TorQ; the TorQ runners in scripts/processes; and the running stack - with the Python and web surfaces alongside](diagrams/repo-overview.svg)

Read it in two columns. The left is the pipeline, top to bottom: a declaration
in `src/etl/` names a source, a worker or a streaming job; the framework in
`src/etl/core/` runs it; a script in `scripts/` puts it on the tickerplant. The
right is everything that talks to that pipeline without being part of it.

The line worth knowing is the one between the first two bands. **No q file under
`src/` knows TorQ exists** --- that is what lets a worker be tested against a
recorder instead of a tickerplant --- and exactly one namespace is allowed to,
`.qtorq` in `scripts/`. `scripts/gates/check_etl_layering.py` fails the build if
anything under `src/etl/` reaches for it. The core does *read* three of TorQ's
facilities when they are there - logging, connected services and the process
name - each from one owner file and with a plain-q fallback, and the same gate
holds each read to its file.

For the *running* stack --- who connects to whom --- see
[`architecture/stack.md`](architecture/stack.md), and for ports the generated
[`reference/processes.md`](reference/processes.md); this diagram deliberately
stops at the shape.

## Where a document goes

Five directories, one question each. The rule is what a document *is for*, not
what it is about --- a page about the ETL framework can be a guide, a reference
or an architecture note, and only its purpose decides where it goes.

  | Directory                                 | Answers                                                   | Audience                                                              | Pages                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | ---                                       | ---                                                       | ---                                                                   | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | [`guides/`](guides/README.md)             | *How do I do this?*                                       | operators and new developers                                          | [`uqs.md`](guides/uqs.md) running the stack<br>[`when-it-breaks.md`](guides/when-it-breaks.md) a failed backfill, a dead streaming job<br>[`new-pipeline.md`](guides/new-pipeline.md) adding a pipeline, end to end<br>[`config-audit.md`](guides/config-audit.md) who changed runtime config<br>[`metatables.md`](guides/metatables.md) partition profiling<br>[`deploy.md`](guides/deploy.md) deploying to a server<br>[`odbc.md`](guides/odbc.md) ODBC sources on a server<br>[`real-time-subscribers.md`](guides/real-time-subscribers.md) following a table live from outside                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
  | [`scaffolding/`](scaffolding/README.md)   | *How do I create one of these?*                           | developers adding a process                                           | one per `uqs job new` shape: [`feed.md`](scaffolding/feed.md), [`etl.md`](scaffolding/etl.md), [`normalizer.md`](scaffolding/normalizer.md), [`backfill.md`](scaffolding/backfill.md), [`reaction.md`](scaffolding/reaction.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | [`services/`](services/README.md)         | *What does the stack run, and what does each process do?* | anyone new to the stack, then operators and developers of one process | a showcase of every process, one short card each, linking to a full page where one exists: [`synthetic-feeds.md`](services/synthetic-feeds.md), [`superbook.md`](services/superbook.md), [`cross-arbitrage.md`](services/cross-arbitrage.md), [`fx-positions.md`](services/fx-positions.md), [`databento.md`](services/databento.md), [`kafka.md`](services/kafka.md), [`crypto-recorder.md`](services/crypto-recorder.md), [`tap.md`](services/tap.md), [`markouts.md`](services/markouts.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | [`architecture/`](architecture/README.md) | *Why is it shaped this way?*                              | developers changing it                                                | [`pipeline-philosophy.md`](architecture/pipeline-philosophy.md) what `src/etl/` is built on<br>[`event-tape.md`](architecture/event-tape.md) the order/trade event tape<br>[`cryptorust-discovery.md`](architecture/cryptorust-discovery.md) a client that never listens<br>[`pipeline-architecture-example.md`](architecture/pipeline-architecture-example.md) Desk System, composed<br>[`stack.md`](architecture/stack.md) the running stack and how it is wired                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
  | [`reference/`](reference/README.md)       | *What is the contract?*                                   | anyone integrating, and CI                                            | [`quant-modules.md`](reference/quant-modules.md) each `src/` module<br>[`environment.md`](reference/environment.md) every variable, gated<br>[`pipeline-declarations.md`](reference/pipeline-declarations.md) every pipeline declaration key, gated<br>[`processes.md`](reference/processes.md) every process, its port and edges, generated<br>[`surfaces/current/`](reference/surfaces/current/) the exported contract surface, generated                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

### Also in `docs/`

- [`faq.md`](faq.md) --- short answers to the questions asked most, starting
  with how this differs from plain TorQ, each pointing to the page that has the
  long one.
- [`man.q`](man.q) --- the function registry. It reads the qDoc comments under
  `src/` when it loads, so it cannot fall out of date.
- [`presentation/`](presentation/uqf.qmd) --- a short Quarto deck, mostly these
  diagrams, on how uqf differs from running TorQ directly. Render it with
  `quarto render docs/presentation/uqf.qmd`; the HTML is not committed.
- [`diagrams/`](diagrams/) --- the d2 source of every diagram in these pages and
  its rendered SVG, each embedded in the page it illustrates.
  `scripts/generate/render_diagrams.py --check` reports an SVG that no longer
  matches its source, but only where the pinned d2 version is installed; CI has
  no d2, so there it skips.
