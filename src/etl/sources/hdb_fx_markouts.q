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

\d .

.qpipe.source.hdb_fx_markouts.source_name:`hdb_fx_markouts

/ The primary input: the HDB's executions, narrowed to what scoring reads.
.qpipe.source.hdb_fx_markouts.columns:`source_time`venue`sym`side`size`price`fill_id`clock
.qpipe.source.hdb_fx_markouts.types:"pssjffss"

.qpipe.source.hdb_fx_markouts.time_column:`source_time

/ An execution is (venue; fill_id); the worker writes by its own target_key.
.qpipe.source.hdb_fx_markouts.row_key:`venue`fill_id

/ The supporting input: the books, read by the transform as `market_data`.
.qpipe.source.hdb_fx_markouts.supporting:enlist[`market_data]!enlist .qetl.plant.columns[`market_data;`source_time`sym`source`bid_prices`bid_sizes`ask_prices`ask_sizes`clock]

/ .u.upd stamps `time` in UTC, and the sources' own stamps are UTC.
.qpipe.source.hdb_fx_markouts.tz:`UTC

/ The pairs a scored execution needs a reference for beyond its own route's legs:
/ the conversion pair that takes its money to the reporting currency (#1931), the
/ market_data sym of every rate the spec reads from a book, for the quote currencies
/ the window's executions are in. The core's (markout_core.conversion_pairs), so the
/ live job's readiness legs, this source's symbol filter and its ready hook name the
/ same books.
/ @param spec the job's spec
/ @param rows the window's executions
/ @return a symbol vector
/ @eg .qpipe.source.hdb_fx_markouts.conversion_pairs[.qpipe.transform.fx_markout.default_spec;([] sym:enlist `EURUSD)]  ->  `symbol$()
.qpipe.source.hdb_fx_markouts.conversion_pairs:{[spec;rows]
    (.qpipe.transform.markout_core.conversion_pairs[.qpipe.transform.fx_domain.domain;spec;rows])`sym}

