/ hdb_markouts.q - our fills, marked out against quotes, from the local HDB (.qpipe.source.hdb_markouts).
/ .
/ The live markout job (src/etl/streaming/markout.q) scores fills as they
/ stream past, from buffers that die with its process, so a fill it never
/ saw - it was down, restarting, or the fill predates it - is never marked
/ out. This source is what hdb_markouts_backfill reads instead: for a window of
/ fills, both of what the HDB holds -
/ .
/   `trades  the fills the executions job published and end of day saved,
/            already in markout's shape (signed side, trade_price, pip_factor)
/   `quote   top of book, from the window's start to its end plus the
/            longest horizon, so the last fill's horizons have their quotes
/ .
/ - scored by the SAME function at the SAME horizons as the live job
/ (.qexec.markout_at_horizons, .qpipe.job.markout.horizons), so a fill
/ scored both ways scores identically.
/ .
/ Why the scoring is in the source and not a transform: a bounded worker's
/ transform reads exactly one input, the source's contract (require_transform),
/ and a markout needs two tables. `query` runs in the backfill process; only
/ its two selects cross the wire, each through .qetl.source.ipc, so `--trace`
/ shows both under one window.
/ .
/ The window is on `trade_time`, not `time` (the horizon instant): a fill at
/ 10:59:58 in the [10:00;11:00) window keeps its 10s markout, which lands
/ after 11:00.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_MARKOUTS=localhost:<hdb1's port> in the
/ shell `uqs backfill` runs from. Without it, the fixture below is used.

\d .qpipe.source.hdb_markouts

source_name:`hdb_markouts

/ execution_quality's published columns: one row per fill per horizon.
columns:`time`sym`trade_time`horizon`trade_price`ref_price`markout_pips
types:"pspnfff"

target:`execution_quality
time_column:`trade_time

/ A fill at one horizon. The table carries no fill id, so two fills of one
/ pair at the same instant share a key - as they do in the live job's rows.
row_key:`sym`trade_time`horizon

/ .u.upd stamps `time` in UTC, and trades and quote are both stamped that way.
tz:`UTC

/ Score fills against quotes at the live job's horizons.
/ @param deals rows of the HDB's trades: sym time side trade_price pip_factor
/ @param quotes rows of the HDB's quote: sym time bid ask
/ @return one row per fill per horizon, as .qexec.markout_at_horizons
/ @eg count .qpipe.source.hdb_markouts.score[.qpipe.source.hdb_markouts.raw_fills[];.qpipe.source.hdb_markouts.raw_quotes[]] -> 8
score:{[deals;quotes]
    .qexec.markout_at_horizons[deals;
        select sym, time, mid:0.5*bid+ask from quotes;
        .qpipe.job.markout.horizons]}

/ A window with no fills: the contract's columns, empty.
none:([] time:`timestamp$(); sym:`symbol$(); trade_time:`timestamp$(); horizon:`timespan$();
    trade_price:`float$(); ref_price:`float$(); markout_pips:`float$())

/ The window's fills, and the quotes they are marked against, both from the
/ HDB; scored here. Both tables are date-partitioned, so the partition is
/ constrained first, and each is named as a symbol so it resolves at the
/ HDB's root - see upstream_trades.q for what a bare name does across the
/ wire.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the window's fills, scored at every horizon
query:{[h;range_from;range_to]
    deals:.qetl.source.ipc[h;{[from_ts;to_ts]
        `time xasc select time, sym, side, trade_price, pip_factor from `trades
            where date within `date$(from_ts;to_ts), time>=from_ts, time<to_ts
      };range_from;range_to];
    / No fills, no quote query - and no score: markout_at_horizons throws
    / 'type on zero trades, which would fail every quiet window.
    if[0=count deals; :none];
    quotes:.qetl.source.ipc[h;{[from_ts;to_ts]
        `time xasc select time, sym, bid, ask from `quote
            where date within `date$(from_ts;to_ts), time>=from_ts, time<to_ts
      };range_from;range_to+max .qpipe.job.markout.horizons];
    score[deals;quotes]}

/ Four fills on 2026.09.17: EURUSD both ways, a USDJPY buy, and a GBPUSD
/ fill with no quote after it, so a null markout is part of the fixture.
/ @return the fills, as the HDB's trades holds them
/ @eg count .qpipe.source.hdb_markouts.raw_fills[] -> 4
raw_fills:{[]
    ([] time:2026.09.17D10:00:00.000000000 2026.09.17D10:00:05.000000000
             2026.09.17D10:00:07.000000000 2026.09.17D23:59:59.000000000;
        sym:`EURUSD`EURUSD`USDJPY`GBPUSD;
        side:1 -1 1 1;
        trade_price:1.1001 1.1003 150.02 1.2701;
        pip_factor:10000 10000 100 10000)}

/ Quotes around raw_fills, as the HDB's quote holds them.
/ @return quotes on 2026.09.17, sorted by time
/ @eg count .qpipe.source.hdb_markouts.raw_quotes[] -> 6
raw_quotes:{[]
    ([] time:2026.09.17D09:59:59.000000000 2026.09.17D10:00:01.000000000
             2026.09.17D10:00:06.500000000 2026.09.17D10:00:07.500000000
             2026.09.17D10:00:12.000000000 2026.09.17D10:00:20.000000000;
        sym:`EURUSD`EURUSD`EURUSD`USDJPY`EURUSD`USDJPY;
        bid:1.1000 1.1004 1.1006 150.00 1.1010 150.10;
        ask:1.1002 1.1006 1.1008 150.02 1.1012 150.12)}

/ What query returns for the fixture's fills and quotes: the same scoring,
/ on rows shaped as the HDB holds them.
/ @return the fixture fills scored at every horizon, sorted by trade_time
fixture:{[] `trade_time xasc score[raw_fills[];raw_quotes[]]}

.qetl.source.define[source_name;
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz!
    (source_name;`trades;target;time_column;row_key;columns;types;query;fixture;tz)];

\d .
