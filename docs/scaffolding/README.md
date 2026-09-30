# Scaffolding

`uqs job new` writes the skeleton of a process: its q files, its table, its
registry entry and a failing test. Five shapes, one page each. Every command
below was run against this tree, and the output shown is what it printed.

## Which shape

The question that picks it is **where the rows come from**, not what you do with
them:

  |                                         | rows come from      | shape          | guide                          |
  | ---                                     | ---                 | ---            | ---                            |
  | nothing — you make them                 | a timer             | **feed**       | [feed.md](feed.md)             |
  | one or more plant tables                | a subscription      | **etl**        | [etl.md](etl.md)               |
  | several tables that mean the same thing | a subscription each | **normalizer** | [normalizer.md](normalizer.md) |
  | outside the stack, for a past window    | a query you write   | **backfill**   | [backfill.md](backfill.md)     |
  | a window a backfill just published      | its publication     | **reaction**   | [reaction.md](reaction.md)     |

The first three are **streaming**: long-running processes that subscribe,
compute and republish forever. The fourth is **bounded**: it takes a window from
the environment, fills it, records coverage and exits. Picking the wrong one is
the only structural mistake here that is expensive to undo, which is why the
question is asked here, before anything is written.
[new-pipeline.md](../guides/new-pipeline.md) takes the two shells apart once you
have chosen.

`--kind` names three of them --- `streaming` (the default), `normalizer`,
`backfill`. There is no `--kind feed`: a streaming job that subscribes to
nothing *is* a feed, and the scaffold derives that rather than asking twice. A
reaction is `--triggered-by DATASET`, and is the one shape with no process of
its own: it runs inside the process that publishes `DATASET`.

## What every shape has in common

The four with a process write the same six changes, and the reason each exists
is the same across shapes. A reaction writes only the first and the last two: it
has no table.

```
create src/etl/<streaming|sources+workers>/<name>.q   the job itself
append to scripts/processes/uqs_tables.q              its table
append to tests/q/test_stack_tables.q                 that table's name
append to scripts/processes/uqs_catalog.q             a SCAFFOLDED description
create tests/q/test_<name>.q                          a test that FAILS
append to tests/run_tests.q                           the test's namespace
```

Then it regenerates `docs/man.q` and the process table itself --- you do not run
those.

**It writes the shape, never the logic.** The generated handler throws and the
generated test fails, on purpose. A scaffold that left something green behind
would make "generated" and "implemented" look the same from outside, which is
how you get a process that is `up`, heartbeating, and publishing nothing.

**Four things stay yours**, and each is gated so you cannot forget:

  |                              | what fails until you do it                          |
  | ---                          | ---                                                 |
  | the handler body             | `test_<name>_is_implemented`                        |
  | the test                     | the same one — delete it when you write a real test |
  | the catalog description      | `test_no_scaffold_left.py`                          |
  | one line of stack-page prose | a pytest check on `docs/architecture/stack.md`      |

A streaming job also lands in **no profile**, so `uqs start --profile` cannot
reach it. The scaffold says so; `test_profiles.py` fails until it is in one or
in `UNPROFILED` with a reason. Backfills are exempt --- see
[backfill.md](backfill.md).

## Before you run it

`--dry-run` prints the plan and writes nothing. Every example here was checked
that way first, and it costs nothing to do the same:

```bash
uqs job new <name> ... --dry-run
```

To take a scaffold back out - a typo in the name, a wrong shape - run
`uqs job remove <name>`. It works out every file and line the scaffold wrote
from the tree, keeps any table or source another job still uses, and refuses a
job that has been written (its SCAFFOLDED markers are gone) unless `--force`.

## After you run it

The order that gives the shortest feedback loop, from [the new-job
skill](../../.claude/skills/new-job/SKILL.md):

1. the handler body
2. the test, replacing the stub entirely
3. the docstrings --- the `man-registry` pre-commit hook regenerates
   `docs/man.q` from them

Then `scripts/test.py q-unit`, and `scripts/test.py stack-smoke` against a real
stack, which is the only thing that proves the wiring.