/ The symbols the window's references are read for: the legs of every execution's
/ route (the live job's own `legs`), and its conversion pairs.
/ @param spec the job's spec
/ @param rows the window's executions
/ @return a symbol vector, distinct
/ @eg .qpipe.source.hdb_fx_markouts.reference_symbols[.qpipe.transform.fx_markout.default_spec;([] sym:enlist `USDPLN)]  ->  `EURUSD`EURPLN
.qpipe.source.hdb_fx_markouts.reference_symbols:{[spec;rows]
    legs:distinct raze .qpipe.transform.fx_markout.legs_of[spec;rows];
    distinct legs,.qpipe.source.hdb_fx_markouts.conversion_pairs[spec;rows]}

/ What the source reads of its books, declared (.qetl.source REFERENCE READ):
/   date_skew     1 day either side. The window is on source_time but the HDB is
/                 partitioned on the plant's `time`, which can fall on the other
/                 side of midnight from the source's stamp
/   lookback_cap  7 days, for a spec whose max_age is 0Wn. A finite max_age sets
/                 the look-back itself: a book older than max_age plus the furthest
/                 look-back horizon is not a reference, so reading back further
/                 is rescanning the market for nothing (#1926). A pair unquoted for
/                 longer than the look-back is unpriced, as the live job would leave
/                 it after a restart; without any look-back an execution early in a
/                 window would have nothing to be priced against and the target key
/                 would replace the live job's correct row with nulls
/   symbols       reference_symbols
.qpipe.source.hdb_fx_markouts.reference_read:`date_skew`lookback_cap`symbols!(1;7D;.qpipe.source.hdb_fx_markouts.reference_symbols)

/ The executions query sent to the HDB: the date partition is constrained first (widened by the
/ declared date_skew) and the table is named as a symbol so it resolves at the HDB's root.
/ @param skew the declared date_skew
/ @return a lambda of (from_ts; to_ts) for the HDB to run
/ @private
.qpipe.source.hdb_fx_markouts.deals_remote:{[skew]
    {[skew;from_ts;to_ts]
        `source_time xasc select source_time, venue, sym, side, size, price, fill_id, clock from `executions
            where date within (neg[skew],skew)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts
    }[skew]}

/ The window's executions, from the HDB.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the executions, sorted by source_time
.qpipe.source.hdb_fx_markouts.read_executions:{[h;range_from;range_to]
    skew:(.qetl.source.reference_read .qpipe.source.hdb_fx_markouts.source_name)`date_skew;
    .qetl.source.ipc[h;.qpipe.source.hdb_fx_markouts.deals_remote[skew];range_from;range_to]}

/ The bounds the books of a window are read between: from the window's start back by the
/ look-back the spec's max_age sets (measured from the window's start, so it already includes the
/ furthest look-back horizon) to its end plus the furthest horizon. `early` is the furthest an
/ execution is scored before its own time.
/ @param range_from the window's inclusive lower bound
/ @param range_to the window's exclusive upper bound
/ @return `read_from`early`read_to!(...): where the last-before search starts, how far back a
/   book can still count, and where the books end
/ @eg key .qpipe.source.hdb_fx_markouts.book_bounds[2026.09.17D10:00:00;2026.09.17D11:00:00]  ->  `read_from`early`read_to
.qpipe.source.hdb_fx_markouts.book_bounds:{[range_from;range_to]
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    early:.qpipe.transform.fx_markout.lookback[spec];
    lb:.qetl.source.reference_lookback[.qpipe.source.hdb_fx_markouts.source_name;spec`max_age;early];
    `read_from`early`read_to!(range_from-lb;range_from-early;range_to+0D|max spec`horizons)}

/ The books query sent to the HDB: the declared reference_read's, each (pair, source)'s last
/ snapshot before the window's start AND every one in the window, both widened by date_skew.
/ @param syms the market_data syms to read
/ @param skew the declared date_skew
/ @param read_from where the last-before search starts
/ @return a lambda of (from_ts; to_ts) for the HDB to run
/ @private
.qpipe.source.hdb_fx_markouts.books_remote:{[syms;skew;read_from]
    {[syms;skew;read_from;from_ts;to_ts]
        cand:`source_time xasc select source_time, sym, source, bid_prices, bid_sizes, ask_prices, ask_sizes, clock
            from `market_data where date within (neg[skew],skew)+`date$(read_from;from_ts), source_time>=read_from, source_time<from_ts, sym in syms;
        before:0!?[cand;();`sym`source!`sym`source;()];
        inwin:select source_time, sym, source, bid_prices, bid_sizes, ask_prices, ask_sizes, clock from `market_data
            where date within (neg[skew],skew)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts, sym in syms;
        `source_time xasc (cols before)#before,inwin
      }[syms;skew;read_from]}

/ The books of some symbols (see books_remote), read through the traced send.
/ @param h an open handle to the HDB
/ @param syms the market_data syms to read
/ @param range_from the window's inclusive lower bound
/ @param range_to the window's exclusive upper bound
/ @return the books, sorted by source_time
.qpipe.source.hdb_fx_markouts.read_books:{[h;syms;range_from;range_to]
    skew:(.qetl.source.reference_read .qpipe.source.hdb_fx_markouts.source_name)`date_skew;
    b:.qpipe.source.hdb_fx_markouts.book_bounds[range_from;range_to];
    .qetl.source.ipc[h;.qpipe.source.hdb_fx_markouts.books_remote[syms;skew;b`read_from];b`early;b`read_to]}

/ The window's executions, and the books they are scored against, both from the HDB: the
/ executions of the window, then the books of the symbols they need (reference_read's `symbols`).
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return `executions`market_data!(the window's executions; the books they need)
.qpipe.source.hdb_fx_markouts.query:{[h;range_from;range_to]
    rr:.qetl.source.reference_read .qpipe.source.hdb_fx_markouts.source_name;
    deals:.qetl.source.ipc[h;.qpipe.source.hdb_fx_markouts.deals_remote[rr`date_skew];range_from;range_to];
    if[0=count deals; :`executions`market_data!(deals;0#.qpipe.source.hdb_fx_markouts.supporting`market_data)];
    syms:rr[`symbols][.qpipe.transform.fx_markout_outputs.job_spec;deals];
    b:.qpipe.source.hdb_fx_markouts.book_bounds[range_from;range_to];
    books:.qetl.source.ipc[h;.qpipe.source.hdb_fx_markouts.books_remote[syms;rr`date_skew;b`read_from];b`early;b`read_to];
    `executions`market_data!(deals;books)}

/ The window's executions and a PRESENCE SUMMARY of their books: one row for each (pair, source)
/ that holds a book anywhere in the span the window's books are read over. What `ready` needs to
/ say whether the books are there (markout_core.unmet asks only whether there are any, and
/ whether each conversion pair has one of its declared source), without reading them: the
/ books themselves are read a batch at a time (batch_read, `read).
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return `executions`market_data!(the window's executions; one book per (pair, source) held)
.qpipe.source.hdb_fx_markouts.window:{[h;range_from;range_to]
    deals:.qpipe.source.hdb_fx_markouts.read_executions[h;range_from;range_to];
    empty:0#.qpipe.source.hdb_fx_markouts.supporting`market_data;
    if[0=count deals; :`executions`market_data!(deals;empty)];
    syms:(.qetl.source.reference_read .qpipe.source.hdb_fx_markouts.source_name)[`symbols][.qpipe.transform.fx_markout_outputs.job_spec;deals];
    if[0=count syms; :`executions`market_data!(deals;empty)];
    skew:(.qetl.source.reference_read .qpipe.source.hdb_fx_markouts.source_name)`date_skew;
    b:.qpipe.source.hdb_fx_markouts.book_bounds[range_from;range_to];
    held:.qetl.source.ipc[h;{[syms;skew;from_ts;to_ts]
        0!select source_time:last source_time, bid_prices:last bid_prices, bid_sizes:last bid_sizes,
            ask_prices:last ask_prices, ask_sizes:last ask_sizes, clock:last clock by sym, source from `market_data where date within (neg[skew],skew)+`date$(from_ts;to_ts),
            source_time>=from_ts, source_time<to_ts, sym in syms
      }[syms;skew];
      b`read_from;
      b`read_to];
    `executions`market_data!(deals;(cols empty)#held)}

/ The books of exactly these symbols over the window: a batch's own, as `query` reads them for the
/ whole window, restricted to the keys.
/ @param h an open handle to the HDB
/ @param range_from the window's inclusive lower bound
/ @param range_to the window's exclusive upper bound
/ @param syms the market_data syms
/ @return enlist[`market_data]!enlist books
.qpipe.source.hdb_fx_markouts.read_batch:{[h;range_from;range_to;syms]
    enlist[`market_data]!enlist .qpipe.source.hdb_fx_markouts.read_books[h;syms;range_from;range_to]}

/ The reference keys a batch of executions needs: reference_read's `symbols`, conversion pairs included.
/ @param rows the batch's executions
/ @return a symbol vector
/ @eg .qpipe.source.hdb_fx_markouts.needs[([] sym:enlist `USDPLN)]  ->  `EURUSD`EURPLN
.qpipe.source.hdb_fx_markouts.needs:{[rows]
    (.qetl.source.reference_read .qpipe.source.hdb_fx_markouts.source_name)[`symbols][.qpipe.transform.fx_markout_outputs.job_spec;rows]}

/ How the source reads a window a batch at a time (.qetl.source BATCH READ, source_batch_read.q).
.qpipe.source.hdb_fx_markouts.batch_read:`window`needs`read`keys`sort!(.qpipe.source.hdb_fx_markouts.window;.qpipe.source.hdb_fx_markouts.needs;.qpipe.source.hdb_fx_markouts.read_batch;enlist[`market_data]!enlist `sym;enlist[`market_data]!enlist `source_time)

/ How far the HDB's books reach, less the furthest horizon: a window ending at or before
/ this has every book its executions are scored against (#1927). The latest book is read
/ from the newest partition only; a recorder that has not landed any gives -0Wp, and a
/ -0Wp bound means nothing is complete yet, so no window is planned.
/ @param h an open handle to the HDB
/ @return the exclusive instant through which the books the scoring needs are present
/ @eg .qpipe.source.hdb_fx_markouts.complete_through[{2026.09.17D10:00:25}]  ->  2026.09.17D10:00:15.000000000
.qpipe.source.hdb_fx_markouts.complete_through:{[h]
    latest:.qetl.source.ipc_call[h;{[unused] exec max source_time from `market_data where date=max date};enlist 0b];
    / max of no rows is -0Wp, and arithmetic on it wraps to a far-future instant
    $[latest<=-0Wp; -0Wp; latest-0D|max (.qpipe.transform.fx_markout_outputs.job_spec)`horizons]}

/ Can this window be scored, or are its books absent? The core's gate (markout_core.unmet,
/ #1927): an execution with no book for one of its route's legs is a legitimate
/ unavailable row and is ready, but a window that has scoreable executions and NO books
/ at all is not, and neither is one whose conversion pair has no book of its declared
/ source, because every row it scored would be unavailable or no_rate and coverage would
/ then keep them for good. A window with no executions, or none a route can be built
/ for, has nothing to score and is ready.
/ @param batch `executions`market_data!(...) as the query returns them
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the unmet requirements, as a list of strings; () when ready
/ @eg .qpipe.source.hdb_fx_markouts.ready[.qpipe.source.hdb_fx_markouts.fixture[];2026.09.17D00:00;2026.09.18D00:00]  ->  ()
.qpipe.source.hdb_fx_markouts.ready:{[batch;range_from;range_to] / noqa: QF016 the hook's fixed signature
    .qpipe.transform.markout_core.unmet[.qpipe.transform.fx_domain.domain;.qpipe.transform.fx_markout_outputs.job_spec;batch`executions;batch`market_data]}

/ Three fills on 2026.09.17 and the books around them: a EURUSD buy at 1.1001 against
/ two sources, a USDPLN sell that routes EURUSD (inverted) and EURPLN, and a crypto
/ fill the calculation cannot route and so excludes.
/ @return the executions, as the HDB's executions holds them
/ @eg count .qpipe.source.hdb_fx_markouts.raw_executions[]  ->  3
.qpipe.source.hdb_fx_markouts.raw_executions:{[]
    ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:20 2026.09.17D10:00:21;
        venue:`a`a`binance_spot; sym:(`EURUSD;`USDPLN;`$"BTC-USDT"); side:1 -1 1;
        size:1e6 1e6 0.5; price:1.1001 3.99 62000f; fill_id:`x1`y1`z1; clock:3#`venue)}

/ The books: EURUSD on sources a and b, and EURPLN on a. Each is re-quoted within
/ the reference's max_age (.qf.mkt.reference_max_age, 5s) of every instant a fill is
/ scored at, with the same prices, because a book older than that is not a reference
/ (#1410): the +18s and +25s rows repeat what was already quoted.
/ @return the books, as the HDB's market_data holds them
/ @eg count .qpipe.source.hdb_fx_markouts.raw_market_data[]  ->  12
.qpipe.source.hdb_fx_markouts.raw_market_data:{[]
    ([] source_time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00.5 2026.09.17D10:00:08 2026.09.17D09:59:59 2026.09.17D10:00:25,2026.09.17D10:00:18 2026.09.17D10:00:18 2026.09.17D10:00:18 2026.09.17D10:00:25 2026.09.17D10:00:25;
        sym:`EURUSD`EURUSD`EURUSD`EURUSD`EURUSD`EURPLN`EURPLN`EURUSD`EURUSD`EURPLN`EURUSD`EURUSD;
        source:`a`a`a`b`b`a`a`a`b`a`a`b;
        bid_prices:(enlist 1.0999;enlist 1.1004;enlist 1.1009;enlist 1.1003;enlist 1.1008;enlist 4.9999;enlist 5.0999;enlist 1.1009;enlist 1.1008;enlist 4.9999;enlist 1.1009;enlist 1.1008);
        bid_sizes:12#enlist enlist 1e6;
        ask_prices:(enlist 1.1001;enlist 1.1006;enlist 1.1011;enlist 1.1007;enlist 1.1010;enlist 5.0001;enlist 5.1001;enlist 1.1011;enlist 1.1010;enlist 5.0001;enlist 1.1011;enlist 1.1010);
        ask_sizes:12#enlist enlist 1e6; clock:12#`venue)}

/ What query returns for the fixture's day.
/ @return `executions`market_data!(raw_executions[];raw_market_data[])
.qpipe.source.hdb_fx_markouts.fixture:{[] `executions`market_data!(.qpipe.source.hdb_fx_markouts.raw_executions[];.qpipe.source.hdb_fx_markouts.raw_market_data[])}

.qetl.source.define[.qpipe.source.hdb_fx_markouts.source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`supporting`reference_read`batch_read`complete_through`ready!
    (.qpipe.source.hdb_fx_markouts.source_name;`executions;.qpipe.source.hdb_fx_markouts.time_column;.qpipe.source.hdb_fx_markouts.row_key;.qpipe.source.hdb_fx_markouts.columns;.qpipe.source.hdb_fx_markouts.types;.qpipe.source.hdb_fx_markouts.query;.qpipe.source.hdb_fx_markouts.fixture;.qpipe.source.hdb_fx_markouts.tz;.qpipe.source.hdb_fx_markouts.supporting;.qpipe.source.hdb_fx_markouts.reference_read;.qpipe.source.hdb_fx_markouts.batch_read;.qpipe.source.hdb_fx_markouts.complete_through;.qpipe.source.hdb_fx_markouts.ready)];

\d .
