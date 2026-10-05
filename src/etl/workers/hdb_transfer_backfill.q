/ hdb_transfer_backfill.q - copy trades from one kdb+ HDB into another, adding
/ each trade's notional (.qpipe.job.hdb_transfer_backfill).
/ .
/ The worker half of the HDB-to-HDB example (source: hdb_transfer.q). Mostly a
/ declaration: windowing, retries, coverage, checkpoints and dry-run are
/ .qetl.job.bounded's. WHERE the rows go is the runner's choice, not this
/ file's - under TorQ, torq_backfill.q writes into the stack's HDB; the
/ example script points .qetl.io.default at a second HDB directory.
/ .
/ Scaffolded with:
/   uqs job new hdb_transfer --kind backfill --transport local --dataset trades_copy \
/       --columns 'trade_id:long,sym:symbol,price:float,size:long,side:symbol,notional:float'

\d .qpipe.job.hdb_transfer_backfill

/ What this run SAW, beyond its row count: how many pairs, and the notional
/ moved - a partial extract changes both. Survives an empty batch.
facts:{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    `rows`syms`notional!(count batch;count distinct batch`sym;sum batch`notional)}

/ The transform: the source's trades, plus notional = price * size, in
/ trades_copy's column order.
with_notional:{[batch] cols[.qetl.plant.shape `trades_copy] xcols update notional:price*size from batch}

\d .

.qetl.transform.define[`hdb_transfer_with_notional;`inputs`output`fn`examples!(
    enlist[`batch]!enlist 0#.qpipe.source.hdb_transfer.fixture[];
    .qetl.plant.shape `trades_copy;
    .qpipe.job.hdb_transfer_backfill.with_notional;
    enlist `inputs`expected!(
        enlist[`batch]!enlist ([] time:enlist 2026.01.05D09:00; trade_id:enlist 1; sym:enlist `EURUSD;
            price:enlist 1.25; size:enlist 1000000; side:enlist `buy);
        ([] time:enlist 2026.01.05D09:00; trade_id:enlist 1; sym:enlist `EURUSD;
            price:enlist 1.25; size:enlist 1000000; side:enlist `buy; notional:enlist 1250000f)))];

/ `procname` is the process that runs this worker - the process registry is
/ read from this declaration, so there is no entry to add anywhere else.
.qetl.job.bounded.define[`hdb_transfer_backfill;
    `source`dataset`width`transform`facts`procname`note!
        (`hdb_transfer;`trades_copy;1D;`hdb_transfer_with_notional;.qpipe.job.hdb_transfer_backfill.facts;
         `hdb_transfer_backfill1;
         "bounded - copies trades from a kdb+ HDB on this machine into trades_copy, adding notional; the HDB-to-HDB example")];
