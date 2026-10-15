/ test_mock_mids.q - the mock_mids streaming job (.qt.qpipe.job.mock_mids).


.qt.qpipe.job.mock_mids.quotes:{[] ([] time:2#2026.09.01D09:00:00.000000001; source_time:2#2026.09.01D09:00:00.000000000;
    sym:`EURUSD`USDJPY; bid:1.0849 149.49; ask:1.0851 149.51)}

.qt.qpipe.job.mock_mids.sent:()

.qt.qpipe.job.mock_mids.setUp_job:{[]
    `.qt.qpipe.job.mock_mids.sent set ();
    .qetl.job.stream.wire[`mock_mids;{[t;x] .qt.qpipe.job.mock_mids.sent,:enlist (t;x); count x}];
    }

.qt.qpipe.job.mock_mids.test_each_quote_becomes_its_mid_and_spread:{[t]
    .qpipe.job.mock_mids.on_batch[`mock_ticks;.qt.qpipe.job.mock_mids.quotes[]];
    m:last first .qt.qpipe.job.mock_mids.sent;
    .qunit.assertEquals[first first .qt.qpipe.job.mock_mids.sent;`mock_mids;"published onto mock_mids"];
    .qunit.assertEquals[m`sym;`EURUSD`USDJPY;"one row per quote"];
    .qunit.assertEquals[m`mid;1.085 149.5;"halfway between bid and ask"];
    .qunit.assertTrue[all 1e-9>abs (m`spread)-0.0002 0.02;"ask less bid"];
    .qunit.assertTrue[not `time in cols m;"the plant stamps `time`"]};

.qt.qpipe.job.mock_mids.test_another_table_and_an_empty_batch_publish_nothing:{[t]
    .qpipe.job.mock_mids.on_batch[`quote;.qt.qpipe.job.mock_mids.quotes[]];
    .qpipe.job.mock_mids.on_batch[`mock_ticks;0#.qt.qpipe.job.mock_mids.quotes[]];
    .qunit.assertEquals[.qt.qpipe.job.mock_mids.sent;();"nothing to say, nothing sent"]};

/ What test_job_output_contracts.q drives mock_mids with, so every table it
/ publishes is held to its plant table by name, order and type.
.qt.qpipe.job.mock_mids.contract_driver:{[] .qpipe.job.mock_mids.on_batch[`mock_ticks;.qt.qpipe.job.mock_mids.quotes[]]}
