# Markouts and memory: the market is mapped, never copied

Market data can run to tens of terabytes. A markout run must never copy it into
a process heap: its memory grows with its **executions**, which are small, and
not with the market (#2288). This page is the design, what it relies on in q,
and the numbers that show it holds.

## What q does, verified on KDB-X

- `select cols from market_data where date=d` - the date and nothing else in the
  `where` - keeps the columns **memory-mapped**. Adding any other constraint
  (`sym in`, a time range, a source) materialises a copy of every column it
  names. On a 20-million-row partition, the date-only select added about 600
  bytes to the heap and mapped 960MB; the same select with `sym in` added 50MB.
- An as-of join keyed on `sym`, `source`, `source_time` against such a selection
  **does** touch only the pages it needs - but only when it is done in the right
  way. `aj` with three keys groups the right table by `(sym; source)` inside
  each parted `sym` by copying that sym's columns into the heap for the call. At
  100 million rows a day the heap's high-water mark went from 64MB to 192MB
  around one `aj` of 200 probes, and it grows with the sym's rows. A two-key
  `aj` (`sym`, time) does not copy but cannot tell sources apart.
- So `.qetl.mapped.asof` does the search itself: it finds each `(sym; source)`
  run by galloping (a few elements, never a column) and runs every probe's
  binary search over its run's `source_time` in lockstep, `log2(longest run)`
  rounds of one gather each. It returns exactly what `aj` returns -
  `tests/q/test_mapped_books.q` holds the two equal on a market of three days,
  three sources and withdrawn sides - and its heap, and its high-water mark, do
  not move with the number of rows.

## The layout it relies on

- **Flat top of book.** `market_data` carries `bid` and `ask`: the level-0 sides
  as floats, null for a side the source withdrew or a level that is not a price
  (`.qf.mkt.top_sides`' rule, applied once at write time by
  `.qetl.plant.with_flat`, which every mapping calls). The ladders stay beside
  them, mapped and untouched: a markout reads none.
- **`rid`.** The row's position in its partition as sorted. A match returns the
  `rid` of each book, and `.qetl.mapped.depth_at` reads a mapped ladder at those
  rows only - never the day's - and refuses a `rid` whose row does not carry the
  flat top the match did. Nothing on a markout's path calls it (a gate refuses
  it); it is the other half of the column.
