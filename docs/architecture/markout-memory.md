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
- So `.qetl.mapped.asof` does the search itself: it finds each equality-key run
  (for `market_data`, `(sym; source)`) by galloping (a few elements, never a
  column) and runs every probe's binary search over its run's as-of column
  (`source_time`) in lockstep, `log2(longest run)` rounds of one gather each. It
  returns exactly what `aj` returns - `tests/q/test_mapped_books.q` holds the
  two equal on a market of three days, three sources and withdrawn sides - and
  its heap, and its high-water mark, do not move with the number of rows.

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
- **Pointer columns (#3000).** `two_sided_rid`, after `rid`: see "Match and
  conditions" below.
- **Sorted, and the sort is the declaration.** By `sym`, `source`,
  `source_time`, with `p#` on `sym`. uqf's own finish (`.qetl.io.finish_parts`)
  writes it from the plant's declared sort rule, and TorQ's end-of-day sort
  writes it from `scripts/torqconfig/sort.csv` (KDBSERVCONFIG puts that file
  ahead of `lib/torq`'s, which is never edited), after which
  `scripts/processes/uqs_row_ids.q` numbers the rows.
  `uqs data compress --report` reads the same `sort.csv`, so it names any
  partition not in this order. Nothing in the search names those columns
  (#3000): `arranged`, `require_arranged`, `arrange`, `runs`, `asof` and
  `asof_where` take the order from the table's declared sort
  (`.qetl.plant.sorting`), as a table name (`.qetl.mapped.order_of` reads it) or
  as the columns themselves - the HDB, which never loads the plant, is handed
  them. **Every sort column but the last is an equality key** (the first parted,
  runs found by galloping, generalised from `(sym; source)` to n keys); **the
  last is the as-of column.** `market_data`'s declared order is unchanged, so
  its results are bit for bit what they were; `tests/q/test_mapped_match.q`
  searches tables declared `region, sym, venue, ts` and `sym, time` by the same
  code and holds them to a scan.
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

### Reference policies: a variant per instant (#2996)

The spec's `reference` may instead name a **reference policy**
(`.qetl.reference.policy`, `src/etl/core/reference_policy.q`): a rule table that
picks the sources and the variant per reference instant. One complete regime per
row - `rule_id`, `priority`, `active_from` (inclusive) and `active_to`
(exclusive, `24:00` allowed) in UTC, `weekdays` (`` `all ``, `` `weekdays `` or
day symbols), `instrument_selector`, `sources`, `reference_variant`, `max_age`
(`0Nn` keeps the variant's staleness) and `parameters` (merged into the
variant's, refused when its transform lacks the key):

```q
rules:([] rule_id:`asia_open`london_open`us_open`fallback; priority:10 20 30 0;
    active_from:00:00 08:00 12:00 00:00; active_to:08:00 12:00 21:00 24:00;
    weekdays:`weekdays`weekdays`weekdays`all; instrument_selector:`EURUSD`EURUSD`EURUSD`all;
    sources:(`LP_A`LP_B;`LP_A`LP_C;`LP_B`LP_D;`all);
    reference_variant:`best_top`median_top`best_top`best_top; max_age:4#0Nn; parameters:4#enlist ())
.qetl.reference.policy.register[`fx_sessions;rules]
spec:spec,enlist[`reference]!enlist `fx_sessions
```

`register` refuses a malformed rule by name and **proves** coverage: for every
instrument the rules name (and any other, when a rule selects `` `all ``), every
weekday and every window between the boundaries the rules declare, at least one
rule matches and only one at the top priority - exhaustive, not sampled. At run
time `t0` and `t1` are each matched on their own timestamp
(`.qetl.reference.policy.select`, on the execution's instrument), the probes are
grouped by rule, and each group is priced by `.qetl.reference.priced` - the road
`.qetl.reference.at` takes - with the rule's sources as the venues, so a source
outside the rule cannot set the price. An instant no rule matches fails as
`unmatched_reference_rule`; there is no fallback the rules do not declare.

The policy's canonical content (rules sorted by `rule_id`, and the definition
each rule prices with) goes into the configuration snapshot, so a different
policy is a different `config_id`, while a spec without a policy keeps its id. A
row does not repeat its rules: `.qpipe.transform.config_markout.reference_rules`
re-derives `ref_rule_t0`, `ref_rule_t1` and their variants from the row's
`config_id`, `sym`, `source_time` and `horizon`, over the rules the snapshot
recorded. The jobs read their books by the spec's `max_age`, so a spec whose
policy has a rule with a longer `max_age` is refused when it is validated
(`.qetl.reference.policy.require_max_age`): raise the spec's `max_age`, or lower
the rule's. A rule with a null `max_age` is held to the same limit through its
variant's `stale` (`` `spec `` is the spec's own and always fits).

### Reference variants at scale (#2289)

Reference prices may be wanted in a thousand ways, and a stored column per way
is impossible at the size market data runs to. A variant is a declaration, so a
list of them factors, and the market is touched once:

1. **SELECT** - per distinct (venue set, offset, match) the as-of search over
   the flat keys (`tob_at`); without a match the staleness only filters the
   matched books afterwards (with one it bounds the search, see below). It
   returns the books and their `rid`s.
2. **DEDUPE** - the distinct books across every variant.
3. **GATHER ONCE** - `.qetl.mapped.gather` (in the process that maps
   `market_data`) reads the columns the variants' transforms declare - the union
   over the variants run - for those books in one `depth_at` call, through their
   `rid`s: a slice of k rows x those columns. `.qetl.mapped.depth_reads` counts
   the rows read and the gather refuses to return unless the count equals the
   distinct books; `scripts/gates/check.py variant-gather` refuses variant code
   that names `market_data`, a second reader of the extra columns, and a gather
   that stops deduping or counting.
4. **TRANSFORM** - each distinct (transform, parameters) once over the slice.
5. **COMBINE** - each variant's, across the books matched to each instant.

**Declared columns (#3000).** A transform declares the `market_data` columns it
reads as a symbol list, `needs`:

```q
.qetl.reference.add_transform[`micro;`bid`ask`bid_sizes`ask_sizes;(`symbol$())!();{[slice;unused_params] ...}];
```

`` `top `` and `` `depth `` are gone (nothing aliases them); `top` is
`` `bid`ask ``, the ladder transforms list the ladders they read. A column
`market_data` does not have is refused at registration, by name. A transform
whose list holds no ladder runs over the books a SELECT matched (they carry its
columns); one that does runs once over the gathered slice, which carries every
column in the union, and is read by `rid`. The gather reads, beyond the flat top
the search itself returns, exactly the union of the variants' `needs` -
`market`, `time`, a ladder - for the matched books' `rid`s: memory proportional
to the matched books and the columns declared, never to the market. A promoted
variant's weight `w` is declared but is not stored in `market_data`: the mapped
gather refuses it by name, and it runs over books that carry it
(`.qetl.variant.flat`).

**Match and conditions (#3000).** A variant's SELECT may carry `match`, a
dictionary the matched book must satisfy. It may name exactly two kinds of
column, and nothing else:

  | key                         | value                    | means                                                        |
  | ---                         | ---                      | ---                                                          |
  | an equality key of the sort | a value, or a list       | only those runs are searched (no read at all)                |
  | a declared condition        | `1b`                     | the latest book in the run that passes it                    |

```q
.qetl.reference.register[`two_sided;`venues`stale`offset`transform`combine`match!(
    `all;0D00:00:30;0D;`top;`best;enlist[`two_sided]!enlist 1b)];
```

**A condition is declared on the plant table, beside its sorting**, as a q
expression over the table's flat columns:

```q
.qetl.plant.condition[`market_data;`two_sided;"(not null bid) & not null ask"];
```

and the table declares its pointer column, `two_sided_rid` (long), in its own
line. The partition writer stores, for each row, **the rid of the latest row of
the same run - the same equality keys of the declared sort - at or before it
that passes the condition, null if none**: `.qetl.mapped.pointer`, two running
maxima over the partition's columns (the latest passing position, cut off at
each run's first row), one vectorised pass at write time. Three writers store
it, by the one function `.qetl.mapped.pointers.write`, beside the rid: uqf's own
finish (`.qetl.io.assign_row_ids`), TorQ's end-of-day sort
(`scripts/processes/uqs_row_ids.q`, which now loads the plant and refuses to
load without it) and `uqs data check --apply` (which also checks a partition's
pointers against its rows). A partition written before the column existed is
refused by the search with the same fix-it message as one without `bid`: *run
`uqs data check --apply`*.

**The search.** Bisection on the as-of column finds the latest book at or before
the instant; the match then costs one indexed read of `<name>_rid` at that book
and one read of the as-of column at the pointer, for the staleness bound (a
pointer older than the variant's `stale` is no book). A constant number of reads
per probe, however long the stretch of failing books - no loop over the window
anywhere. A match that names anything else is refused at registration, by name:
a stored column that is no key and no declared condition, a ladder, the as-of
column, a condition with any value but `1b`, or a second condition (the latest
row passing two conditions is not derivable from two pointers: declare their
conjunction as a condition of its own). Declaring one is a table edit and a
migration, which is the point: a condition nobody declared has no pointer, and
answering it would be a scan of the window per probe.

`asof_where` is the search and `aj` is still not: a three-key `aj` copies the
first key's columns into the heap. The staleness joins the SELECT group of a
variant with a match, since it bounds which pointer counts.
`tests/q/test_mapped_match.q` holds the pointer equal to a scan (run boundaries,
null when none) and the search equal to a scan, and runs the same code over
tables declared `region, sym, venue, ts` and `sym, time`;
`tests/q/test_variant_columns.q` holds a variant with a match, and one declaring
`market`, equal to a scan through the gather and through books in memory alike.

**Compression.** `market_data` IS written compressed (gzip, block 17, level 6,
after three days: `src/etl/compression_policy.q`), and a compressed column is
not mapped but inflated a block at a time on access, so the claim is measured
there too. `tests/q/run_mapped_heap.q compress` compresses every column of the
built HDB as the policy does and `measure_match` is run on it:

  | rows a day  | plain: heap added, resident growth | compressed: heap added, resident growth | on disk, plain -> compressed |
  | ---         | ---                                | ---                                     | ---                          |
  | 200,000     | 314,384 B, 5.5 MB                  | 314,384 B, 6.8 MB                       | 17.5 MB -> 5.4 MB            |
  | 2,000,000   | 314,384 B, 35.8 MB                 | 314,384 B, 39.0 MB                      | 172 MB -> 49 MB              |
  | 10,000,000  | 314,384 B, 16.6 MB                 | 314,384 B, 18.8 MB                      | (not kept)                   |

The heap `.Q.w[]` counts is the same on the compressed partition, and is flat in
the market on both. The resident set (read with `ps`, so it includes the pages a
search touches and the blocks it inflates) is noisy - first-touch faults of the
mapping dominate it, and it does not rise with the market (10,000,000 rows is
below 2,000,000) - and compressed runs a few MB above plain from the blocks
inflated. `tests/q/test_mapped_heap.q` asserts the heap for both; the resident
set is reported, not asserted.

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
transform and combine, parameters of the transform's own names and types, a
match on stored flat columns). The stored definition carries the transform's
`needs` and the `match`, so a changed column list or condition is a changed
`config_id`. The starter set, beside #1 `best_top`: `micro_median` (micro-price,
median across venues), `vwap_1m_best` (`vwap[1,000,000]`, best across venues),
`sizew_mid` (size-weighted mid across venues), `median_top` (median across
venues) and `depth3_wmean` (top-3 depth-weighted). Each has a hand-computed test
in `tests/q/test_reference_starter.q`. New transforms and combines are
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

**Measured, a condition and declared columns**
(`tests/q/run_mapped_heap.q measure_match`, KDB-X, 1,000 probes, the latest
`two_sided` book within an hour, `market` and `time` read by `rid`, a quarter of
the books one-sided): 314,384 B of heap at 200,000, 2,000,000 and 10,000,000
rows a day (2,432, 3,756 and 3,951 books matched; `.Q.w[]` allocates in powers
of two, so the figure steps rather than rising), against about 6 MB for the same
books selected with a `where` beyond the date. See Compression above for the
compressed partition.

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
ladders, and says so by name when it does not - and in general a buffer must
carry every column the variants declare (`needs`) and every column their
conditions read, or `.qetl.variant.live` refuses it, naming the columns and the
variants that read them (#3000). The buffer's pointers are made as the slice is
made: `.qetl.variant.slice` arranges the buffer and runs the writer's own pass
(`.qetl.mapped.pointer`) over it, so a condition's pointer in a live buffer is
the one a partition would hold. That is one vectorised pass over the bounded
buffer per evaluation - not state kept between appends, because the buffer is
re-sorted into run order for every evaluation anyway and an incrementally kept
"last passing rid per run" would be rebuilt by that sort.

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
