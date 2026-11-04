/ eq_fills_backfill.q - equity fills from a csv drop into `executions`
/ (.qpipe.job.eq_fills_backfill, #1981).
/ .
/ A declaration over .qetl.job.bounded like every worker: windows, retries, coverage,
/ checkpoints and dry-run are the shell's. The source (.qpipe.source.eq_fills) has already
/ mapped the file's columns into the canonical execution fields and stamped the clock; this
/ worker adds the two columns a drop does not carry (book, product: null, as for any fill
/ with no desk) and holds the window to the clock rule before it is published.
/ .
/ WHERE THE ROWS GO. No `io` is declared, so the process decides. Run as eq_fills_backfill1,
/ scripts/processes/torq_backfill.q makes it the HDB writer, partitioned by the source's
/ time column, source_time: the fill lands in the partition of its own execution date, and its
/ `time` is that execution time, not the receipt time a live fill carries. Loaded in plain q
/ it is .qetl.io.memory, and the rows are in an `executions` table in that process.
/ .
/ A SECOND WORKER ON `executions` MUST NOT SHARE THIS ONE'S source_version. Coverage is kept
/ per dataset, partition and version, so two files backfilled under one version would let the
/ second skip every window the first covered. There is deliberately no default version: a
/ corrected drop is a new version, and a run must say which it is.
/ .
/ IDEMPOTENCE. The row key is (venue; fill_id), the source's, so a window refetched at the
/ same version is skipped by coverage and one refetched at another is upserted, not appended.

\d .

/ Refuse a batch that cannot be a set of fills: a non-positive size or price, a null fill id,
/ a pair of fills with one (venue; fill_id), or a row with no clock. Shaped correctly and still
/ wrong is what a check is for; the source has already refused what it could not read.
/ @param batch the transformed rows of one window
/ @return a table check/status/detail, one row per failing condition; empty when the batch passes
/ @eg count .qpipe.job.eq_fills_backfill.quality_check[.qetl.plant.published `executions]  ->  0
.qpipe.job.eq_fills_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    bad_size:select from batch where not size>0;
    bad_price:select from batch where not price>0;
    dup:select from batch where 1<(count each group flip (venue;fill_id)) flip (venue;fill_id);
    bad_clock:select from batch where not clock in .qetl.source.fills.clocks;
    raze {[nm;t]
        if[0=count t; :.qetl.job.bounded.no_failures[]];
        ([] check:count[t]#nm; status:count[t]#`breach; detail:.qf.util.full each t)
      }'[`nonpositive_size`nonpositive_price`duplicate_fill`unstamped_clock;(bad_size;bad_price;dup;bad_clock)]}

/ What a window SAW beyond its row count: fills, symbols, venues, the clocks and the first and
/ last execution time. Every aggregate survives an empty window, which is legal.
/ @param batch the transformed rows of one window
/ @return a dictionary of labels for the window's materialisation
/ @eg .qpipe.job.eq_fills_backfill.facts[0#.qetl.plant.published `executions]
.qpipe.job.eq_fills_backfill.facts:{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    `fills`symbols`venues`clocks`first_fill`last_fill!(count batch;count distinct batch`sym;count distinct batch`venue;
        "," sv string distinct batch`clock;min batch`source_time;max batch`source_time)}

\d .

/ The source's rows become canonical executions: the same fields, with the two a drop does not carry.
.qetl.transform.define[`eq_fills_to_executions;`inputs`output`fn`examples!(
    enlist[`batch]!enlist .qpipe.source.eq_fills.fixture[];
    .qetl.plant.published `executions;
    {[batch]
        select source_time, sym, venue, side, size, price, fee, fee_ccy, fill_id, book:`, product:`, clock from batch};
    enlist `inputs`expected!(
        enlist[`batch]!enlist 2#.qpipe.source.eq_fills.fixture[];
        ([] source_time:2026.02.25D14:30:00.020000000 2026.02.25D14:30:00.500000000; sym:`AAPL`META; venue:`XNAS`XNAS;
            side:1 -1; size:100 10f; price:271.6 643.2; fee:0.35 0.1; fee_ccy:`USD`USD; fill_id:`x0001`x0002;
            book:``; product:``; clock:`venue`venue)))];

.qetl.job.bounded.define[`eq_fills_backfill;
    `source`dataset`width`transform`check`facts`procname`note!
        (`eq_fills;`executions;1D;`eq_fills_to_executions;
         .qpipe.job.eq_fills_backfill.quality_check;.qpipe.job.eq_fills_backfill.facts;
         `eq_fills_backfill1;
         "bounded: reads an equity fills csv drop and writes canonical executions, on the declared clock")];
