/ test_mock_ticks.q - the mock_ticks feed (.qt.qpipe.job.mock_ticks).


.qt.qpipe.job.mock_ticks.t0:2026.09.01D09:00:00.000000000

.qt.qpipe.job.mock_ticks.sent:()

.qt.qpipe.job.mock_ticks.setUp_feed:{[]
    `.qpipe.job.mock_ticks.mids set .qpipe.job.mock_ticks.start;
    `.qt.qpipe.job.mock_ticks.sent set ();
    .qetl.job.stream.wire[`mock_ticks;{[t;x] .qt.qpipe.job.mock_ticks.sent,:enlist (t;x); count x}];
    }

.qt.qpipe.job.mock_ticks.test_a_tick_quotes_every_pair_around_its_mid:{[t]
    q:.qpipe.job.mock_ticks.tick .qt.qpipe.job.mock_ticks.t0;
    .qunit.assertEquals[q`sym;`EURUSD`GBPUSD`USDJPY;"one quote per pair"];
    .qunit.assertTrue[all q[`ask]>q`bid;"never crossed"];
    .qunit.assertTrue[not `time in cols q;"the plant stamps `time`, so the feed never sends it"]};

.qt.qpipe.job.mock_ticks.test_the_same_instants_give_the_same_quotes:{[t]
    a:.qpipe.job.mock_ticks.tick each .qt.qpipe.job.mock_ticks.t0+0D00:00:01*til 5;
    `.qpipe.job.mock_ticks.mids set .qpipe.job.mock_ticks.start;
    b:.qpipe.job.mock_ticks.tick each .qt.qpipe.job.mock_ticks.t0+0D00:00:01*til 5;
    .qunit.assertEquals[a;b;"a tick's draws come from its timestamp"]};

.qt.qpipe.job.mock_ticks.test_a_mid_moves_at_most_one_basis_point_a_tick:{[t]
    before:.qpipe.job.mock_ticks.mids;
    .qpipe.job.mock_ticks.tick .qt.qpipe.job.mock_ticks.t0;
    .qunit.assertTrue[all 0.0001>=abs -1+.qpipe.job.mock_ticks.mids%before;"a walk, not a jump"]};

.qt.qpipe.job.mock_ticks.test_the_timer_publishes_one_batch_of_mock_ticks:{[t]
    .qpipe.job.mock_ticks.on_timer[];
    .qunit.assertEquals[(count .qt.qpipe.job.mock_ticks.sent;first first .qt.qpipe.job.mock_ticks.sent;count last first .qt.qpipe.job.mock_ticks.sent);(1;`mock_ticks;3);
        "one batch, three quotes, onto mock_ticks"]};
