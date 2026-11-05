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

One q dict per table. Keys `algo`, `blocksize`, `level`, `minage`, `columns` and
`mode` --- the library that applies a policy reads exactly this.

  | Key         | Meaning                                                                        | Accepted                                                           |
  | ---         | ---                                                                            | ---                                                                |
  | `algo`      | the algorithm, as `.cmp` takes it                                              | 0 decompress, 1 qipc, 2 gzip, 3 snappy                             |
  | `blocksize` | the logical block size, a power of two (16 is 64 KB)                           | 12 to 20                                                           |
  | `level`     | the algorithm's level                                                          | gzip 0--9; qipc and snappy take none (0)                           |
  | `minage`    | whole days a partition must be old before it is compressed                     | 0 or more                                                          |
  | `columns`   | column to `(algo;blocksize;level)`, for the columns that differ from the table | columns of the table                                               |
  | `mode`      | *when* it is compressed: see [Write time or age](#write-time-or-age)           | `age` (the default), `write`, `both`                               |

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

## Write time or age

`mode` is the one choice that is not about *how* a table is compressed but
*when*. Default `age`, which is what the stack did before it existed.

  | `mode`  | At save (the HDB writer)               | Later (TorQ's `.cmp`, `minage` days on)                          |
  | ---     | ---                                    | ---                                                              |
  | `age`   | writes uncompressed                    | compresses the partition                                         |
  | `write` | compresses with the table's policy     | held out: the generated CSV gives the table `minage` 36500       |
  | `both`  | compresses with the table's policy     | keeps its rows; finds nothing to do in a partition already done  |

**The trade-off** (KX whitepaper, <https://code.kx.com/q/wp/compress/>).
Write-time compression spends CPU at the moment of the write --- end of day for
TorQ, the window finish for a backfill --- and from then on every read of the
partition inflates blocks. Age-based compression leaves the hot partitions, the
ones queried at random all day, as fast as they are written, and pays the CPU
later, in a housekeeping window, once the partition is cold. So: `age` for a
table that is queried while fresh (the default, and every table this stack
declares); `write` for a table that is written once, scanned in bulk, and whose
disk is the constraint; `both` when the writer should compress but a partition
some other writer left uncompressed must still be caught.

**Never twice.** A partition is compressed once, whoever gets there first:

- The writer applies a policy only to a partition none of whose columns is
  compressed (`.qetl.compression.for_write` reads `-21!`). A later finish of a
  compressed partition (another backfill window appended to it) leaves it as it
  is, even if the policy has changed since: changing a policy re-compresses
  nothing that exists. To change a partition's algorithm, decompress it (a
  policy of algorithm 0 through the age pass) and compress again.
- TorQ's `.cmp` skips a file already compressed, whatever it was compressed
  with. `.qtorq.compress.plan` shows that as `action = skip`, so `--report` and
  `--apply` count a `both` partition as done.
- A `write` table never reaches the age pass. `.cmp` has no row meaning "leave
  this table alone": algorithm 0 *decompresses* what the writer compressed, so
  the generated CSV instead gives the table a `minage` of 36500 days, which no
  partition reaches.

**How the writer does it.** `.qetl.io.finish_parts` (the finish of every bounded
worker, `flush` included) sorts each partition it wrote --- the one write that
rewrites every column file --- under `.qetl.compression.with_zd`, which sets
`.z.zd` to `(blocksize;algo;level)` for the sort and puts it back as it found it
(unset, or the value an operator set), also when the write fails, because
`.z.zd` is the process's and a writer that left it set would compress everything
the process wrote afterwards. A column that declares its own triple is then
rewritten on its own (`apply_columns`; `-19!` to a temp file, kept only if it
reads back equal, hash files moved with it). Appending a window to a partition
is not itself compressed: it happens before the finish, which is where a
partition is compressed. A later window appended to a partition that is already
compressed is appended to the compressed files (KDB-X allows it), and its finish
finds the partition compressed and leaves it so.

A table whose finish rewrites nothing (no `sym` or `time` column and no sort
rule) has no write to compress under, so it is not compressed at save; give it
`both` and the age pass catches it.

### Who writes the HDB, and what each does about compression

  | Writer                                                                                            | Compression today                                                                                                                                                                                                                                                                                                              |
  | ---                                                                                               | ---                                                                                                                                                                                                                                                                                                                            |
  | `.qetl.io.finish_parts` (`src/etl/core/io_hdb.q`): bounded workers' finish and `flush`            | the policy's `mode`: `write`/`both` compress under `.z.zd`; `age` writes uncompressed                                                                                                                                                                                                                                          |
  | `.qetl.io.write_hdb` append, `.qetl.io.stage` and `swap` (same file)                              | uncompressed by design; the finish that follows compresses the partition                                                                                                                                                                                                                                                       |
  | TorQ end of day: wdb `.wdb.endofdaysort`, and the sort workers (`.wdb.setcompression`)            | the process setting `compression` (`lib/torq/config/settings/wdb.q`, `sort.q`), empty here, so uncompressed. One triple for every table: it cannot express a per-table mode, so a `write` table written by end of day stays uncompressed and is not caught by the age pass. Use `both` or `age` for a table end of day writes. |
  | `uqs data hdb-check --fix` / `scripts/gates/fill_hdb_partitions.q` (KX dbmaint)                   | `set` with `.z.zd` unset: columns it adds are uncompressed, whatever the rest of the partition is; the age pass (`both`, `age`) compresses them later                                                                                                                                                                          |
  | `uqs data migrate` (`scripts/gates/stage_hdb_migration.q`)                                        | `set` with `.z.zd` unset: the rebuilt partition is uncompressed                                                                                                                                                                                                                                                                |
  | `scripts/gates/seed_hdb_partitions.q`, `scripts/examples/hdb_transfer_example.q`, `uqs` scaffolds | `set` with `.z.zd` unset: test and example data, uncompressed                                                                                                                                                                                                                                                                  |

No writer in the tree sets `.z.zd` or passes compression parameters to `set`
except the finish above.

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
