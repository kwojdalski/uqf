/ test_hdb_transfer_backfill.q - the HDB-to-HDB example job: source
/ hdb_transfer read from a kdb+ HDB's files, worker hdb_transfer_backfill
/ (.hdb_transferbftest). The whole run, source HDB to destination HDB in a fresh
/ process, is scripts/examples/hdb_transfer_example.q, in the q-scripts lane.

\d .hdb_transferbftest

root:`:build/test_hdb_transfer_source

/ A two-day source HDB: trades on 2026.01.05 and 2026.01.06, one of them at
/ midnight exactly, so the half-open window is tested at its edge.
setUp_hdb:{[]
    system"rm -rf build/test_hdb_transfer_source";
    t:([] time:2026.01.05D09:00 2026.01.05D23:59:59 2026.01.06D00:00 2026.01.06D12:00;
        trade_id:1 2 3 4; sym:`EURUSD`USDJPY`EURUSD`GBPUSD;
        price:1.08 151.2 1.09 1.27; size:1000 2000 3000 4000; side:`buy`sell`buy`sell);
    {[t;d] (` sv .hdb_transferbftest.root,(`$string d),`trades,`) set .Q.en[.hdb_transferbftest.root;select from t where d=`date$time]}[t] each 2026.01.05 2026.01.06;
    }

test_the_query_reads_one_window_half_open_with_symbols_decoded:{[t]
    r:.qpipe.source.hdb_transfer.query[.hdb_transferbftest.root;2026.01.05D00:00;2026.01.06D00:00];
    .qunit.assertEquals[(r`trade_id;r`sym);(1 2;`EURUSD`USDJPY);
        "the first day only - midnight belongs to the next window - and plain symbols, not enumerations"]};

test_the_query_returns_the_declared_columns:{[t]
    r:.qpipe.source.hdb_transfer.query[.hdb_transferbftest.root;2026.01.05D00:00;2026.01.07D00:00];
    .qunit.assertEquals[(cols r;count r);(.qpipe.source.hdb_transfer.columns;4);"the source contract's shape, every row"]};

test_the_transform_adds_notional_in_the_tables_order:{[t]
    out:.qpipe.job.hdb_transfer_backfill.with_notional .qpipe.source.hdb_transfer.fixture[];
    .qunit.assertEquals[cols out;cols .qetl.plant.shape `trades_copy;"trades_copy's column order"];
    .qunit.assertEquals[out`notional;(out`price)*out`size;"notional is price times size"]};

test_the_facts_say_more_than_a_row_count:{[t]
    f:.qpipe.job.hdb_transfer_backfill.facts .qpipe.job.hdb_transfer_backfill.with_notional .qpipe.source.hdb_transfer.fixture[];
    .qunit.assertEquals[f`rows`syms;4 3;"rows and distinct pairs"];
    .qunit.assertEquals[.qpipe.job.hdb_transfer_backfill.facts 0#.qetl.plant.shape `trades_copy;(enlist `window)!enlist "empty window";
        "an empty window is legal, and said so"]};

\d .