- **Sorted.** By `sym`, `source`, `source_time`, with `p#` on `sym`. uqf's own
  finish (`.qetl.io.finish_parts`) writes it from the plant's declared sort
  rule, and TorQ's end-of-day sort writes it from `scripts/torqconfig/sort.csv`
  (KDBSERVCONFIG puts that file ahead of `lib/torq`'s, which is never edited),
  after which `scripts/processes/uqs_row_ids.q` numbers the rows.
  `uqs data compress --report` reads the same `sort.csv`, so it names any
  partition not in this order.
- A `(sym; source)` is on one clock: the clock belongs to the feed that writes
  the source.

## One date at a time

`.qetl.mapped.tob_at` runs where the partitions are mapped - hdb1 loads it, or a
process that loaded an HDB - and is sent the probes: each leg of each execution
at the execution's instant and at every horizon. For each date within the
stored-date skew of a probe it reads the date's columns with a date-only select
and asks `asof`; probes no book answers go on to the earlier dates, down to the
reference's age limit, one date-only read each (the book before midnight that
prices the day's first execution). It returns the distinct matched books: memory
proportional to the probes. The three bounded sources (`hdb_fx_markouts`,
`hdb_crypto_markouts`, `hdb_eq_markouts`) call it; none selects `market_data`
with a `where` beyond the date, and `scripts/gates/check.py markout-mapped`
refuses a markout file that does or that names a depth column.

## The core's input

`markout_core.calc` takes a top-of-book table - `sym`, `source`, `source_time`,
`bid`, `ask`, `clock` - sorted and attributed as above. It **checks** that
(`.qetl.mapped.require_arranged`: the attribute, and the order in full for a
small table, by sampled neighbours for a mapped one) and refuses it otherwise;
it never sorts or copies. `markout_core.score` is the entry the domains'
transforms call: it arranges a small in-memory table first (a live job's buffer
arrives in time order) and hands an arranged one - what a source reads - through
untouched.

## The reference is a declared variant

A markout's reference is `SELECT x TRANSFORM x COMBINE` (`.qetl.reference`):
which books count (venue set, staleness, offset - done by the as-of search over
the flat keys), a pure function of one book, a pure function of the books across
venues. **Variant #1**, `best_top`, is the rule every markout used before: every
venue, the latest book within `max_age`, level 0, the highest bid and lowest
ask. `tests/q/test_reference_variant.q` holds it bit-identical to
`.qf.mkt.best_across_venues`. The spec's `reference` key names the variant, and
a variant other than #1 goes into the run's configuration snapshot and so its
`config_id` (#1999). Variant #1 adds nothing: it is what `contract_version` 1
already means, so every result scored before variants existed, and every one
scored under #1 since, keeps its id.

A markout run uses the spec's one variant; a LIST of them is `.qetl.variant`
(`src/etl/core/variant_run.q`) and `markout_variants.calc`.

### Reference variants at scale (#2289)

Reference prices may be wanted in a thousand ways, and a stored column per way
is impossible at the size market data runs to. A variant is a declaration, so a
list of them factors, and the market is touched once:

1. **SELECT** - per distinct (venue set, offset) the as-of search over the flat
   keys (`tob_at`); the staleness only filters the matched books afterwards. It
   returns the books and their `rid`s.
2. **DEDUPE** - the distinct books across every variant.
3. **GATHER ONCE** - `.qetl.mapped.gather` (in the process that maps
   `market_data`) reads the ladders of those books in one `depth_at` call,
   through their `rid`s: a slice of k rows x depth. `.qetl.mapped.depth_reads`
   counts the rows read and the gather refuses to return unless the count equals
   the distinct books; `scripts/gates/check.py variant-gather` refuses variant
   code that names `market_data`, a second ladder reader, and a gather that
   stops deduping or counting.
4. **TRANSFORM** - each distinct (transform, parameters) once over the slice.
5. **COMBINE** - each variant's, across the books matched to each instant.

The cost is (fills x horizons x venues) x (depth + distinct transforms) and does
not depend on the size of the market or on the number of variants beyond the
transforms and combines themselves.

**Declaring a variant** is one file under `src/etl/core/variants/` (globbed;
nothing else changes) and a test:

```q
.qetl.reference.register[`vwap_5m_best;`venues`stale`offset`transform`combine`params!(
    `all;`spec;0D;`vwap;`best;enlist[`qty]!enlist 5000000f)];
```

The registry validates the spec (venues, staleness, offset, a registered
transform and combine, parameters of the transform's own names and types). The
starter set, beside #1 `best_top`: `micro_median` (micro-price, median across
venues), `vwap_1m_best` (`vwap[1,000,000]`, best across venues), `sizew_mid`
(size-weighted mid across venues), `median_top` (median across venues) and
`depth3_wmean` (top-3 depth-weighted). Each has a hand-computed test in
`tests/q/test_reference_starter.q`. New transforms and combines are
`.qetl.reference.add_transform` / an entry in `.qetl.reference.combines`
(`reference_library.q`).

**Configuration.** Each variant has its own `config_id`: the id of the snapshot
`config_markout` records for the spec naming it
(`.qpipe.transform.markout_variants.ids`, `pin`). Variant #1 adds nothing to its
snapshot, so its id is the id the spec had before variants existed.
`markout_variants.calc` stacks one `markout_core.calc` per variant under a
`variant` key; each row carries its variant's `config_id`. The long layout's
`variant` key is in the in-memory result; persisting it is a table declaration
with that key.

**Tier 1.** A hot variant can be stored: `.qetl.variant.flat` writes the
transform's answer as flat columns (`bid`, `ask`, `w`) from the variant's own
transform function, and `.qetl.variant.promote` registers the same SELECT and
COMBINE over the `stored` transform. `tests/q/test_variant_run.q` holds the two
bit-identical.

**Measured** (`tests/q/run_mapped_heap.q measure_variants`, KDB-X, 500
executions priced against a market of 200,000 rows a day with three-level
ladders, one SELECT group):

  | variants | books gathered | ladder rows read | heap after gather | evaluate     |
  | ---      | ---            | ---              | ---               | ---          |
  | 1        | 1,624          | 1,624            | 598,832 B         | 2 ms         |
  | 10       | 1,624          | 1,624            | 598,832 B         | 17 ms        |
  | 100      | 1,624          | 1,624            | 598,832 B         | 164 ms       |
  | 1,000    | 1,624          | 1,624            | 598,832 B         | 1,638 ms     |

With ten times the market (2,000,000 rows a day) the gather is 1,959 books and
684,592 B at every variant count. With three distinct offsets (three SELECT
groups) it is 1,800 books at every count: the gather grows with the distinct
SELECTs, never with the variants.

## The live path

A live horizon job buffers its reference as the transform declares it, and the
transform's market input is now the flat top, so the buffer holds `source_time`,
`sym`, `source`, `bid`, `ask`, `clock` and no depth, bounded by what the pending
executions can still need. Jobs that declare `shared_reference` and read the
same table in one process keep **one** buffer: the first to see a batch appends
the rows any of them keeps and hands the buffer on - q shares it by reference -
and a trim keeps the union of what each member's pending events can still need
(`src/etl/core/horizon_shared.q`). The stack runs one job to a process, so this
takes effect where jobs are loaded together (a single-process run, the test
suite); `fx_markout_wide` can be removed later without touching the others - it
leaves the group and nothing else changes.

The same declared variants run over that bounded buffer: `.qetl.variant.live`
arranges and numbers the buffer exactly as the gathered slice is, then
evaluates, so a live job and a bounded run price a variant alike
(`tests/q/test_variant_run.q` holds them equal). Variants of the flat top work
on today's buffer; a variant that reads depth needs the buffer to carry the
ladders, and says so by name when it does not.

## Migrating an HDB

`uqs data check` (dry) lists what is missing (`bid`, `ask`, `rid` per partition)
and the partitions not in the declared order. `uqs data check --apply` asks,
then - with the stack stopped, as for any migration - derives the flat top from
each partition's stored ladders in chunks (they stay mapped), rewrites a
partition not in order in the HDB writer's staging area and swaps it in whole
under the write lock, and numbers its rows. A partition already in order with
its numbers right is not touched; a second run changes nothing. The bootstrap
fill that every `uqs` command runs does **not** do any of this: a null `bid`
would read as a withdrawn book, and deriving it reads every ladder.

## Measured

`tests/q/run_mapped_heap.q` builds a synthetic HDB (two days of `market_data`,
sorted, flat, no depth columns at all) and, in a fresh process that maps it,
runs the `hdb_fx_markouts` source's query and the worker's transform, reporting
what `.Q.w[]` says the run added to the heap. `tests/q/test_mapped_heap.q`
asserts the shape of it; the numbers below are the run (KDB-X, Apple silicon,
`M` executions scored against the day's `N` rows; the "before" is origin/master
against an HDB of the old shape, with three-level ladders).

  | rows per day `N` | before: heap added (2,000 executions) | after: heap added (2,000 executions)  |
  | ---              | ---                                   | ---                                   |
  | 100,000          | 39.7 MB                               | 8.7 MB                                |
  | 300,000          | 110.9 MB                              | 9.1 MB                                |
  | 1,000,000        | 305.7 MB                              | 9.1 MB                                |
  | 10,000,000       | (grows with `N`: ~300 bytes a row)    | 9.9 MB                                |
  | 100,000,000      | (grows with `N`)                      | 9.9 MB; high-water mark unchanged     |

And with the market held at 1,000,000 rows a day, the heap added grows with the
executions: 4.5 MB for 1,000, 9.1 MB for 2,000, 18.2 MB for 4,000.

A book's depth read through its `rid` is flat the same way (210 KB at 100,000
rows a day, 280 KB at 1,000,000, for 500 probes), while selecting the day's
books with a `where` beyond the date - the old way - grows from 3.1 MB to 25 MB
over the same two sizes.

To measure at a size of your own: build with
`q tests/q/run_mapped_heap.q build DIR ROWS EXECUTIONS` and `measure` the same
arguments, or set `UQF_HEAP_ROWS` for the suite.
