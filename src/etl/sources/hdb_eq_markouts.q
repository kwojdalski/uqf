/ hdb_eq_markouts.q - the equity executions to mark out, and the books to mark them against, from the
/ local HDB (.qpipe.source.hdb_eq_markouts, #1981).
/ .
/ The live job (eq_markout) scores fills as they stream past, from buffers that die with their
/ process: a fill it never saw - it was down, it restarted, or the fill reached the HDB through a
/ bounded worker (eq_fills_backfill writes the HDB and nothing live) - is never marked out. This is
/ what its bounded twin reads instead, two inputs from what the HDB holds:
/ .
/   `executions   the canonical executions end of day saved, narrowed to the equity venues
/                 (.qetl.venue.venues_of `equity), with the clock the core's clock
/                 rule needs. The PRIMARY input: it owns the window and its rows are what a run
/                 counts. The FX and crypto fills of the same table are not read
/   `market_data  the equity venues' TOP OF BOOK for every symbol the executions need, at exactly the
/                 instants the scoring asks about: each symbol at the execution and at every horizon, as
/                 the latest book of each venue at or before the instant, looking back to the reference's
/                 age limit and across midnight into the earlier partitions. A supporting input: context,
/                 never cut to the window
/ .
/ MEMORY STAYS PROPORTIONAL TO THE EXECUTIONS (#2288). The books are not selected into this process: the as-of
/ join runs where the partitions are memory-mapped (the HDB, .qetl.mapped.tob_at) and only the matched books
/ come back. No query here selects market_data with a where beyond the date, and none reads a depth column.
/ .
/ Nothing here knows a vendor: both tables are the canonical ones, and the scoring is the worker's
/ transform, the live job's own (src/etl/transforms/eq_markout_outputs.q), so a refilled row is the
/ row the live job publishes. The calendar (sessions, halts) is the transform's: a window reads the
/ books a horizon past the close needs and the core marks them unavailable.
/ .
/ The window is on the execution's source_time, the clock it is scored on, not the plant's `time` the
/ HDB is partitioned and sorted by: a fill stamped at the plant after midnight can carry a source_time
/ before it, so each query reads one date either side. (eq_fills_backfill partitions by source_time,
/ so its fills sit in their own date.)
/ .
/ Live: export UQF_SOURCE_CRED_HDB_EQ_MARKOUTS=localhost:<hdb1's port> in the shell
/ `uqs job backfill` runs from. Without it, the fixture below is used.

\d .

.qpipe.source.hdb_eq_markouts.source_name:`hdb_eq_markouts

/ The primary input: the HDB's executions, narrowed to what scoring reads.
.qpipe.source.hdb_eq_markouts.columns:`source_time`venue`sym`side`size`price`fill_id`clock
.qpipe.source.hdb_eq_markouts.types:"pssjffss"

