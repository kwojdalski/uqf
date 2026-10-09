/ mock_mids.q - the mid and spread of each mock quote (.qpipe.job.mock_mids).
/ .
/ Reads `mock_ticks`; publishes `mock_mids`, one row per quote.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qpipe.job.mock_mids

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `mock_mids;

/ Each quote's mid and spread.
/ @param quotes mock_ticks rows
/ @return a table of mock_mids' columns, without `time`
/ @eg .qpipe.job.mock_mids.mids .qpipe.job.mock_ticks.tick 2026.09.01D09:00:00.000000000
mids:{[quotes] select source_time, sym, mid:0.5*bid+ask, spread:ask-bid from quotes}

/ @param t the table a batch is for
/ @param x its rows
on_batch:{[t;x]
    if[not t~`mock_ticks; :()];
    if[0=count x; :()];
    publish[`mock_mids;mids[x]];
    }

\d .

.qetl.job.stream.define[`mock_mids;`procname`subscribe_to`publishes`on_batch`note!(
    `mock_mids1;
    enlist `mock_ticks;
    enlist `mock_mids;
    .qpipe.job.mock_mids.on_batch;
    "The mid and spread of the mockups bundle's synthetic quotes - a streaming job that reads another bundle job. Starts only when named")];
