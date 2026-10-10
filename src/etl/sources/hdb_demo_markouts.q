/ hdb_demo_markouts.q - our fills, and the quotes to mark them against, from the local HDB (.qpipe.source.hdb_demo_markouts).
/ .
/ The live markout job (src/etl/streaming/demo_markout.q) scores fills as they
/ stream past, from buffers that die with its process, so a fill it never
/ saw - it was down, restarting, or the fill predates it - is never marked
/ out. This source is what hdb_demo_markouts_backfill reads instead: for a window of
/ fills, two inputs from what the HDB holds -
/ .
/   `trades  the fills the executions job published and end of day saved,
/            already in markout's shape (signed side, trade_price, pip_factor)
/   `quote   top of book, from the window's start to its end plus the
/            longest horizon, so the last fill's horizons have their quotes,
/            AND each traded pair's last quote before the window starts -
/            the one an early fill is priced against when the market was
/            quiet across the window's start (looked back up to `lookback`)
/ .
/ - handed over RAW. `trades` is the primary input: it owns the window and
/ its rows are what the run counts. `quote` is a supporting input (#617):
/ context, validated against its own contract and never cut to the window.
/ .
/ The scoring is the worker's transform, not this file's: before #617 a
/ bounded worker's transform could read only one input, so the markout ran
/ here, in `query`, where the transform's examples never saw it and the
/ fixture path skipped it. What stays here is what belongs to fetching -
/ which quotes a window needs: the lookback before it and the longest
/ horizon after it. `query` runs in the backfill process; only its two
/ selects cross the wire, each through .qetl.source.ipc, so `--trace` shows
/ both under one window.
/ .
/ The window is on the fill's `time`, which the markout rows carry as
/ `trade_time`, not on the horizon instant: a fill at 10:59:58 in the
/ [10:00;11:00) window keeps its 10s markout, which lands after 11:00.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_DEMO_MARKOUTS=localhost:<hdb1's port> in
/ the shell `uqs backfill` runs from. Without it, the fixture below is used.

\d .qpipe.source.hdb_demo_markouts

source_name:`hdb_demo_markouts

/ The primary input: the HDB's fills, in markout's shape already (signed
/ side, trade_price, pip_factor).
columns:`time`sym`side`trade_price`pip_factor
types:"psjfj"

time_column:`time

/ A fill. The table carries no fill id, so two fills of one pair at the same
/ instant share a key - as they do in the live job's rows. The worker writes
/ by its own target_key, (sym;trade_time;horizon), the markout rows' key.
row_key:`sym`time

/ The supporting input: top of book, read by the transform as `quote`.
supporting:enlist[`quote]!enlist ([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$())

/ .u.upd stamps `time` in UTC, and trades and quote are both stamped that way.
tz:`UTC

/ How far back the last quote before a window is looked for. Without it, a
/ fill early in a window whose latest quote predates the window has nothing
/ to be priced against and scores null - and the target key replaces the
/ live job's correct row with that null. A pair unquoted for longer than this
/ scores null, as the live job would after a restart.
lookback:7D

/ The window's fills, and the quotes they are marked against, both from the
/ HDB. Both tables are date-partitioned, so the partition is constrained
/ first, and each is named as a symbol so it resolves at the HDB's root -
/ see upstream_trades.q for what a bare name does across the wire.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return `trades`quote!(the window's fills; the quotes they need)
query:{[h;range_from;range_to]
    deals:.qetl.source.ipc[h;{[from_ts;to_ts]
        `time xasc select time, sym, side, trade_price, pip_factor from `trades
            where date within `date$(from_ts;to_ts), time>=from_ts, time<to_ts
      };range_from;range_to];
    / No fills, no quote query: a window whose primary input is empty
    / publishes nothing, whatever quotes there are.
    if[0=count deals; :`trades`quote!(deals;0#supporting`quote)];
    / One request: the window's quotes, plus each traded pair's last quote
    / before it. The pairs and the lookback travel in the projection.
    quotes:.qetl.source.ipc[h;{[syms;lb;from_ts;to_ts]
        before:select time, sym, bid, ask from
            0!select last time, last bid, last ask by sym from `quote
                where date within `date$(from_ts-lb;from_ts), time<from_ts, sym in syms;
        inwin:select time, sym, bid, ask from `quote
            where date within `date$(from_ts;to_ts), time>=from_ts, time<to_ts, sym in syms;
        `time xasc before,inwin
      }[distinct deals`sym;lookback];range_from;range_to+.qpipe.transform.demo_markouts.max_horizon];
    `trades`quote!(deals;quotes)}

/ Four fills on 2026.09.17: EURUSD both ways, a USDJPY buy, and a GBPUSD
/ fill with no quote after it, so a null markout is part of the fixture.
/ @return the fills, as the HDB's trades holds them
/ @eg count .qpipe.source.hdb_demo_markouts.raw_fills[] -> 4
raw_fills:{[]
    ([] time:2026.09.17D10:00:00.000000000 2026.09.17D10:00:05.000000000
             2026.09.17D10:00:07.000000000 2026.09.17D23:59:59.000000000;
        sym:`EURUSD`EURUSD`USDJPY`GBPUSD;
        side:1 -1 1 1;
        trade_price:1.1001 1.1003 150.02 1.2701;
        pip_factor:.qccy.pip_factor `EURUSD`EURUSD`USDJPY`GBPUSD)}

/ Quotes around raw_fills, as the HDB's quote holds them.
/ @return quotes on 2026.09.17, sorted by time
/ @eg count .qpipe.source.hdb_demo_markouts.raw_quotes[] -> 6
raw_quotes:{[]
    ([] time:2026.09.17D09:59:59.000000000 2026.09.17D10:00:01.000000000
             2026.09.17D10:00:06.500000000 2026.09.17D10:00:07.500000000
             2026.09.17D10:00:12.000000000 2026.09.17D10:00:20.000000000;
        sym:`EURUSD`EURUSD`EURUSD`USDJPY`EURUSD`USDJPY;
        bid:1.1000 1.1004 1.1006 150.00 1.1010 150.10;
        ask:1.1002 1.1006 1.1008 150.02 1.1012 150.12)}

/ What query returns, for the fixture's day: the fills and the quotes, as
/ the HDB holds them. The worker cuts `trades` to each window; `quote` is
/ context and is not cut.
/ @return `trades`quote!(raw_fills[];raw_quotes[])
fixture:{[] `trades`quote!(raw_fills[];raw_quotes[])}

.qetl.source.define[source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`supporting!
    (source_name;`trades;time_column;row_key;columns;types;query;fixture;tz;supporting)];

\d .
