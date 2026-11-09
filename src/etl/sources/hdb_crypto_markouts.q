/ hdb_crypto_markouts.q - the crypto executions to mark out, and the books to mark them against, from the
/ local HDB (.qpipe.source.hdb_crypto_markouts, #1936).
/ .
/ The live job (crypto_markout) scores fills as they stream past, from buffers that die
/ with their process: a fill it never saw - it was down, restarting, or the fill predates
/ it - is never marked out. This is what its bounded twin reads instead, two inputs from
/ what the HDB holds:
/ .
/   `executions   the canonical executions end of day saved, with the fee and the clock
/                 the crypto measures and the core's clock rule need. The PRIMARY input:
/                 it owns the window and its rows are what a run counts
/   `market_data  the TOP OF BOOK of every pair an execution's route needs, at exactly the instants the
/                 scoring asks about: each leg at the execution and at every horizon, as the latest book of
/                 each venue at or before the instant, looking back to the reference's age limit and across
/                 midnight into the earlier partitions. A supporting input: context, never cut to the window
/ .
/ MEMORY STAYS PROPORTIONAL TO THE EXECUTIONS (#2288). The books are not selected into this process: the as-of
/ join runs where the partitions are memory-mapped (the HDB, .qetl.mapped.tob_at) and only the matched books
/ come back. No query here selects market_data with a where beyond the date, and none reads a depth column.
/ .
/ Nothing here knows a vendor: both tables are the canonical ones, and the scoring is the
/ worker's transform, the live job's own (src/etl/transforms/crypto_markout_outputs.q), so
/ a refilled row is the row the live job publishes. Executions that are not crypto pairs
/ (the FX fills share the table) are read and then excluded by the core as `no_route`,
/ exactly as the live job's `keep` drops them.
/ .
/ The window is on the execution's source_time, the clock it is scored on, not the plant's
/ `time` the HDB is partitioned and sorted by: a fill stamped at the plant after midnight
/ can carry a source_time before it, so each query reads one date either side. A fill's
/ source_time is the VENUE's where the recorder sends it (cryptorust#1062), so the window
/ cuts on the venue's clock; a fill from before the column is the plant's receipt time on
/ the receipt clock and, scored against venue books, is a `clock_mismatch row.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_CRYPTO_MARKOUTS=localhost:<hdb1's port> in the shell
/ `uqs job backfill` runs from. Without it, the fixture below is used.

\d .

.qpipe.source.hdb_crypto_markouts.source_name:`hdb_crypto_markouts

/ The primary input: the HDB's executions, narrowed to what scoring reads.
.qpipe.source.hdb_crypto_markouts.columns:`source_time`venue`sym`side`size`price`fee`fee_ccy`fill_id`clock
.qpipe.source.hdb_crypto_markouts.types:"pssjfffsss"

.qpipe.source.hdb_crypto_markouts.time_column:`source_time

/ An execution is (venue; fill_id); the worker writes by its own target_key.
.qpipe.source.hdb_crypto_markouts.row_key:`venue`fill_id

/ The supporting input: the books, read by the transform as `market_data`.
.qpipe.source.hdb_crypto_markouts.supporting:enlist[`market_data]!enlist .qetl.plant.columns[`market_data;`source_time`sym`source`bid`ask`clock]

/ .u.upd stamps `time` in UTC, and the sources' own stamps are UTC.
.qpipe.source.hdb_crypto_markouts.tz:`UTC

/ The market_data symbols the window's references are read for: the legs of every
/ execution's route (the live job's own `legs`), and the conversion pairs the spec
/ reads a rate from (none while every rate is fixed, as the shipped spec's is).
/ @param spec the job's spec
/ @param rows the window's executions
/ @return a symbol vector, distinct
/ @eg .qpipe.source.hdb_crypto_markouts.reference_symbols[.qpipe.transform.crypto_markout_outputs.job_spec;([] sym:enlist `$"BTC-USDT")]  ->  enlist `$"BTC-USDT"
.qpipe.source.hdb_crypto_markouts.reference_symbols:{[spec;rows]
    legs:distinct raze .qpipe.transform.crypto_markout_outputs.legs rows;
    distinct legs,(.qpipe.transform.markout_core.conversion_pairs[.qpipe.transform.crypto_markout.domain;spec;rows])`sym}

/ What the source reads of its books, declared (.qetl.source REFERENCE READ):
/   date_skew     1 day either side: the window is on source_time but the HDB is
/                 partitioned on the plant's `time`, which can fall on the other side of
/                 midnight from the venue's stamp
/   lookback_cap  7 days, for a spec whose max_age is 0Wn. The shipped spec's max_age is
/                 finite (.qf.mkt.reference_max_age), so the look-back is that - a book
/                 older than it at an execution's instant is not a reference (#1410), and
/                 reading further back could only fetch rows scoring would ignore
/   symbols       reference_symbols
.qpipe.source.hdb_crypto_markouts.reference_read:`date_skew`lookback_cap`symbols!(1;7D;.qpipe.source.hdb_crypto_markouts.reference_symbols)

/ The window's executions, and the books they are scored against, both from the HDB: the executions of
/ the window, then the top of book at the instants they are scored at (see the header).
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return `executions`market_data!(the window's executions; the books they need)
.qpipe.source.hdb_crypto_markouts.query:{[h;range_from;range_to]
    rr:.qetl.source.reference_read .qpipe.source.hdb_crypto_markouts.source_name;
    skew:rr`date_skew;
    deals:.qetl.source.ipc[h;{[skew;from_ts;to_ts]
        `source_time xasc select source_time, venue, sym, side, size, price, fee, fee_ccy, fill_id, clock from `executions
            where date within (neg[skew],skew)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts
      }[skew];range_from;range_to];
    / not the equity venues' fills (#2574): rows would drop them, and their books are not fetched for nothing
    deals:select from deals where not venue in .qpipe.transform.eq_markout_outputs.venues;
    if[0=count deals; :`executions`market_data!(deals;0#.qpipe.source.hdb_crypto_markouts.supporting`market_data)];
    spec:.qpipe.transform.crypto_markout_outputs.job_spec;
    early:.qpipe.transform.crypto_markout_outputs.lookback[];
    lb:.qetl.source.reference_lookback[.qpipe.source.hdb_crypto_markouts.source_name;spec`max_age;early];
    legs:.qpipe.transform.markout_core.legs_of[.qpipe.transform.crypto_markout.domain;spec;deals];
    probes:.qetl.mapped.probes[legs;deals`source_time;.qpipe.transform.markout_core.horizons_of spec];
    `executions`market_data!(deals;.qetl.mapped.read_books[h;probes;range_from-lb;skew;`all])}

/ How far the HDB's crypto books reach, less the furthest horizon: a window ending at or
/ before this has every book its executions are scored against (#1927). The latest book is
/ read from the newest partition only, and only crypto's: an FX book newer than the last
/ crypto one would overstate it. A recorder that has not landed any gives -0Wp, and a -0Wp
/ bound means nothing is complete yet, so no window is planned.
/ @param h an open handle to the HDB
/ @return the exclusive instant through which the books the scoring needs are present
/ @eg .qpipe.source.hdb_crypto_markouts.complete_through[{2026.09.17D10:00:25}]  ->  2026.09.17D10:00:15.000000000
.qpipe.source.hdb_crypto_markouts.complete_through:{[h]
    latest:.qetl.source.ipc_call[h;{[a] .qetl.mapped.newest a};enlist enlist[`market]!enlist `crypto];
    / max of no rows is -0Wp, and arithmetic on it wraps to a far-future instant
    $[latest<=-0Wp; -0Wp; latest-.qpipe.transform.markout_core.reach .qpipe.transform.crypto_markout_outputs.job_spec]}

/ Can this window be scored, or are its books absent? An execution with no book for its pair is
/ a legitimate unavailable row and is ready; a window that has crypto executions and NO books at
/ all is not ready, because every row it scored would be unavailable and coverage would then
/ keep them for good (#1927); and so is a window whose spec reads a conversion rate from a book
/ that is absent. The core's own `unmet` says which, so the gate and the scoring agree.
/ @param batch `executions`market_data!(...) as the query returns them
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the unmet requirements, as a list of strings; () when ready
/ @eg .qpipe.source.hdb_crypto_markouts.ready[.qpipe.source.hdb_crypto_markouts.fixture[];2026.09.17D00:00;2026.09.18D00:00]  ->  ()
.qpipe.source.hdb_crypto_markouts.ready:{[batch;range_from;range_to] / noqa: QF016 the hook's fixed signature
    .qpipe.transform.markout_core.unmet[.qpipe.transform.crypto_markout.domain;.qpipe.transform.crypto_markout_outputs.job_spec;batch`executions;batch`market_data]}

/ Four fills on 2026.09.17 and the books around them: a BTC-USDT buy at 62000 with a quote-currency
/ fee, an ETH-USDT sell with a base-currency fee, a BTC-USDT buy stamped on the RECEIPT clock (the
/ books are the venue's, so it scores as clock_mismatch), and a EURUSD fill the calculation cannot
/ route and so excludes.
/ @return the executions, as the HDB's executions holds them
/ @eg count .qpipe.source.hdb_crypto_markouts.raw_executions[]  ->  4
.qpipe.source.hdb_crypto_markouts.raw_executions:{[]
    ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:20 2026.09.17D10:00:21 2026.09.17D10:00:22;
        venue:`a`b`a`a; sym:(`$"BTC-USDT";`$"ETH-USDT";`$"BTC-USDT";`EURUSD); side:1 -1 1 1;
        size:0.25 2 0.1 1e6; price:62000 3000 62005 1.1f; fee:15.5 0.002 0 0f; fee_ccy:`USDT`ETH``;
        fill_id:`f1`f2`f3`f4; clock:`venue`venue`receipt`receipt)}

/ The books: BTC-USDT on venues a and b around the first fill, ETH-USDT on both around the second,
/ and BTC-USDT on a around the third (which is on the receipt clock, so these venue-clock books are
/ no reference for it). Each is re-quoted within the reference's max_age
/ (.qf.mkt.reference_max_age, 5s) of every instant a fill is scored at, because a book older than that
/ is not a reference (#1410). In order: BTC a 62000/62010 and b 62004/62008 at 10:00:00, BTC a
/ 62010/62020 at 10:00:09; ETH a 2999/3001 and b 2998/3002 at 10:00:19; BTC a 62005/62015 at
/ 10:00:20; ETH a 2997/2999 and b 2996/3000 at 10:00:21; ETH a 2990/2992 and b 2989/2993 at
/ 10:00:28; BTC a 62005/62015 at 10:00:30.
/ @return the books, as the HDB's market_data holds them
/ @eg count .qpipe.source.hdb_crypto_markouts.raw_market_data[]  ->  11
.qpipe.source.hdb_crypto_markouts.raw_market_data:{[]
    ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:09 2026.09.17D10:00:19 2026.09.17D10:00:19 2026.09.17D10:00:20 2026.09.17D10:00:21 2026.09.17D10:00:21 2026.09.17D10:00:28 2026.09.17D10:00:28 2026.09.17D10:00:30;
        sym:`$("BTC-USDT";"BTC-USDT";"BTC-USDT";"ETH-USDT";"ETH-USDT";"BTC-USDT";"ETH-USDT";"ETH-USDT";"ETH-USDT";"ETH-USDT";"BTC-USDT");
        source:`a`b`a`a`b`a`a`b`a`b`a;
        bid:62000 62004 62010 2999 2998 62005 2997 2996 2990 2989 62005f;
        ask:62010 62008 62020 3001 3002 62015 2999 3000 2992 2993 62015f;
        clock:11#`venue)}

/ What query returns for the fixture's day.
/ @return `executions`market_data!(raw_executions[];raw_market_data[])
/ @eg key .qpipe.source.hdb_crypto_markouts.fixture[]  ->  `executions`market_data
.qpipe.source.hdb_crypto_markouts.fixture:{[] `executions`market_data!(.qpipe.source.hdb_crypto_markouts.raw_executions[];.qpipe.source.hdb_crypto_markouts.raw_market_data[])}

.qetl.source.define[.qpipe.source.hdb_crypto_markouts.source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`supporting`reference_read`complete_through`ready!
    (.qpipe.source.hdb_crypto_markouts.source_name;`executions;.qpipe.source.hdb_crypto_markouts.time_column;.qpipe.source.hdb_crypto_markouts.row_key;.qpipe.source.hdb_crypto_markouts.columns;.qpipe.source.hdb_crypto_markouts.types;.qpipe.source.hdb_crypto_markouts.query;.qpipe.source.hdb_crypto_markouts.fixture;.qpipe.source.hdb_crypto_markouts.tz;.qpipe.source.hdb_crypto_markouts.supporting;.qpipe.source.hdb_crypto_markouts.reference_read;.qpipe.source.hdb_crypto_markouts.complete_through;.qpipe.source.hdb_crypto_markouts.ready)];

\d .
