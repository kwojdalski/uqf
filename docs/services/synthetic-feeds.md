# Synthetic feeds: fxfeed1 and quotesfeed1

The two feeds that invent market data for the demo, and how to add another.
Starting, stopping and inspecting the stack as a whole is in [running the uqf
stack](../guides/uqs.md).

## fxfeed1 - adding your own row-generating process

`src/etl/streaming/fx_feed.q` is a second, independent feed process publishing
synthetic top-of-book quotes for `EURUSD`/`GBPUSD`/`USDJPY`/`AUDUSD` (a small
random walk around a fixed spot, `+/-` 1 pip wide) into the same `quote` table
the vendored `feed1` already writes equity quotes into - `sym` is just a symbol
column, so FX pairs and equity tickers coexist in one table with no schema
change. It's the concrete worked example for "how do I add a process that
publishes rows".

**It is a declaration, not a process script.** Since #204 a feed says what it is
and the framework does the rest: the job file declares its timer body and the
table it publishes, `.qstream.define` records that, and one generic runner -
`scripts/processes/torq_stream.q` - is what TorQ actually starts. Which job a
process runs is decided by its procname, so there is no per-feed script any
more. The three things the job itself still owns:

1. build one row per pair as plain vectors (see the file's own comment on why
   they must stay vectors, not dicts keyed by pair - a dict here silently
   produces a `length` error on insert, the hard way to find out)
2. hand them to its own `publish`, which the runner wires to the tickerplant -
   the job never calls `.u.upd` or touches discovery itself
3. declare `period` and `on_timer`, which is what makes it a feed rather than a
   subscriber

To add your own: write a job file under `src/etl/streaming/` and register it
with `.qstream.define`. That is the whole registration - the process registry is
read from the declaration, and a new process is given the next free port in
`scripts/processes/process_ports.csv`. `uqs new-job` scaffolds it; see [adding a
pipeline](../guides/new-pipeline.md).

## quotesfeed1 - a real database for one of uqf's own table shapes

`src/etl/streaming/quotes_feed.q` is a proof of concept for getting an actual
on-disk kdb+ database, built with the TorQ Finance Starter Pack's own
tickerplant/RDB/WDB/HDB machinery, seeded with a table shape uqf's *own* pricing
code understands - rather than the vendored pack's generic `quote`/`trade`
tables. It publishes synthetic depth-aware FX quotes (3 levels per side,
level-0-first vectors) into a new `quotes` table:
`time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes - the same shape
`src/pricing/forwards.q`'s `require_quotes_cols` expects (`ts` there; `time`
here, since the tickerplant's own `upd` machinery requires the first column
literally named `time` - rename it back with `select ts:time,... from quotes`
before handing rows to `.qfwd.cross_book_at`/etc).

Getting this table into a real, on-disk database took no changes to
`rdb.q`/`wdb.q`/`hdb.q` at all - the vendored RDB's default `subscribeto:`
`` ` `` already means "every table in the schema", and WDB writes down whatever
the RDB has, so a brand new table only needs two things:

1. **Schema** - `uqs.model.plant_schema._generated_schema_content()` appends the
   `quotes` table definition to a *copy* of the vendored `database.q` (written
   to `output/uqs/database.q` on every `bootstrap()`, same generate-never-edit
   approach as `process.csv`), and `_base_process_rows()` repoints `stp1`'s
   `-schemafile` extras arg at that copy instead of the vendored file.
2. **Feed** - the `quotes_feed` job itself, wired in as a process.csv row
   exactly like `fxfeed1` (port offset `+24`), running under the shared
   `torq_stream.q` runner.

```
uqs query \
    "select time,sym,bid_prices,ask_prices from quotes" --port 6052        # rdb1
uqs query \
    "select ts:time,sym,bid_prices,bid_sizes,ask_prices,ask_sizes from quotes" --port 6052
```

After an EOD writedown (`raw -- eod`/`wdb1`'s own cycle, or just leaving the
demo running past midnight) `quotes` rows land in the HDB alongside
`quote`/`trade`, queryable the same way.
