# Worked example: from one kdb+ database to another

A complete [backfill](backfill.md), from the scaffold to a filled database: a
job that reads trades out of one kdb+ HDB on this machine, adds each trade's
notional, and writes them into a second HDB. One script runs all of it, with no
TorQ and no running stack:

```bash
q scripts/examples/hdb_transfer_example.q          # from the repository root
q scripts/examples/hdb_transfer_example.q -keep    # keep both databases to look at
```

## What it does

1. **Builds a source database.** Three days of trades go into a temporary
   directory. The table is date-partitioned and splayed, and its symbols are
   enumerated against the database's own `sym` file, like any kdb+ HDB.
2. **Points the job at it.** For a `local` source, the credential *is* the HDB's
   directory, so the script sets `UQF_SOURCE_CRED_HDB_TRANSFER` to the source
   database's path. Where rows go is the runner's choice: the script installs
   `.qetl.io.hdb` on a second, empty directory, partitioned by the source's time
   column.
3. **Runs it three times:**
   - **the first two days:** both are written;
   - **the same range again:** nothing is fetched, because every window is
     already in the coverage ledger;
   - **all three days:** only the third day is fetched.
4. **Reads the destination back from disk** and checks it against the source row
   for row. It exits 1 on any difference, which is why the `q-scripts` test lane
   runs it.

```
== 3. run it
  first two days:        completed
  same range again:      idle  (every window already covered - nothing fetched)
  all three days:        completed  (only the third day was missing)
  ok    completed, then idle, then completed
  ok    rows written: 310 then 0 then 170 - the re-run wrote nothing, the last only the new day

== 4. read the destination back from disk
date      | trades notional
----------| -------------------
2026.01.05| 150    1.638325e+10
2026.01.06| 160    1.592609e+10
2026.01.07| 170    1.142484e+10
  ok    every source trade arrived: 480
  ok    each row exactly as the source holds it
  ok    notional = price * size on every row
  ok    one partition per day in the destination
```

With `-keep`, the destination is an ordinary HDB: `q <its path>` loads it, and
`select count i by date from trades_copy` shows the three days.

## How the job was made

```bash
uqs job new hdb_transfer --kind backfill --transport local --dataset trades_copy \
    --columns 'trade_id:long,sym:symbol,price:float,size:long,side:symbol,notional:float'
```

That wrote the skeleton: a source, a worker, the `trades_copy` table in
`plant_tables.q`, a catalog entry and a failing test. The parts written by hand:

  | file                                                                                       | what was filled in                                                                                                                                                                                                                                                  |
  | -------------------------------------------------------------------------------------      | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | [`src/etl/sources/hdb_transfer.q`](../../src/etl/sources/hdb_transfer.q)                   | the columns it reads (no `notional`: the worker adds it); `trade_id` as the row key; the query, which reads the window's partitions with `.qetl.source.local` and keeps `time>=from` and `time<to`; a deterministic fixture                                         |
  | [`src/etl/workers/hdb_transfer_backfill.q`](../../src/etl/workers/hdb_transfer_backfill.q) | the transform `with_notional` (price × size, in the quote currency, so USDJPY's is in yen), with an example `.qetl.transform` runs; facts recording rows, pairs and notional for each window                                                                        |
  | [`tests/q/test_hdb_transfer_backfill.q`](../../tests/q/test_hdb_transfer_backfill.q)       | the window's half-open edge, the declared shape, the transform and the facts                                                                                                                                                                                        |

## Pointing it at a real database

- **On the stack.** Set the credential to your HDB's root directory, then run it
  like any backfill. The process writes into the stack's own HDB, exactly as
  `torq_backfill.q` does:

  ```bash
  export UQF_SOURCE_CRED_HDB_TRANSFER=/data/source_hdb
  uqs backfill hdb_transfer_backfill --from 2026-01-05 --to 2026-01-08 --version v1 --wait
  ```

- **Without the stack.** `scripts/dev/run_backfill.q` runs the same worker in
  one q process. Its `-hdb` flag names the destination directory:

  ```bash
  UQF_SOURCE_CRED_HDB_TRANSFER=/data/source_hdb q scripts/dev/run_backfill.q \
      -worker hdb_transfer_backfill -version v1 -from 2026.01.05D00:00 -to 2026.01.08D00:00 \
      -hdb /data/destination_hdb
  ```

The source needs a `trades` table with `time`, `trade_id`, `sym`, `price`,
`size` and `side`. Change the query's `select`, the source's `columns`, and the
table in `plant_tables.q` together for a different shape. The source contract
refuses a mismatch by name.
