# Inspecting and maintaining the HDB

This page shows how to read the database's shape, repair and migrate HDB
partitions, compress them, replay a tickerplant log and compare tables.

## Reading the database's shape

`schema` lists the tables a running process holds and the shape of each.

```bash
uqs schema                  # every table on rdb1, with row and column counts
uqs schema fx_orderbook     # one table's columns, types and attributes
uqs schema 'crypto*'        # every table matching a pattern, one block each
uqs schema hdb1             # the history instead of today
uqs schema --port 6052      # a port directly, skipping PROC resolution
uqs schema -i               # browse it, re-read every 2 seconds
uqs schema -i --every 500ms # faster; --every 0 re-reads only on R
```

Quote a pattern, or the shell expands it against your filenames first. A
`float vector` column holds one list per row. An empty vector column reports as
`general` until a row exists, so a table can read `general` before its first
publish and `float vector` after.

## Missing partitions

```text
$ uqs schema hdb1
could not read the schema from hdb1 (6053):
  "./2015.01.07/arbitrage. OS reports: No such file or directory"
```

The table is not missing. A partitioned kdb+ database needs every table in every
partition, and one absent directory fails the whole query. The error names
whichever table sorts first, so it does not say which partition is short.

```bash
uqs data check           # which partitions are missing what
uqs data check --apply   # write each missing table and column, via vendored KX dbmaint
```

`--apply` shows what it would write and asks first. `--yes` skips the question,
and without a terminal `--apply` needs it. The fill is additive: an existing
table directory is left alone, and a second run changes nothing. Every `uqs`
command runs this fill, so you rarely need it by hand.

## Bringing old partitions up to date

Partitions written by older versions can lack columns, or the row order, that
current code expects. `uqs data check` reports them, and
`uqs data check --apply` fixes them after asking. Run `--apply` yourself: the
fill that runs with every `uqs` command leaves existing markout and
`market_data` partitions as they are.

Before the first `--apply`:

- Stop the stack. The `market_data` fix rewrites each partition that is out of
  order.
- Expect markout partitions to lose three columns. `contract_version`,
  `rate_timing` and `report_ccy` are folded into one `config_id`, which
  [`uqs data snapshot show`](uqs.md#configuration-snapshots) reads back.
- Check `scripts/hdb/clock_rules.csv`. The `clock` column of `executions` and
  `market_data` is filled from its rules, and a row no rule matches gets
  `unknown`, which markouts refuse. If a source is not covered, add a rule row
  (`table,market,name,clock`, where `*` as the name matches any source of that
  market). A partition already filled `unknown` is not revisited by a later run.

## Renamed or retyped HDB columns

`data check --apply` does not rewrite existing columns. When a release renames
or retypes one, stop the stack and migrate each affected date and table.

```bash
uqs data migrate trade --date 2026-01-01 --rename old_price:price --cast price
uqs data migrate trade --date 2026-01-01 --rename old_price:price --cast price --apply
uqs data check
uqs restart hdb1
```

Without `--apply` the command is a dry run. `--apply` shows the same plan and
asks before writing, and `--yes` skips the question. `--cast` allows a type
change only when every value survives conversion back to its original type. A
stored column that the current schema does not name must be mapped with
`--rename`. The applied command prints the old directory's backup path for
rollback. It does not work on a segmented HDB.

An empty table that kept an obsolete schema is rebuilt from its current
declaration instead. The old table is kept at the reported backup path.

```bash
uqs data migrate trade --date 2026-01-01 --reset-empty          # eligibility and the schema change; writes nothing
uqs data migrate trade --date 2026-01-01 --reset-empty --apply  # after asking; --yes does not ask
```

## One table, in any HDB

`data check` and `data alter` also work on one table across a whole HDB: this
runtime's, a release's, or an attached TorQ stack's.

```bash
uqs data check --attachment pb-prod                          # the plan: which dates lack the table
uqs data check --attachment pb-prod --apply                  # write an empty one there, after asking
uqs data alter --attachment pb-prod --reorder time,sym,amount,note
uqs data alter --attachment pb-prod --reorder time,sym,amount,note --apply
uqs data alter --attachment pb-prod --undo /data/hdb.uqf-alter/20261028D101500123-pb.json --apply
```

Name the HDB with exactly one handle. The plan says which was used.

  | handle                             | the HDB                                                                                     | runs                                     |
  | ---                                | ---                                                                                         | ---                                      |
  | `uqs --target NAME data ...`       | the deployed release's own configured HDB                                                   | on the server, from the release          |
  | `uqs data ... --attachment NAME`   | the attachment's pinned `storage.hdb_dir`, else its live wdb's `.wdb.hdbdir`                | on the stack's host, over ssh            |
  | `uqs data ... --hdb DIR`           | that directory                                                                              | on this machine                          |
  | none                               | this runtime's own HDB (what `--target` reaches on a server)                                | on this machine                          |

With `--attachment`, the table defaults to the attachment's own. The q that
writes the HDB must be KDB-X: PeachQ is refused.

`data check --table T` (or `--attachment`, `--hdb`) gives each partition lacking
the table an empty one. The schema is this runtime's declaration when it
declares the table. Otherwise it comes from the latest partition holding the
table, or from `--template YYYY.MM.DD`.

`data alter [TABLE]` makes one change per run, in each partition holding the
table:

  | operation                     | writes                                                     |
  | ---                           | ---                                                        |
  | `--reorder c1,c2,...`         | each partition's `.d`; it must list every existing column  |
  | `--rename old:new`            | the column's files renamed, and `.d`                       |
  | `--attr col:p\|s\|g\|u\|none` | the column's attribute (its header)                        |
  | `--cast col:TYPE`             | the column, written beside it and renamed over it          |

A cast to or from symbol is refused. The partitions holding the table must agree
on its columns, order and types before `data alter` writes anything.

Dry run first. Both commands print the plan and write nothing until `--apply`,
which shows the same plan and asks once. `--yes` skips the question.

`data alter` writes a journal before its first write, beside the HDB at
`<hdb>.uqf-alter/<stamp>-<table>.json`. If a run stops part way, run the same
operation again to resume it. `--undo JOURNAL` puts back the journalled `.d` or
attribute, or renames the column back, and is a preview until you add `--apply`.
A completed cast is undone by a cast back, which is exact only for a lossless
cast.

Run these commands outside end of day, and while no backfill writes the table.
With `--hdb`, writers are not checked, and the plan prints the reload command
for you to run.

### On production

For a table a backfill wrote into a production TorQ HDB on some dates only:

1. Attach the job if it is not attached (`uqs job attachment add`), with `--ssh`
   naming an account on the HDB's host that can write it. Check that
   `ssh -o BatchMode=yes USER@HOST true` works.
2. Let any attached backfill finish (`uqs job attachment status`), and pick a
   time well away from end of day.
3. Read the plan from `uqs data check --attachment NAME`. Check that the HDB it
   names is the directory you mean, then add `--apply`.
4. To change the layout, plan with
   `uqs data alter --attachment NAME --reorder ...` (or `--rename`, `--attr`,
   `--cast`), then add `--apply`. Keep the journal path the plan prints.
   `uqs data alter --attachment NAME --undo JOURNAL` takes it to reverse the
   change.

## How the HDB is compressed

```bash
uqs data compress --report            # per table and column: algorithm, block size, ratio
uqs data compress --report --json     # the same, as one document
uqs --target uat data compress --report
uqs --target kdb_prd data compress --report --hdb /data/hdb   # a TorQ server without uqf
```

`--report` reads what the HDB holds and changes nothing. After the per-column
figures it lists the largest uncompressed partitions, then each partition that
is not in the order, or lacks the attribute, that `sort.csv` declares. The
report does not re-sort. On a large HDB it is substantial read I/O.

Under `--target`, `--hdb DIR` is a path on that target, which needs q and the
HDB and no uqf. Sorting is then judged only against `--sort-csv FILE`, also a
path on the target. This form reports only: it refuses `--apply`.

## Compressing the HDB

`data compress` brings the HDB's older partitions to the [compression
policy](../reference/compression-policy.md). Without `--apply` nothing changes.

