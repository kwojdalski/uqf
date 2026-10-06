# Database metatables

`src/metadata/metatables.q` provides `.qmeta`: reusable definitions for
measuring explicit database partitions and returning ordinary q tables. It works
in a plain q process and in a TorQ HDB. Counts are the default; eFX dimensions
and additional measurements are supplied by the caller.

## Boundary with TorQ and ETL

TorQ already provides collection, timers, transport and persisted daily
statistics through DQE/DQEDB. Its [DQE
process](../../lib/torq/code/processes/dqe.q) stores scalar `resultstab` and
table-valued `advancedres` results. Its
[tablecount](../../lib/torq/code/dqe/tablecount.q) counts all HDB tables for a
partition; [bycount](../../lib/torq/code/dqe/bycount.q) groups a table in the
latest partition. Use those directly when they meet the need.

This component adds a shared definition format, explicit multi-partition
selection, regular typed metatables, and replacement of previously measured
slices. It installs no process, scheduler, connections, handlers, persistence
engine or quality checker. `scripts/processes/torq_metatables.q` adapts the same
queries to DQE's existing result protocol. ETL run records and publication
coverage remain separate: observed row counts cannot establish successful
ingestion, expected completeness, or absence of duplicates. They can contradict
the coverage ledger, though, and `reconcile` reports where they do (see
[Reconcile with ETL coverage](#reconcile-with-etl-coverage)).

## Names

A metatable's name starts with `meta_` (`.qmeta.prefix`), the way the pipeline's
own bookkeeping starts with `etl_` (`etl_runs`, `etl_coverage`). In `tables[]`,
an HDB directory or `uqs schema`, either prefix marks a table as derived from
the data beside it rather than being market data, and a tool can find them by
pattern instead of by a list.

`.qmeta.require_name` checks a name and the DQE adapter applies it: a name
without the prefix is refused, with the prefixed name it should have been, never
renamed for you. The prefix is fixed rather than configurable - every reader
would have to resolve it, and changing it would strand the tables already
stored.

## Define and collect

Run from the repository root in a process with `trade` loaded:

```q
\l src/metadata/metatables.q

/ Total rows per requested date, with no secondary grouping.
totals:.qmeta.definition[`trade;`date;`symbol$();()!()];
meta_trade_counts:.qmeta.collect[totals;2026.09.01 2026.09.02];

/ Counts and traded base quantity per pair and venue.
metrics:`rows`base_quantity`first_time`last_time!
    ((count;`i);(sum;`size);(min;`time);(max;`time));
by_market:.qmeta.definition[`trade;`date;`sym`venue;metrics];
meta_trade_markets:.qmeta.collect[by_market;2026.09.01 2026.09.02];
```

Adapt `trade`, `sym`, `venue`, `size` and `time` to your schema. Summing `size`
is meaningful only when its unit and currency are uniform within each group; no
currency conversion or cross-pair total is implied.

Output columns are the partition column, grouping columns, named measurements,
`meta_observed_at` (UTC timestamp captured at the start of collection) and
`meta_definition` (the definition's fingerprint, see below). Grouping and
measurement types are preserved. The partition column need not be named `date`:
integer, long, month, date and symbol slices are supported. Symbol slices can be
logical partitions in an in-memory table; this does not imply that kdb+ supports
physical symbol-partitioned HDBs.

`aggregates` accepts trusted functional qSQL expressions, including custom
aggregate functions. Each must return a scalar per group. Definitions are
application code, not an untrusted-client input format. Missing columns and
query errors propagate rather than being recorded as zero. A map of named
definitions can cover several tables or several breakdowns of one table; there
is no mutable global registry or fixed list of eFX columns.

## Broader profiling

`profile[time_cols;null_cols;rules]` builds an aggregate dictionary for the same
standalone `definition`/`collect`/`refresh` API and the DQE adapter:

```q
/ A rule is a per-row boolean expression: true marks a violation.
/ Exclude nulls explicitly when a separate null count owns that policy.
rules:`crossed`nonpositive_bid!
    ((>;`bid;`ask);(&;(not;(null;`bid));(<=;`bid;0f)));
measurements:.qmeta.profile[enlist`time;`time`bid`ask;rules];
spec:.qmeta.definition[`quote;`date;`sym`venue;measurements];

/ On demand: returns the current profile without storing it.
current_profile:.qmeta.collect[spec;enlist 2026.09.01];

/ Explicit refresh: replaces these slices in a previously collected table.
stored_profile:.qmeta.refresh[stored_profile;spec;enlist 2026.09.01];
```

For each partition/group this produces:

  | Columns                              | Meaning                                    |
  | ---                                  | ---                                        |
  | `rows`                               | Total row count, including rows with nulls |
  | `min_time`, `max_time`               | Earliest/latest non-null time              |
  | `null_time`, `null_bid`, `null_ask`  | Null counts for each configured column     |
  | `bad_crossed`, `bad_nonpositive_bid` | Number of rows violating each rule         |
  | `meta_observed_at`                   | Collection start time in UTC               |

Temporal columns must have q temporal types. Empty/all-null time ranges return
typed null bounds. Null counts follow q's `null` semantics, including its
treatment of empty symbols; they are not generic string-blank checks. Quality
rules must return one boolean per source row, including an empty boolean vector
for an empty slice. Scalar, numeric and wrong-length rule outputs fail the
query. Rules are trusted functional qSQL expressions, so column comparisons,
allowed-value sets and custom predicates can express feed-specific checks
without adding framework code.

Q treats numeric nulls as less than ordinary numbers. A rule such as `bid <= 0`
therefore counts null bids too; the example explicitly excludes them. Likewise,
decide whether crossed-quote rules should exclude missing prices. Null counts
remain separate, and rule violations can overlap; do not add them together as a
count of distinct bad rows.

Counts are long integers. Compute rates using `rows` as the denominator; zero
rows mean no observations, not a 100% quality score. No automatic pass/fail
thresholds, alerts, temporal-gap detector or duplicate-key policy are assumed.
Use TorQ DQC or the existing `.qdqc` functions for applicable checks on these
measurements. This keeps profiling separate from quality enforcement.

## Partition semantics and refresh

- Supply a nonempty **typed vector** of partitions. There is no implicit latest
  date or unbounded all-history scan. Duplicate requests are deduplicated.
- Each query filters the partition first and returns aggregates directly; it
  does not fetch the full table into an intermediate in-memory table.
- An ungrouped empty slice returns one row (`rows=0` for default counts).
  Grouped empty slices have no groups. A missing date and an existing empty date
  can both produce zero: collection is not a physical-file inventory.
- Groups are observed groups, not a Cartesian product of expected pairs and
  venues. There are no fabricated zeros for missing markets.
- Collection is synchronous. A live source can change between partition queries;
  the timestamp does not promise a transaction spanning partitions.

```q
/ Recompute a corrected date, removing groups that disappeared.
meta_trade_markets:.qmeta.refresh[meta_trade_markets;by_market;enlist 2026.09.01];
```

Refresh returns a replacement value only after all requested queries succeed. It
preserves other partitions and their observation timestamps. An empty recomputed
grouped slice removes its old groups. Repeating a refresh does not accumulate
counts. The caller owns assignment and storage.

Keep one unchanged definition per metatable. `refresh` enforces this two ways:
the stored schema must match, and every stored row's `meta_definition` must
equal `.qmeta.fingerprint` of the definition passed in. The second check catches
what the first cannot: changing `notional` from `(sum;`size)` to `(max;`size)`
keeps the column names and types, but would otherwise mix two meanings in one
table. The fingerprint is an MD5 of the serialized definition, so it also
changes when a lambda the definition embeds is redefined (as `.qmeta.time_bound`
in `profile`). A table collected before this column existed has no fingerprints
and must be rebuilt with `collect`.

For a partitioned HDB, use its loaded partition column and values (normally
`.Q.pf` and a selected subset of `.Q.PV`). Read on an HDB after its reload has
completed. This component does not replace TorQ's EOD/reload coordination. Kdb+
can initialize its partition-count cache on first use; see the [KX partition
guidance](https://code.kx.com/q/kb/partition/) before dispatching count queries
on secondary threads or read-only evaluation contexts.

## Reconcile with ETL coverage

Bounded workers record, for every window they complete, how many rows they
published (`rows_published` in the `etl_coverage` ledger). `reconcile` compares
those claims with what a metatable observed:

```q
/ Restrict the metatable to the slice that the coverage partition describes.
eurusd:select from trade_markets where sym=`EURUSD;
.qmeta.reconcile[eurusd;2026.09.01 2026.09.02;`trades;`EURUSD;`v1]
```

It returns one row per requested date: `observed` (rows summed over the
metatable's groups), `published` (rows summed over that day's current windows),
`windows`, `fully_covered` (whether the claims cover the whole day) and
`status`:

  | Status       | Meaning                                                                                                  |
  | ---          | ---                                                                                                      |
  | `match`      | observed equals published                                                                                |
  | `mismatch`   | they differ: rows lost, duplicated, or written outside the ledger                                        |
  | `unrecorded` | no window claims this date, so there is nothing to compare                                               |
  | `ambiguous`  | a window crosses midnight, or two current windows overlap, so the day's published total cannot be stated |

Only current claims count; superseded ones are ignored. Dates are explicit, as
with `collect`. A requested date with no rows in the metatable counts as zero
observed rows, because a grouped empty slice has no rows to show. Reading the
result:

- A `match` is consistent, not proof: the right number of wrong rows also
  matches.
- The comparison only means something when bounded workers are the dataset's
  only writer. Rows from a live feed or a manual load appear as a `mismatch`,
  and on a day that is not `fully_covered` that may be all a mismatch means.
- The metatable must have a `rows` count, which is the default aggregate and the
  first one `profile` produces. Partitions other than `date` are not supported.

`reconcile` changes neither the metatable nor the ledger, and needs
`src/etl/core/materialisation.q` loaded.

## TorQ DQE integration

The stack wires this in. DQE itself stays off with `uqs start`, for the reason
[uqs.md](uqs.md#what-actually-starts) gives, so start it when you want the
metatables built:

```sh
uqs start dqe1 dqedb1
```

From then on DQE builds every metatable in its query list at 04:30 UTC, each
from the previous day's partition on `hdb1`, and stores it in DQEDB's
`advancedres`. One is defined:

  | Metatable                  | From           | Grouped by | Measurements                      |
  | ---                        | ---            | ---        | ---                               |
  | `meta_fx_orderbook_by_sym` | `fx_orderbook` | `sym`      | `rows`, `first_time`, `last_time` |

What uqs sets up for it, on every command:

- `hdb1` loads `src/metadata/metatables.q` after its database. DQE sends
  `.dqe.uqf_metatable` to `hdb1` by value and it runs there.
- `dqe1` loads `scripts/processes/uqs_dqe_config.q` before its own script, and
  `metatables.q` and the adapter, `scripts/processes/torq_metatables.q`, after
  it.
- `$TORQDATA/dqengineconfig.csv` is the starter pack's query list with this
  tree's metatables appended. `uqs_dqe_config.q` points DQE at it; the vendored
  file is not edited.

To add a metatable, append a row to `UQF_DQE_ROWS` in
[`python/uqs/src/uqs/stack/dqe.py`](../../python/uqs/src/uqs/stack/dqe.py). Its
`params` are the adapter's six arguments as q - name, table, partition column,
partitions, group columns, aggregates - and must hold no comma, because DQE
reads the file as plain comma-separated text. DQE `value`s them each time it
arms its timers, at start and at every end of day, so `.z.d-1` is the day just
finished.

The adapter can also be called directly on an HDB that has both files loaded:

```q
.dqe.uqf_metatable[`meta_trade_by_market;`trade;`date;
    2026.09.01 2026.09.02;`sym`venue;()!()]
```

Or submitted through DQE's own transport from `dqe1`:

```q
.dqe.runquery[`.dqe.uqf_metatable;
    (`meta_trade_by_market;`trade;`date;2026.09.01 2026.09.02;`sym`venue;()!());
    `table;enlist`hdb1]
```

DQE reflects the adapter's `tab` parameter into `advancedres.table` and records
its name in `resultkeys`. The nested `resultdata` retains **source partition
values**; DQEDB's own storage date is not the source partition date. DQE
persistence retains its own append/history semantics; `.qmeta.refresh` does not
mutate DQEDB or change DQE's retention policy. No TorQ files are patched.

## Verification

```sh
scripts/test.py q-unit
scripts/test.py q-metatables-hdb
```

The second command creates and cleans up a disposable two-date, enumerated HDB
in a fresh KDB-X process. Tests cover exact totals, eFX grouping, custom
aggregates, empty slices, repeat refresh and the adapter result shape. Unit
tests additionally cover disappearing groups, failed refreshes, schema changes,
redefined aggregates, invalid inputs, logical symbol partitions, and every
`reconcile` status against an in-memory coverage ledger.
`python/uqs/tests/test_dqe_config.py` reads the generated query list as DQE does
and runs each of this tree's rows through the adapter. The full live DQE
transport and DQEDB lifecycle are not exercised by these tests.
