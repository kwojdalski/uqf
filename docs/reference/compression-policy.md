# Compression policy

How the HDB's tables are compressed, declared once and rendered into the file
TorQ's `.cmp` reads. This page states the contract:
[`src/etl/core/compression.q`](../../src/etl/core/compression.q) validates a
declaration,
[`src/etl/compression_policy.q`](../../src/etl/compression_policy.q) holds this
stack's, and
[`scripts/torqconfig/compressionconfig.csv`](../../scripts/torqconfig/compressionconfig.csv)
is **generated** --- never edit it. After changing a declaration:

```bash
uv run python scripts/generate/derive.py compression-config           # write
uv run python scripts/generate/derive.py compression-config --check   # exit 1 if stale
```

The target needs KDB-X: it loads the tree, prints what q renders, and then runs
TorQ's own `.cmp.checkcsv` (the vendored `lib/torq/code/common/compress.q`, read
only, with `.lg` stubbed) over the result, so the file committed is one `.cmp`
loads. The `compression-config` hook runs the check on commit.

**TorQ's `.cmp` is the engine.** The library that applies a policy (#2146) wraps
`.cmp.showcomp` and `.cmp.compressmaxage`; the CSV below is exactly the file
`.cmp` reads, `default` rows included. A policy is therefore refused if `.cmp`
could not take it, and nothing under `src/` copies `.cmp`'s rules --- the
authority on what it loads is `.cmp.checkcsv`, run by `derive.py` and by
`scripts/gates/tests/test_derive.py`.

## Why a declaration and not a CSV

TorQ's `.cmp` (`lib/torq/code/common/compress.q`) reads `compressionconfig.csv` ---
`table,minage,column,calgo,cblocksize,clevel` --- and the vendored copy is one
`default` row. A CSV kept by hand in this repository would be a second copy of
facts that belong to the datasets: whether a table is large, whether it is
re-read, which of its columns are booleans. So the facts are declared beside the
tables, validated when declared, and the CSV is derived.

### Which file TorQ reads

Not the one under `scripts/torqconfig`. `.proc.getconfigfile` returns the first
of the application's `appconfig`, `KDBSERVCONFIG` and `KDBCONFIG` that holds the
file, and the starter pack ships `appconfig/compressionconfig.csv`, so a
generated file there would be ignored. Instead `uqs` copies the derived file
into `TORQDATA` on every bootstrap (`stack/compression.py`, beside
`reporter.csv`), and `scripts/processes/uqs_compression_config.q` sets
`.cmp.inputcsv` to that copy. A process that runs `.cmp` loads it **before**
`compress.q`, which reads `inputcsv` once as it loads.
`python/uqs/tests/test_compression_config.py` loads both, with the lookup
stubbed to answer the starter pack's file, and asserts `.cmp.inputcsv` is the
`TORQDATA` copy. No process in the stack runs `.cmp` yet: whichever one #2146
adds takes the overlay in `VENDORED_LOAD_OVERLAY` (`stack/procs.py`).

## The shape

One q dict per table. Keys `algo`, `blocksize`, `level`, `minage` and `columns` ---
the library that applies a policy reads exactly this.

  | Key         | Meaning                                                                        | Accepted                                                           |
  | ---         | ---                                                                            | ---                                                                |
  | `algo`      | the algorithm, as `.cmp` takes it                                              | 0 decompress, 1 qipc, 2 gzip, 3 snappy                             |
  | `blocksize` | the logical block size, a power of two (16 is 64 KB)                           | 12 to 20                                                           |
  | `level`     | the algorithm's level                                                          | gzip 0--9; qipc and snappy take none (0)                           |
  | `minage`    | whole days a partition must be old before it is compressed                     | 0 or more                                                          |
  | `columns`   | column to `(algo;blocksize;level)`, for the columns that differ from the table | columns of the table                                               |

