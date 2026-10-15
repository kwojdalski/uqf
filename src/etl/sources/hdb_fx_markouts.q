/ hdb_fx_markouts.q - the executions to mark out, and the books to mark them against, from the local HDB
/ (.qpipe.source.hdb_fx_markouts, #1395).
/ .
/ The live markout jobs (fx_markout, fx_markout_wide) score executions as they
/ stream past, from buffers that die with their process: an execution they never
/ saw - they were down, restarting, or it predates them - is never marked out.
/ This source is what the bounded twins read instead, two inputs from what the HDB
/ holds:
/ .
/   `executions   the canonical executions end of day saved. The PRIMARY input: it
/                 owns the window and its rows are what a run counts
/   `market_data  the books of every pair an execution's route needs, from the
/                 window's start back by the job's lookback to its end plus the
/                 furthest horizon, AND each (pair, source)'s last snapshot before
/                 that - the book an early execution is priced against when the
/                 market was quiet across the window's start. A supporting input:
/                 context, validated against its own contract, never cut to the window
/ .
/ Nothing here knows a vendor: both tables are the canonical ones. The scoring is the
/ worker's transform, the live job's own (src/etl/transforms/fx_markout_outputs.q), so
/ a refilled row is the row the live job publishes.
/ .
/ The window is on the execution's source_time, the clock it is scored on, not the
/ plant's `time` the HDB is partitioned and sorted by: an execution stamped at the
/ plant after midnight can carry a source_time before it, so each query reads one
/ date either side.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_FX_MARKOUTS=localhost:<hdb1's port> in the shell
/ `uqs job backfill` runs from. Without it, the fixture below is used.

\d .qpipe.source.hdb_fx_markouts

source_name:`hdb_fx_markouts

/ The primary input: the HDB's executions, narrowed to what scoring reads.
columns:`source_time`venue`sym`side`size`price`fill_id
types:"pssjffs"

time_column:`source_time

/ An execution is (venue; fill_id); the worker writes by its own target_key.
row_key:`venue`fill_id

/ The supporting input: the books, read by the transform as `market_data`.
supporting:enlist[`market_data]!enlist .qetl.plant.columns[`market_data;`source_time`sym`source`bid_prices`bid_sizes`ask_prices`ask_sizes]

/ .u.upd stamps `time` in UTC, and the sources' own stamps are UTC.
tz:`UTC

/ How far back a source's last book before the window is looked for. Without it, an
/ execution early in a window whose latest book predates the window has nothing to be
/ priced against, and the target key would replace the live job's correct row with
/ nulls. A pair unquoted for longer than this is unpriced, as the live job would
/ leave it after a restart.
lookback:7D

/ The window's executions, and the books they are scored against, both from the HDB.
/ Both tables are date-partitioned, so the partition is constrained first, and each is
/ named as a symbol so it resolves at the HDB's root. The legs to read are the
/ routes' (the live job's own `legs`), so the books read are the books scoring needs.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return `executions`market_data!(the window's executions; the books they need)
query:{[h;range_from;range_to]
    deals:.qetl.source.ipc[h;{[from_ts;to_ts]
        `source_time xasc select source_time, venue, sym, side, size, price, fill_id from `executions
            where date within (-1 1)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts
      };range_from;range_to];
    if[0=count deals; :`executions`market_data!(deals;0#supporting`market_data)];
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    pairs:distinct raze .qpipe.transform.fx_markout.legs_of[spec;deals];
    books:.qetl.source.ipc[h;{[syms;lb;from_ts;to_ts]
        cand:`source_time xasc select source_time, sym, source, bid_prices, bid_sizes, ask_prices, ask_sizes
            from `market_data where date within `date$(from_ts-lb;from_ts), source_time<from_ts, sym in syms;
        before:0!?[cand;();`sym`source!`sym`source;()];
        inwin:select source_time, sym, source, bid_prices, bid_sizes, ask_prices, ask_sizes from `market_data
            where date within (-1 1)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts, sym in syms;
        `source_time xasc (cols before)#before,inwin
      }[pairs;lookback];
      range_from-.qpipe.transform.fx_markout.lookback[spec];
      range_to+0D|max spec`horizons];
    `executions`market_data!(deals;books)}

/ Three fills on 2026.09.17 and the books around them: a EURUSD buy at 1.1001 against
/ two sources, a USDPLN sell that routes EURUSD (inverted) and EURPLN, and a crypto
/ fill the calculation cannot route and so excludes.
/ @return the executions, as the HDB's executions holds them
/ @eg count .qpipe.source.hdb_fx_markouts.raw_executions[]  ->  3
raw_executions:{[]
    ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:20 2026.09.17D10:00:21;
        venue:`a`a`binance_spot; sym:(`EURUSD;`USDPLN;`$"BTC-USDT"); side:1 -1 1;
        size:1e6 1e6 0.5; price:1.1001 3.99 62000f; fill_id:`x1`y1`z1)}

/ The books: EURUSD on sources a and b, and EURPLN on a.
/ @return the books, as the HDB's market_data holds them
/ @eg count .qpipe.source.hdb_fx_markouts.raw_market_data[]  ->  7
raw_market_data:{[]
    ([] source_time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00.5 2026.09.17D10:00:08 2026.09.17D09:59:59 2026.09.17D10:00:25;
        sym:`EURUSD`EURUSD`EURUSD`EURUSD`EURUSD`EURPLN`EURPLN;
        source:`a`a`a`b`b`a`a;
        bid_prices:(enlist 1.0999;enlist 1.1004;enlist 1.1009;enlist 1.1003;enlist 1.1008;enlist 4.9999;enlist 5.0999);
        bid_sizes:7#enlist enlist 1e6;
        ask_prices:(enlist 1.1001;enlist 1.1006;enlist 1.1011;enlist 1.1007;enlist 1.1010;enlist 5.0001;enlist 5.1001);
        ask_sizes:7#enlist enlist 1e6)}

/ What query returns for the fixture's day.
/ @return `executions`market_data!(raw_executions[];raw_market_data[])
fixture:{[] `executions`market_data!(raw_executions[];raw_market_data[])}

.qetl.source.define[source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`supporting!
    (source_name;`executions;time_column;row_key;columns;types;query;fixture;tz;supporting)];

\d .