.qpipe.source.hdb_eq_markouts.time_column:`source_time

/ An execution is (venue; fill_id); the worker writes by its own target_key.
.qpipe.source.hdb_eq_markouts.row_key:`venue`fill_id

/ The supporting input: the books, read by the transform as `market_data`.
.qpipe.source.hdb_eq_markouts.supporting:enlist[`market_data]!enlist .qetl.plant.columns[`market_data;`source_time`sym`source`bid`ask`clock]

/ .u.upd stamps `time` in UTC, and the sources' own stamps are UTC.
.qpipe.source.hdb_eq_markouts.tz:`UTC

/ The market_data symbols the window's references are read for: the legs of every execution's
/ route (the live job's own `legs`), and the conversion pairs the spec reads a rate from (none: the
/ shipped spec reports in the instruments' own currency).
/ @param spec the job's spec
/ @param rows the window's executions
/ @return a symbol vector, distinct
/ @eg .qpipe.source.hdb_eq_markouts.reference_symbols[.qpipe.transform.eq_markout_outputs.job_spec;([] sym:enlist `AAPL)]  ->  enlist `AAPL
.qpipe.source.hdb_eq_markouts.reference_symbols:{[spec;rows]
    legs:distinct raze .qpipe.transform.eq_markout_outputs.legs rows;
    distinct legs,(.qpipe.transform.markout_core.conversion_pairs[.qpipe.transform.eq_domain.domain;spec;rows])`sym}

/ What the source reads of its books, declared (.qetl.source REFERENCE READ):
/   date_skew     1 day either side: the window is on source_time but the HDB is
/                 partitioned on the plant's `time`, which can fall on the other side of
/                 midnight from the venue's stamp
/   lookback_cap  7 days, for a spec whose max_age is 0Wn. The shipped spec's max_age is
/                 finite (one minute), so the look-back is that - a book older than it at an
/                 execution's instant is not a reference, and reading further back could only
/                 fetch rows scoring would ignore
/   symbols       reference_symbols
.qpipe.source.hdb_eq_markouts.reference_read:`date_skew`lookback_cap`symbols!(1;7D;.qpipe.source.hdb_eq_markouts.reference_symbols)

/ The window's executions, and the books they are scored against, both from the HDB. The
/ executions are the equity venues' alone, and so are the books. The books are the top of book at
/ the instants the scoring asks about (see the header).
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return `executions`market_data!(the window's executions; the books they need)
.qpipe.source.hdb_eq_markouts.query:{[h;range_from;range_to]
    rr:.qetl.source.reference_read .qpipe.source.hdb_eq_markouts.source_name;
    skew:rr`date_skew;
    ven:.qetl.venue.venues_of `equity;
    deals:.qetl.source.ipc[h;{[ven;skew;from_ts;to_ts]
        `source_time xasc select source_time, venue, sym, side, size, price, fill_id, clock from `executions
            where date within (neg[skew],skew)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts, venue in ven
      }[ven;skew];range_from;range_to];
    if[0=count deals; :`executions`market_data!(deals;0#.qpipe.source.hdb_eq_markouts.supporting`market_data)];
    spec:.qpipe.transform.eq_markout_outputs.job_spec;
    early:.qpipe.transform.eq_markout_outputs.lookback[];
    lb:.qetl.source.reference_lookback[.qpipe.source.hdb_eq_markouts.source_name;spec`max_age;early];
    legs:.qpipe.transform.markout_core.legs_of[.qpipe.transform.eq_domain.domain;spec;deals];
    probes:.qetl.mapped.probes[legs;deals`source_time;.qpipe.transform.markout_core.horizons_of spec];
    `executions`market_data!(deals;.qetl.mapped.read_books[h;probes;range_from-lb;skew;ven])}

/ How far the HDB's equity books reach, less the furthest horizon: a window ending at or
/ before this has every book its executions are scored against (#1927). The latest book is
/ read from the newest partition only, and only equity's: an FX or crypto book newer than the
/ last equity one would overstate it. A feed that has not landed any gives -0Wp, and a -0Wp bound
/ means nothing is complete yet, so no window is planned.
/ @param h an open handle to the HDB
/ @return the exclusive instant through which the books the scoring needs are present
/ @eg .qpipe.source.hdb_eq_markouts.complete_through[{2026.02.25D15:02:00}]  ->  2026.02.25D15:01:00.000000000
.qpipe.source.hdb_eq_markouts.complete_through:{[h]
    latest:.qetl.source.ipc_call[h;{[a] .qetl.mapped.newest a};enlist enlist[`market]!enlist `equity];
    / max of no rows is -0Wp, and arithmetic on it wraps to a far-future instant
    $[latest<=-0Wp; -0Wp; latest-.qpipe.transform.markout_core.reach .qpipe.transform.eq_markout_outputs.job_spec]}

/ Can this window be scored, or are its books absent? An execution with no book for its symbol is a
/ legitimate unavailable row and is ready; a window that has equity executions and NO books at all is
/ not ready, because every row it scored would be unavailable and coverage would then keep them for
/ good (#1927). The core's own `unmet` says which, so the gate and the scoring agree.
/ @param batch `executions`market_data!(...) as the query returns them
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the unmet requirements, as a list of strings; () when ready
/ @eg .qpipe.source.hdb_eq_markouts.ready[.qpipe.source.hdb_eq_markouts.fixture[];2026.02.25D00:00;2026.02.26D00:00]  ->  ()
.qpipe.source.hdb_eq_markouts.ready:{[batch;range_from;range_to] / noqa: QF016 the hook's fixed signature
    .qpipe.transform.markout_core.unmet[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec;batch`executions;batch`market_data]}

/ Four fills on 2026.02.25, a winter day (New York at UTC-5, so the session is 14:30 to 21:00 UTC):
/ an AAPL buy of 100 at 271.50 mid-session (e1), an AAPL buy stamped on the RECEIPT clock (e2: the
/ books are the venue's, so it scores as clock_mismatch), a META sell of 10 at 643.25 ten seconds
/ before the close (e3), and a EURUSD fill on another venue, which belongs to another dataset and is
/ not read at all (e4). In time order, as a query returns them.
/ @return the executions, as the HDB's executions holds them
/ @eg count .qpipe.source.hdb_eq_markouts.raw_executions[]  ->  4
.qpipe.source.hdb_eq_markouts.raw_executions:{[]
    ([] source_time:2026.02.25D15:00:00 2026.02.25D15:00:10 2026.02.25D20:59:50 2026.02.25D21:00:30;
        venue:`XNAS`XNAS`XNAS`a; sym:`AAPL`AAPL`META`EURUSD; side:1 1 -1 1;
        size:100 50 10 1e6; price:271.5 271.75 643.25 1.1; fill_id:`e1`e2`e3`e4;
        fee:0.35 0.2 0.1 0f; fee_ccy:4#`USD; clock:`venue`receipt`venue`receipt)}

/ The books: AAPL on XNAS around the first fill (271.25/271.50 at 15:00:00, then 271.50/271.75 at
/ 15:00:03, 271.75/272.00 at 15:00:20 and 272.00/272.25 at 15:00:50), META on XNAS around the
/ close (642.75/643.25 at 20:59:49, 642.25/642.75 at 20:59:54, and 640.75/641.25 at 21:00:20, after
/ it), and a EURUSD book on another source, which the equity venues' filter leaves out.
/ @return the books, as the HDB's market_data holds them
/ @eg count .qpipe.source.hdb_eq_markouts.raw_market_data[]  ->  8
.qpipe.source.hdb_eq_markouts.raw_market_data:{[]
    ([] source_time:2026.02.25D15:00:00 2026.02.25D15:00:03 2026.02.25D15:00:20 2026.02.25D15:00:50 2026.02.25D20:59:49 2026.02.25D20:59:54 2026.02.25D21:00:20 2026.02.25D15:00:00;
        sym:`AAPL`AAPL`AAPL`AAPL`META`META`META`EURUSD;
        source:(7#`XNAS),`a;
        market:(7#`equity),`fx;
        bid:271.25 271.5 271.75 272 642.75 642.25 640.75 1.09f;
        ask:271.5 271.75 272 272.25 643.25 642.75 641.25 1.11f;
        clock:(7#`venue),`receipt)}

/ What query returns for the fixture's day: the equity venue's fills and books, not the EURUSD ones.
/ @return `executions`market_data!(the XNAS executions; the XNAS books)
/ @eg key .qpipe.source.hdb_eq_markouts.fixture[]  ->  `executions`market_data
.qpipe.source.hdb_eq_markouts.fixture:{[]
    ven:.qetl.venue.venues_of `equity;
    e:select source_time, venue, sym, side, size, price, fill_id, clock from .qpipe.source.hdb_eq_markouts.raw_executions[] where venue in ven;
    b:select source_time, sym, source, bid, ask, clock from .qpipe.source.hdb_eq_markouts.raw_market_data[] where source in ven;
    `executions`market_data!(e;b)}

.qetl.source.define[.qpipe.source.hdb_eq_markouts.source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`supporting`reference_read`complete_through`ready!
    (.qpipe.source.hdb_eq_markouts.source_name;`executions;.qpipe.source.hdb_eq_markouts.time_column;.qpipe.source.hdb_eq_markouts.row_key;.qpipe.source.hdb_eq_markouts.columns;.qpipe.source.hdb_eq_markouts.types;.qpipe.source.hdb_eq_markouts.query;.qpipe.source.hdb_eq_markouts.fixture;.qpipe.source.hdb_eq_markouts.tz;.qpipe.source.hdb_eq_markouts.supporting;.qpipe.source.hdb_eq_markouts.reference_read;.qpipe.source.hdb_eq_markouts.complete_through;.qpipe.source.hdb_eq_markouts.ready)];

\d .
