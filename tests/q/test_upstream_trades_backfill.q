/ test_upstream_trades_backfill.q - the upstream_trades_to_local transform's
/ side mapping and the worker's quality check
/ (.qpipe.job.upstream_trades_backfill).
/ .
/ The transform's own example already runs on every build; these hold the
/ rows it does not carry - a side the upstream should never send.

.qt.qpipe.job.upstream_trades_backfill.batch:{[sides]
    n:count sides;
    ([] time:2015.01.07D00:00:01.000000000+0D00:00:01*til n; sym:n#`AAPL; ex:n#"N";
        price:n#28.73; size:n#41i; side:sides)}

/ #2573: every side that was not `buy imported as a sell - a null, `SELL.
.qt.qpipe.job.upstream_trades_backfill.test_an_unknown_side_is_not_imported_as_a_sell:{[t]
    r:.qetl.transform.apply[`upstream_trades_to_local;
        enlist[`batch]!enlist .qt.qpipe.job.upstream_trades_backfill.batch[`buy`sell`side`SELL`]];
    .qunit.assertEquals[r`side;1 -1 -1 0N 0N;"buy, sell and the upstream's side map; SELL and a null do not"];
    qc:.qpipe.job.upstream_trades_backfill.quality_check r;
    .qunit.assertEquals[qc`check;enlist `known_side;"and the check refuses the window"];
    .qunit.assertEquals[first qc`detail;"2 row(s) with a side that is not buy, sell or side";"naming how many"]};

.qt.qpipe.job.upstream_trades_backfill.test_the_fixture_passes_the_quality_check:{[t]
    r:.qetl.transform.apply[`upstream_trades_to_local;enlist[`batch]!enlist .qpipe.source.upstream_trades.fixture[]];
    .qunit.assertEmpty[.qpipe.job.upstream_trades_backfill.quality_check r;"the upstream's own rows still import"]};
