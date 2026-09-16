# Documentation map

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

**`guides/`** — [`torq-demo.md`](guides/torq-demo.md) (running the stack),
[`ci.md`](guides/ci.md) (what the gates do and how to run them locally).

**`architecture/`** — [`restatement-design.md`](architecture/restatement-design.md)
(D-11's bitemporal design, agreed and not yet built),
[`event-tape.md`](architecture/event-tape.md).

**`reference/`** — [`environment.md`](reference/environment.md) (every
variable, machine-checked),
[`etl-framework-requirements.md`](reference/etl-framework-requirements.md)
(ETL-nn), [`frontend-requirements.md`](reference/frontend-requirements.md)
(FE-nn). These are the pages CI holds the code to, which is why they are a
category rather than prose.

**`decisions/`** — [the register](decisions/README.md) plus one page per
decision, both **generated** by `scripts/build_decision_log.py` from the
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

## Adding a document

Ask which of the five questions it answers. If the answer is "none of them",
it probably belongs at the top level with a line added here saying why —
which is a deliberately small amount of friction, because an unexplained file
at the root is how a taxonomy stops meaning anything.

If a document answers two of the five, it is two documents.