```bash
uqs data compress                              # the plan, the estimate, the disk it needs
uqs data compress --table market_data --older-than 30d
uqs data compress --date 2026-11-02 --apply    # that day now, whatever the policy's minage
uqs data compress --apply                      # asks, then one partition at a time
uqs --target prod data compress                # a release's own HDB, run there
```

`--apply` asks first, and `--yes` answers. It stops before writing when a
filesystem is short of space. It holds the HDB write lock, so a backfill or
`data migrate` waits. It compresses one partition at a time, oldest first, and
asks the HDBs to reload once after the last. Partitions a backfill is still
writing are `held` until it finishes. A file that fails its check stops the run.
Every partition already done is valid, and a second run continues from there.
`uqs activity list --action hdb.compress` lists the runs.

## Replaying a tickerplant log

`data replay` runs TorQ's `tickerlogreplay`, the `tpreplay1` process. A replay
empties the tables it replays, in the partitions it touches, before it writes
them.

```bash
uqs data replay                                                   # the plan; runs nothing
uqs data replay --date 2026-09-22 --table quote --table trade --apply
```

`--apply` shows the plan and asks first, and `--yes` answers. The log directory
and schema are read from the running plant, and the database from the hdb
process on the same stack. To replay a log after its stack has stopped, give
`--tplogdir`, `--schema-file` and `--hdb` yourself. `uqs data replay PROC` picks
the plant when more than one is up with a log of its own.

## Checking a dataset's producers

A dataset can be filled by more than one job, such as a polling feed and a
backfill of the same table. `uqs job check DATASET` checks, read only, that they
fit the destination, agree with each other and miss nothing.

```bash
uqs job check demo_deals --date 2026-09-11 --scope sym=EURUSD --gateway gw1:6060
```

It reports four results: `schema`, `behaviour`, `cross_mode` and `completeness`.
Each is `PASS`, `FAIL` or `UNVERIFIED`. A check that could not run reads
`UNVERIFIED`, with the reason. `cross_mode` needs the dataset's declared basis
(`events`, `revisions` or `final_state`). The command exits 1 on `FAIL`, and
with `--fail-unverified` on `UNVERIFIED` too.

## Comparing a table on two endpoints

`uqs data diff` compares one table, or several, on two endpoints. An endpoint is
`local` for this machine's stack, or a deploy target.

```bash
uqs data diff --left local --right prod --table demo_deals --date 2026-09-11 \
    --filter sym=EURUSD --limit 5000 --source-version v1
```

It compares each side's schema, at most `--limit` rows per side in the window,
and, with `--source-version`, the published coverage. Rows are paired on the row
key the table's producers declare. `time` is left out, because each side stamps
it on receipt.

The outcome `equal_in_sample` does not mean the tables are equal. Its label says
whether every row in the window was read on both sides or the read stopped at
`--limit`. Narrow the window or raise `--limit` to read it all. The command
exits 1 on any other outcome.
