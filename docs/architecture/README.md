# Architecture

Decisions that cut across services: why the tree is shaped the way it is,
rather than what it does or how to run it. A page here should still be worth
reading after the code it describes has been rewritten — if it goes stale the
moment a function is renamed, it belongs in [`reference/`](../reference/) or
beside the code as a qDoc block.

## Kinds of page

They are not all the same sort of document, and reading one as though it were
another is the easiest way to be misled by this directory.

| Kind | What it is for | Pages |
|---|---|---|
| **Positions** | the beliefs the code is arranged around, still held | [`pipeline-philosophy.md`](pipeline-philosophy.md) |
| **Design notes, kept after the build** | written *before* the work and kept for the reasoning, not as a statement of what is missing | [`event-tape.md`](event-tape.md) |
| **Closed assessments** | a question asked, answered, and settled — kept so the answer is not re-derived | [`pipeline-framework-gaps.md`](pipeline-framework-gaps.md) |
| **One decision** | a single yes/no, with the reason it went that way | [`cryptorust-discovery.md`](cryptorust-discovery.md) |
| **Synthesis** | the implemented pieces seen as one system | [`pipeline-architecture-example.md`](pipeline-architecture-example.md) |
| **The running stack** | what runs, how it is wired, and why not all of it starts - its process names checked against the registry by a test | [`stack.md`](stack.md) |

## What is here

| Page | In one line |
|---|---|
| [`pipeline-philosophy.md`](pipeline-philosophy.md) | the ten positions `src/etl/` is built on, and what enforces each |
| [`pipeline-architecture-example.md`](pipeline-architecture-example.md) | a desk system composed from the implemented services, in seven bands |
| [`pipeline-framework-gaps.md`](pipeline-framework-gaps.md) | what a Dagster-shaped framework needs, what closed each gap, and what is deliberately absent |
| [`event-tape.md`](event-tape.md) | the per-event order/trade tape: which tape this is, which it is not, and the features it unblocked |
| [`cryptorust-discovery.md`](cryptorust-discovery.md) | why a non-listening process belongs in TorQ's client table and not its server one |
| [`stack.md`](stack.md) | the running stack: process topology, the connection budget, the data pipeline table by table, and config generation |

## A page states its own status, and the two kinds that need one differ

The design note and the closed assessment each open with where they
stand, because a reader who takes a *design note* for a *description of the
code* will look for something that was never built — or, worse, rebuild it:

- **`event-tape.md`** — the contract is decided and the shape implemented;
  five of the six features it unblocks exist.
- **`pipeline-framework-gaps.md`** — **closed**. Every gap it found has been
  built or decided against.

The others carry no status and should not: positions are held until
they are argued out of, a single decision is true or it is revisited, and a
synthesis or the stack page describes whatever is implemented when you read
it.

## Where a page does not belong here

- *How do I run this?* → [`guides/`](../guides/) and
  [`services/`](../services/)
- *What is the contract, and what does CI hold the code to?* →
  [`reference/`](../reference/)
- *How do I create one of these?* → [`scaffolding/`](../scaffolding/)
- *Which process listens on which port?* →
  [`reference/processes.md`](../reference/processes.md), generated from the
  registry

A design note here is kept *current*: where it and the code disagree, the
code is right and the note is corrected. That is the opposite of a migration
plan, which is a record of what was believed before the work — `docs/`
carried two of those and no longer does, because both planned alignment with
an upstream that has since been frozen.