```q
stack:`algo`blocksize`level`minage!(2;16;6;7)
deep:`algo`blocksize`level`minage!(2;17;6;3)
keep:(enlist `algo)!enlist 0
markout:`minage`columns!(14;(enlist `invert)!enlist 2 16 9)
.qetl.compression.set_default stack
.qetl.compression.declare[`market_data;deep]
.qetl.compression.declare[`reference_data;keep]
.qetl.compression.declare[`fx_markout;markout]
```

`set_default` is the stack default and needs `algo` and `minage`, and
`blocksize` and `level` unless `algo` is 0. `declare` is **partial**: it states
what differs from the default, and `.qetl.compression.resolve` returns the whole
dict. Algorithm 0 means "leave this table as written" (or decompress it); it has
no block size or level and resolves to 0 for both.

Declaration refuses, naming the key and the range:

- an algorithm outside 0--3 (lz4hc and zstd by name, see below), a block size
  outside 12--20, a level the algorithm does not take, a `minage` that is
  negative or not an integer;
- a key it does not take (`minage_days`);
- a column override naming a column the table lacks, or on a table whose columns
  are unknown (only a plant table has a schema to check against);
- a table declared twice;
- a level that does not fit the algorithm a table *ends up with*: a table that
  changes only `algo` to qipc, under a gzip level-6 default, is refused at
  declaration, not when the file is generated.

Generation refuses a table that is neither a plant table nor a bounded worker's
dataset, so a misspelt name does not compress nothing and say nothing.

## What no policy can include

The sym file, `.d` files, `par.txt` and the coverage ledger (`etl_coverage`).
The first three are not column data and compressing them breaks the database;
the ledger is read and rewritten whole by the pipeline. `declare` refuses all
four by name. The generated CSV also pins `etl_coverage` at algorithm 0, so
TorQ's `default` row cannot reach it. (TorQ's own scan already skips `.d` and
anything outside a table directory.) A table's *column* called `sym` is an
ordinary column.

## lz4hc and zstd

KDB-X has them (algorithms 4 and 5); `.cmp.checkcsv` does not accept them, and
`.cmp` is the engine. A declaration naming either is refused with a message
saying so, rather than a file `.cmp` would reject at startup.

## The guidance behind the defaults

From the KX compression whitepaper (<https://code.kx.com/q/wp/compress/>):

- **Compress by age.** Recent partitions are hot: they are in the page cache and
  read at random, and random access to compressed data costs two to three times
  the uncompressed read, because a whole block must be inflated to reach a row.
  Old partitions are scanned, rarely and in bulk, where compression saves disk
  and often I/O. `minage` is that boundary, in days.
- **Keep the block size small where access is random.** A block is the unit that
  is inflated, so a small block (12--16) wastes less work per random read and
  compresses a little worse; a large one (17--20) compresses better and suits
  tables only ever scanned whole.
- **Choose by column.** Booleans, low-cardinality columns (symbols are stored as
  integers) and sorted columns, the `p#`-attributed `sym` and the time column
  chiefly, compress well. Floating-point columns of unrelated values compress
  least. A per-column override is for a column that differs from its table *and
  is worth the line*.
- **Algorithm.** gzip compresses most of the three `.cmp` takes and is slowest
  to inflate; snappy inflates fastest at a worse ratio; qipc is cheap and
  modest. The whitepaper's better trade-offs (lz4hc, zstd) are not available
  through `.cmp`.

## This stack's choices

  | Tables                                                                                                                                                             | Policy                                              | Because                                                                                                    |
  | ---                                                                                                                                                                | ---                                                 | ---                                                                                                        |
  | the default (everything unnamed)                                                                                                                                   | gzip, block 16, level 6, after 7 days               | a week keeps days still being backfilled and corrected in the cheap, uncompressed form                     |
  | `market_data`, `fx_orderbook`, `mkt_orderbook`, `wide_orderbook`, `crypto_book`, `crypto_book_1s`, `superbook`, `book_liquidity_1m`, `last_value`, `crypto_trades` | gzip, block 17, level 6, after 3 days               | the bulk of the bytes, written once and scanned in whole-day sweeps; nested float ladders compress well    |
  | `crypto_markout`, `eq_markout`                                                                                                                                     | gzip, block 16, level 6, after 14 days              | derived and re-derived by backfills, so left alone for a fortnight                                         |
  | `fx_markout` (`invert`), `fx_markout_wide` (`leg1_invert`, `leg2_invert`, `leg3_invert`)                                                                           | as above, 14 days; the booleans at gzip level 9     | one byte of two values: the best candidates there are, and the top level costs nothing worth counting      |
  | `reference_data`, `economic_calendar`, `connections`, `config_change`, `ccy_exposure`, `order_routing`                                                             | algorithm 0                                         | a few rows a day: nothing to save, and every read would inflate                                            |

## Adding a table

Name it in `src/etl/compression_policy.q` only if it differs from the default,
then regenerate. A table not named takes the stack default, which TorQ applies
to every table in the HDB that its CSV does not name.
