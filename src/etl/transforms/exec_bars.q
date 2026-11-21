/ exec_bars.q - the time-bar aggregation of fills, shared by the live job and its backfill twin (#946).
/ .
/ exec_bars (streaming, a bars job) and hdb_exec_bars_backfill (bounded) both
/ write exec_bar under one key, (sym;bar_start), and a backfill REPLACES the
/ live job's rows. So they aggregate with this one function, and cut windows
/ with the one `width`, here.

\d .

/ The bar width. The live job declares it and the twin cuts by it; a backfill
/ window should start on a multiple of it, or its first and last bars are partial.
.qpipe.transform.exec_bars.width:0D00:01:00

/ How long after a window's end a fill may arrive and still amend its bar.
.qpipe.transform.exec_bars.lateness:0D00:00:05

/ One bar per sym per window: open and close are the first and last fill by
/ source_time (not arrival order), vwap is size-weighted, volume the summed size.
/ A window with no fills has no row.
/ .
/ A fill with a null price, or a null, zero or negative size, is not a trade a
/ bar can be built from (#2880): a negative size weighted the vwap outside the
/ bar's range, a null size still set its high, and zero-size fills alone made
/ a zero-volume bar the twin's check refuses. fx_positions and posbook drop
/ the null and negative ones too (#2565); a zero-size fill moves no position.
/ Such a fill is dropped here, in the one aggregation the live job and its
/ twin share, and said out loud.
/ @param x fills carrying source_time, sym, size, price and bar_start
/ @return the bars, in exec_bar's columns without time
/ @eg count .qpipe.transform.exec_bars.bars ([] source_time:2#2026.10.09D10:00:05; sym:2#`EURUSD; size:1 2f; price:1.1 1.2; bar_start:2#2026.10.09D10:00:00)  ->  1
/ @eg count .qpipe.transform.exec_bars.bars ([] source_time:2#2026.10.09D10:00:05; sym:2#`EURUSD; size:0 -1f; price:1.1 1.2; bar_start:2#2026.10.09D10:00:00)  ->  0
.qpipe.transform.exec_bars.bars:{[x]
    shape:.qetl.plant.published `exec_bar;
    bad:(null x`price) or not 0<x`size;
    if[any bad;
        .qetl.log.ch.warn[`transform;"dropped fills with a null price or a size that is not positive - they cannot be barred";
            (enlist[`transform]!enlist `exec_bars),enlist[`fills]!enlist `long$sum bad];
        x:x where not bad];
    if[0=count x; :shape];
    x:`source_time xasc x;
    b:0!select open:first price, high:max price, low:min price, close:last price,
        volume:sum size, vwap:size wavg price, trades:count i by sym, bar_start from x;
    (cols shape) xcols b}

\d .
