/ demo_markouts.q - the demo markout scoring, shared by the live job and its backfill twin (#884).
/ .
/ demo_markout (streaming) and hdb_demo_markouts_backfill (bounded) both
/ write demo_execution_quality under one key, (sym;trade_time;horizon), and
/ a backfill REPLACES the live job's rows. So they must compute the same
/ rows, and until this file they did not share the computation: each
/ carried its own mid, its own column selection and its own filter, and
/ the backfill reached into the live job's namespace for the horizons. Any
/ change to the live scoring would have made a refill silently overwrite
/ good rows with differently computed ones.
/ .
/ What the two jobs still adapt, each in its own file, and nothing else:
/   - input names: the live job buffers `quotes`, the source reads `quote`;
/   - `time`: the live job publishes WITHOUT it, and the plant stamps the
/     receipt time (invariant 1); a backfill has no receipt, so it writes
/     trade_time+horizon, the earliest the live row could have been stamped.
/ .
/ The worked example belongs to the transform, as eq_orderbook.q's does.

\d .qpipe.transform.demo_markouts

/ The horizons each fill is scored at, and the furthest of them, which the
/ live job's timer waits out before scoring a fill.
horizons:0D00:00:01 0D00:00:10
max_horizon:max horizons

/ Score fills at every horizon against the mid of the latest quote at or
/ before trade_time+horizon - only the demo's own pairs (.qsynth.pairs), in
/ fill order then horizon order.
/ .
/ A fill whose horizon quote never arrived gets a null ref_price and
/ markout_pips rather than being dropped, so a gap shows in the table.
/ The empty guard is not tidiness: .qexec.markout_at_horizons throws `type`
/ on zero trades.
/ @param fill_rows time sym side trade_price pip_factor, at least
/ @param quotes time sym bid ask
/ @return time (trade_time+horizon) sym trade_time horizon trade_price ref_price markout_pips
/ @eg count .qpipe.transform.demo_markouts.score[([] time:enlist 2026.09.17D10:00:00; sym:`EURUSD; side:1; trade_price:1.1; pip_factor:10000);([] time:enlist 2026.09.17D10:00:00.5; sym:`EURUSD; bid:1.1004; ask:1.1006)] -> 2
score:{[fill_rows;quotes]
    shape:.qetl.plant.shape `demo_execution_quality;
    fill_rows:fill_rows where fill_rows[`sym] in .qsynth.pairs;
    if[0=count fill_rows; :0#shape];
    mids:select sym, time, mid:0.5*bid+ask from quotes;
    out:.qexec.markout_at_horizons[fill_rows;mids;.qpipe.transform.demo_markouts.horizons];
    cols[shape] xcols out}

\d .
