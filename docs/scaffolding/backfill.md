# Scaffolding a bounded worker

A backfill fills a **past window** from a source outside the stack. It takes
its range as flags (`uqs backfill`), fetches, transforms, publishes, records
coverage, and **exits**. It is not a long-running process and it never
subscribes to the tickerplant.

That last point is why it is the odd one out here: everything on the
[feed](feed.md), [etl](etl.md) and [normalizer](normalizer.md) pages is about
a process that stays up.

## The command

```bash
uqs new-job fxprobe --kind backfill --dataset fx_probe \
    --columns "sym:symbol, mid:float" --width 1D
```

`--dataset` is the table it fills, and it is **required** — a backfill with no
dataset has nothing to record coverage against. `--width` is the window size
as a q timespan.

```
scaffold fxprobe_backfill:
  create src/etl/sources/fxprobe.q (56 lines)
  create src/etl/workers/fxprobe_backfill.q (28 lines)
  append to scripts/processes/uqs_tables.q (3 lines)
  append to tests/q/test_stack_tables.q (1 line)
  create tests/q/test_fxprobe_backfill.q (13 lines)
  append to tests/run_tests.q (1 line)
  append to scripts/processes/uqs_catalog.q (2 lines)
  note: write .qfeed.fxprobe.query - parameterised, never concatenated (see src/etl/core/source_contract.q)
  note: write .qfeed.fxprobe.fixture - deterministic, same contract as the live source
  note: declared fields: time, sym, mid
  note: the window is half-open [from;to): >= on the lower bound, < on the upper
```

**Two q files, not one**, and the process is named for the worker
(`fxprobe_backfill1`) rather than the source. The split is the point: a
*source* says where rows come from and what shape they are; a *worker* says
which source, which dataset, how wide a window. One source can have several
workers.

**No profile note.** Profiles are standing start sets and a backfill is
triggered — it holds no plant connection and belongs to no profile. That is
the same distinction `profiles.plant_slots` draws when it counts the budget.

## The source — `src/etl/sources/fxprobe.q`

```q
\d .qfeed.fxprobe

source_name:`fxprobe

/ The columns this adapter READS - not everything the source has. Declaring
/ one the worker never touches means an upstream change to an unused column
/ breaks the run.
fields:`time`sym`mid
```

Two things to write, and both notes are warnings earned the hard way:

**`query` — parameterised, never concatenated.** The window bounds
are *arguments* to a lambda taking `(handle; range_from; range_to)` and
evaluated remotely, not text spliced into a string. Where a driver genuinely
cannot parameterise there is exactly one escape function, `.qodbc.literal`
(`src/etl/core/singlestore_odbc.q`); using anything else is the finding a
security review exists to make.

It is the frontend's guarantee that no caller input reaches query text,
and [`source_contract.q`](../../src/etl/core/source_contract.q) applies it
here on the grounds that *"a source adapter is the same problem with a less
friendly input"*. The rule has no `ETL-nn` of its own — the scaffold cited
one for months, and it resolved to the interval rule below.

**The window is half-open `[from;to)` (ETL-08)** — `>=` on the lower bound,
`<` on the upper. Include the start, exclude the end, reject empty and
reversed intervals, compose adjacent windows only at their common boundary.
One wrong operator double-publishes every boundary row, and the duplicate
surfaces far from here, in a number that is quietly too big.

Two rules, two numbers, both on `query`. Keeping them apart is why the
generated file states them in separate paragraphs.

## The fixture is neither a throw nor empty

```q
fixture:{[]
    ([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `SCAFFOLD; mid:enlist 1.0)}
```

The generated file explains itself, and it is worth reading before replacing
it. Every other scaffolded body throws; this one cannot:

- the worker's `.qxf.passthrough` call reads it **at load time**, so a fixture
  that threw would stop the whole ETL tree from loading — you could not run
  the suite to see what was unfinished;
- empty does not work either, because `.qxf.define` refuses a transform whose
  examples are all empty.

So it is one deterministic row of the declared shape, which loads and asserts
nothing. **Replace it before trusting a run** — a fixture that does not
exercise what the source actually does makes the suite green for no reason.

## The worker — `src/etl/workers/fxprobe_backfill.q`

Mostly a declaration. The lifecycle — windowing, retries, coverage,
checkpoints, dry-run — is `.qbw`'s:

```q
facts:{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    (enlist `rows)!enlist count batch}

.qxf.passthrough[`fxprobe_passthrough;`batch;0#.qfeed.fxprobe.fixture[];.qfeed.fxprobe.fixture[]];

.qbw.define[`fxprobe_backfill;
    `source`dataset`width`transform`facts`procname`note!
        (`fxprobe;`fx_probe;1D;`fxprobe_passthrough;.qwrk.fxprobe_backfill.facts;
         `fxprobe_backfill1;
         "SCAFFOLDED: bounded - say what this backfill is for")];
```

**If you find yourself writing a loop over days, you are rebuilding `.qbw`.**

`facts` is what the run saw beyond its row count — a row count alone reads a
partial extract as success. **Every aggregate must survive an empty batch**:
a zero-row window is legal and is recorded deliberately (ETL-07), because
"ran, found nothing" and "never ran" must not look the same in the coverage
ledger.

## Running it

A backfill takes its window as flags and registers with discovery, so the
fleet has to be up:

```bash
uqs backfill fxprobe_backfill --version v1 --from 2026-09-13 --to 2026-09-15
```

All three are required: `uqs` refuses without them, and the process itself
refuses and names every missing flag at once, because a backfill that
silently defaulted its range would publish the wrong window and record
coverage for it.

## Then

Write `query`, then `fixture`, then the test — and check what it claims with
`.qmatz` coverage reads rather than by trusting the row count.
[new-pipeline.md](../guides/new-pipeline.md) walks the whole bounded
lifecycle end to end with a real worked example.
