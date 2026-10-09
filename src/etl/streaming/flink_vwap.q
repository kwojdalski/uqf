/ flink_vwap.q - per-symbol VWAP windows computed by Apache Flink, each window
/ published once (.qpipe.job.flink_vwap).
/ .
/ Reads `flink_vwap_raw`; publishes `flink_vwap`.
/ .
/ Flink does the windowing outside q (external/flink_vwap_streamer.py runs a
/ tumbling-window SQL job and publishes each closed window). Its result
/ stream is at-least-once: a restarted Flink job, or one replaying a Kafka
/ source from its last checkpoint, emits windows it has emitted before. A
/ window is identified by (sym;window_end), so this job keeps the highest
/ window_end published per sym and drops anything at or below it - the
/ (partition;offset) high-water mark of kafka_flow.q, keyed on the window
/ instead. A window Flink closes is final (the SQL is append-only), so
/ dropping a repeat never drops a correction.
/ .
/ The marks are this process's state and start empty: a repeat that
/ straddles a restart of flink_vwap1 itself is not caught.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qpipe.job.flink_vwap

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `flink_vwap;

/ sym -> the latest window_end already published for it. An unseen sym gives
/ 0Np, which every timestamp compares above, so it keeps every row.
high_water:(`symbol$())!`timestamp$();

/ Publish the windows in this batch that have not been published before.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    if[(0=count x) or not t=`flink_vwap_raw; :()];
    fresh:x where x[`window_end] > .qpipe.job.flink_vwap.high_water x`sym;
    if[0=count fresh; :()];
    / one row per window, the first kept, in batch order
    fresh:fresh asc value first each group flip fresh`sym`window_end;
    .qpipe.job.flink_vwap.publish[`flink_vwap;select sym, window_end, vwap, volume, n from fresh];
    m:exec max window_end by sym from fresh;
    hw:.qpipe.job.flink_vwap.high_water;
    `.qpipe.job.flink_vwap.high_water set hw,(key m)!(hw key m)|value m;
    }

\d .

.qetl.job.stream.define[`flink_vwap;`procname`subscribe_to`publishes`on_batch`note`state!(
    `flink_vwap1;
    enlist `flink_vwap_raw;
    enlist `flink_vwap;
    .qpipe.job.flink_vwap.on_batch;
    "publishes each per-symbol VWAP window an Apache Flink job closes, once, dropping windows Flink emits again after a restart or replay. The raw rows come from an EXTERNAL Python process (external/flink_vwap_feed.py) - Flink runs outside q - so flink_vwap_raw has no producer in this list and this does not start with the stack. Start it with `uqs feed start flink_vwap`";
    enlist `high_water)];
