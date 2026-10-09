/ exec_bars.q - the whole of the one-minute fill bars job (.qpipe.job.exec_bars).
/ .
/ Subscribes to `executions` and publishes `exec_bar`: open, high, low, close,
/ volume, vwap and fill count per sym per minute, on the fill's source_time.
/ .
/ WHAT IS IN THIS FILE: the input shape, the transform's contract with its
/ worked example, and the declaration. The windows, the lateness rule, end of
/ day and replay are the bars kind's (src/etl/core/bars.q, #946); the
/ aggregation is shared with hdb_exec_bars_backfill
/ (src/etl/transforms/exec_bars.q).

\d .qpipe.job.exec_bars

/ The fills the aggregation reads, with the window the kind cuts for it.
fill_rows:update bar_start:`timestamp$() from .qetl.plant.columns[`executions;`source_time`sym`size`price]

\d .

.qetl.transform.define[`exec_bars;`inputs`output`fn`examples!(
    enlist[`tape]!enlist .qpipe.job.exec_bars.fill_rows;
    .qetl.plant.published `exec_bar;
    .qpipe.transform.exec_bars.bars;
    / EURUSD: three fills in the 10:00 minute, arriving out of order (open is
    / the earliest by source_time, not the first to arrive); a fill AT 10:01:00
    / opens the next window (half-open); USDJPY has one fill. 10:02 has none,
    / so it has no bar. vwap = (1e6*1.10 + 2e6*1.11 + 3e6*1.12) / 6e6.
    enlist `inputs`expected!(
        enlist[`tape]!enlist ([] source_time:2026.10.09D10:00:05 2026.10.09D10:00:50 2026.10.09D10:00:20 2026.10.09D10:01:00 2026.10.09D10:00:30;
            sym:`EURUSD`EURUSD`EURUSD`EURUSD`USDJPY;
            size:1e6 3e6 2e6 1e6 5e5;
            price:1.10 1.12 1.11 1.20 150.0;
            bar_start:2026.10.09D10:00:00 2026.10.09D10:00:00 2026.10.09D10:00:00 2026.10.09D10:01:00 2026.10.09D10:00:00);
        ([] sym:`EURUSD`EURUSD`USDJPY; bar_start:2026.10.09D10:00:00 2026.10.09D10:01:00 2026.10.09D10:00:00;
            open:1.10 1.20 150.0; high:1.12 1.20 150.0; low:1.10 1.20 150.0; close:1.12 1.20 150.0;
            volume:6e6 1e6 5e5; vwap:(6.68%6;1.20;150.0); trades:3 1 1)))];

/ A one-minute bar, closed 5 seconds after its minute ends (a fill may be that
/ late and still amend it), looked for every second. The kind also closes the
/ day's last windows at end of day and restores open ones from the log.
.qetl.job.stream.at_bars[`exec_bars;`procname`events`event_time`transform`publishes`width`lateness`period`start_with_all`note!(
    `exec_bars1;
    `executions;
    `source_time;
    `exec_bars;
    `exec_bar;
    .qpipe.transform.exec_bars.width;
    .qpipe.transform.exec_bars.lateness;
    0D00:00:01;
    0b;
    "time bars over the unified fills tape; the window is the fill's source_time")];
