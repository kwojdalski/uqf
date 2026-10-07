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
  | outside q: a broker, a vendor's stream  | a Python publisher  | **external**   | [external.md](external.md)     |

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

For a backfill taken all the way from scaffold to a filled database, see the
worked example [from one kdb+ database to another](hdb-transfer.md). One script
builds a source HDB, runs the scaffolded job into a second HDB and checks the
result.

## What every shape has in common

The four with a process write the same eight changes, and the reason each exists
is the same across shapes. A reaction writes only the first and the fifth and
sixth: it has no table and no process.

```
create src/etl/<streaming|sources+workers>/<name>.q   the job itself
append to src/etl/plant_tables.q              its table
append to tests/q/test_stack_tables.q                 that table's name
append to scripts/processes/uqs_catalog.q             a SCAFFOLDED description
create tests/q/test_<name>.q                          a test that FAILS
append to tests/run_tests.q                           the test's namespace
append to docs/services/README.md                     a SCAFFOLDED Showcase card
append to docs/architecture/stack.md                  a SCAFFOLDED comment naming the process
```

A backfill also gets `scripts/examples/<name>_example.q`, which runs the worker
once on its fixture into a throwaway HDB under `build/` - no stack - and exits 1
unless it published a row. Run it straight after scaffolding:
`q scripts/examples/<name>_example.q`.

Then it regenerates `docs/man.q` and the process table itself --- you do not run
those.

**It writes the shape, never the logic.** The generated handler throws and the
generated test fails, on purpose. A scaffold that left something green behind
would make "generated" and "implemented" look the same from outside, which is
how you get a process that is `up`, heartbeating, and publishing nothing.

**Four things stay yours**, and each is gated so you cannot forget:

  |                                           | what fails until you do it                          |
  | ---                                       | ---                                                 |
  | the handler body                          | `test_<name>_is_implemented`                        |
  | the test                                  | the same one — delete it when you write a real test |
  | the catalog description                   | `test_no_scaffold_left.py`                          |
  | the Showcase card and the stack-page line | `test_no_scaffold_left.py`                          |

A streaming job also lands in **no profile**, so `uqs start --profile` cannot
reach it. The scaffold says so; `test_profiles.py` fails until it is in one or
in `UNPROFILED` with a reason. Backfills are exempt --- see
[backfill.md](backfill.md).

## Table columns

`--columns` spells a new table as `name:type` pairs, and each is written into
`plant_tables.q` as the q column it stands for:

```
--columns "sym:symbol, venue:g#symbol, px:float, bid_prices:list"
```

`sym` is grouped (`` `g#`symbol$() ``) without asking. Any other column is
grouped with `g#` before its type, and `list` is a general column, the
vector-valued kind `fx_orderbook` uses. The types are `timestamp`, `symbol`,
`float`, `long`, `int`, `short`, `boolean`, `char`, `date`, `time`, `timespan`
and `list`, and `time` is added first when you leave it out. What each one
means, its `meta` character and the sample value a fixture gets, is decided in
one place, `python/uqs/src/uqs/scaffold/columns.py`. A test holds it to the
`meta` q itself reports.

When the new table is shaped like one the plant already has, copy it instead:

```bash
uqs job new spread2 --subscribe-to quote --publishes spread2 --columns-from fx_orderbook
```

`--columns-from TABLE` takes that table's columns exactly, attributes included,
from `plant_tables.q` or the vendored `database.q`, and is refused alongside
`--columns`.

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

It only knows the files a scaffold writes, so it also lists, with line numbers,
every other line that still names the job, its process, or a source or table it
removes - an example script, a docs page, a diagram, another suite's test. Those
it leaves alone; `--strict` refuses to remove anything while any remain.

## After you run it

The order that gives the shortest feedback loop, from [the new-job
skill](../../.claude/skills/new-job/SKILL.md):

1. the handler body
2. the test, replacing the stub entirely
3. the docstrings --- `docs/man.q` reads them when it loads

Then `scripts/test.py q-unit`, and `scripts/test.py stack-smoke` against a real
stack, which is the only thing that proves the wiring.
