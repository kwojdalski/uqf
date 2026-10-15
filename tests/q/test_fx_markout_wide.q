/ test_fx_markout_wide.q - the wide layout (src/etl/transforms/fx_markout_outputs.q) and
/ the wide live job (.qpipe.job.fx_markout_wide, #1395).
/ .
/ The long/wide alignment on a live run is test_fx_markout.q's; this holds the layout
/ itself: that it is built from the long result by identity, what its status says,
/ and that it refuses a route it has no columns for rather than cutting a leg off.


.qt.qpipe.job.fx_markout_wide.t:2026.09.17D10:00:00.000000000

/ one calculation result, as the calculation leaves it
.qt.qpipe.job.fx_markout_wide.sample:{[] .qpipe.transform.fx_markout_outputs.sample_result[]}

.qt.qpipe.job.fx_markout_wide.near:{[a;b] (all (null a)=null b) and all (null a) or 1e-9>=abs a-b}

/ What the output-contract suite (test_job_output_contracts.q) drives the job with.
.qt.qpipe.job.fx_markout_wide.contract_driver:{[]
    .qt.qpipe.job.fx_markout.feed[`fx_markout_wide;`market_data;.qt.qpipe.job.fx_markout.covered[]];
    .qt.qpipe.job.fx_markout.feed[`fx_markout_wide;`executions;.qt.qpipe.job.fx_markout.buy[]];
    .qt.qpipe.job.fx_markout.tick[`fx_markout_wide;.qt.qpipe.job.fx_markout.t+0D00:00:10]}

.qt.qpipe.job.fx_markout_wide.test_the_wide_job_is_the_long_job_with_the_other_layout:{[tc]
    long_job:.qetl.job.stream.horizons.def `fx_markout;
    w:.qetl.job.stream.horizons.def `fx_markout_wide;
    .qunit.assertEquals[(w`events;w`reference;w`transform;w`publishes);(`executions;`market_data;`fx_markout_score_wide;`fx_markout_wide);"the same inputs, the wide scoring, the wide table"];
    .qunit.assertEquals[(`procname`transform`publishes`note`ephemeral`keep`legs) _ long_job;(`procname`transform`publishes`note`ephemeral`keep`legs) _ w;"and every other declared choice identical"]};

.qt.qpipe.job.fx_markout_wide.test_the_wide_example_is_the_long_example_reshaped:{[tc]
    / the two hand-written worked examples of fx_markout_outputs.q must be the same numbers in two layouts
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.wide .qpipe.transform.fx_markout_outputs.sample_long[];
        .qpipe.transform.fx_markout_outputs.sample_wide[];"the 16 long rows are the 4 wide rows"]};

.qt.qpipe.job.fx_markout_wide.test_the_wide_layout_is_one_row_per_execution_and_horizon:{[tc]
    r:.qpipe.transform.fx_markout_outputs.wide .qt.qpipe.job.fx_markout_wide.sample[];
    .qunit.assertEquals[count r;1;"five components, one row"];
    .qunit.assertEquals[(first r`total_pips;first r`execution_pips;first r`residual_pips;first r`leg1_pips;first r`leg2_pips);(-15100f;-100f;0f;-10000f;-5000f);"the components side by side"];
    .qunit.assertEquals[(first r`leg1;first r`leg1_invert;first r`leg2;first r`leg2_invert);(`EURUSD;1b;`EURPLN;0b);"legs in route order, inversion kept"]};

.qt.qpipe.job.fx_markout_wide.test_the_wide_row_is_built_from_the_long_rows_not_recomputed:{[tc]
    s:.qt.qpipe.job.fx_markout_wide.sample[];
    changed:update pips:pips+1 from s where kind=`leg, step=2;
    r:.qpipe.transform.fx_markout_outputs.wide changed;
    .qunit.assertEquals[r`leg2_pips;enlist -4999f;"changing the long leg changes the wide leg"];
    .qunit.assertEquals[r`total_pips;enlist -15100f;"and nothing else - it reports, it does not re-derive"]};

.qt.qpipe.job.fx_markout_wide.test_the_long_layout_is_the_result_in_the_plant_tables_columns:{[tc]
    s:.qt.qpipe.job.fx_markout_wide.sample[];
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.long s;s;"every column of the result is the plant table's"];
    .qunit.assertEquals[cols .qpipe.transform.fx_markout_outputs.long s;cols .qetl.plant.published `fx_markout;"in its order"]};

.qt.qpipe.job.fx_markout_wide.test_status_says_whether_the_row_can_be_trusted:{[tc]
    s:.qt.qpipe.job.fx_markout_wide.sample[];
    ok:first (.qpipe.transform.fx_markout_outputs.wide s)`status;
    unavailable:first (.qpipe.transform.fx_markout_outputs.wide update pips:0n, status:`unavailable from s)`status;
    partial:first (.qpipe.transform.fx_markout_outputs.wide update pips:0n, status:`unavailable from s where kind=`execution)`status;
    off:first (.qpipe.transform.fx_markout_outputs.wide update status:`unreconciled from s where kind=`residual)`status;
    .qunit.assertEquals[(ok;unavailable;partial;off);`ok`unavailable`partial`unreconciled;"priced and reconciled; not priced; priced but not fully; priced but the legs do not add up"]};

.qt.qpipe.job.fx_markout_wide.test_a_route_longer_than_the_layout_is_refused_not_truncated:{[tc]
    s:.qt.qpipe.job.fx_markout_wide.sample[];
    long_route:s,update step:4, leg:`EURGBP from s where kind=`leg, step=2;
    .qunit.assertThrows[.qpipe.transform.fx_markout_outputs.wide;long_route;
        "*a route of 4 legs does not fit 3 leg columns*";"dropping a leg would break the reconciliation the layout shows"]};

.qt.qpipe.job.fx_markout_wide.test_an_empty_result_gives_an_empty_layout:{[tc]
    e:0#.qpipe.transform.fx_markout.result;
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.wide e;0#.qetl.plant.published `fx_markout_wide;"wide"];
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.long e;0#.qetl.plant.published `fx_markout;"long"]};
