/ test_markout_layouts.q - long is stored, wide and nested-wide are derived (#1932).
/ .
/ src/etl/transforms/markout_layouts.q knows no domain; FX is only the worked
/ example here, because today's wide table (fx_markout_outputs.q) is the layout the
/ derivation has to reproduce.

/ The FX layout spec: a domain hands this over, the layouts never assume it.
.qt.qpipe.transform.markout_layouts.fx_spec:{[]
    `columns`execution`horizon`shared`own`base`components`present`per_execution!(
        cols .qpipe.transform.fx_markout.result;`venue`fill_id;`horizon;
        `source_time`sym`side`size`pip_factor`version;`leg`invert`price_t0`price_t1`pips`status;`total;
        ([] kind:`execution`leg`leg`leg`residual; step:0 1 2 3 0);`status;
        `source_time`sym`side`size`pip_factor`version)}

/ Today's wide status: what fx_markout_outputs.wide says of the components.
.qt.qpipe.transform.markout_layouts.fx_status:{[w]
    missing:((not null w`leg1_leg)&null w`leg1_pips)|((not null w`leg2_leg)&null w`leg2_pips)|(not null w`leg3_leg)&null w`leg3_pips;
    ?[null w`pips;`unavailable;?[missing|null w`execution_pips;`partial;?[(w`residual_status)=`reconciled;`ok;`unreconciled]]]}

/ Today's wide table, read from the derived wide one: rename, roll the status up, project.
.qt.qpipe.transform.markout_layouts.as_fx_wide:{[w]
    st:.qt.qpipe.transform.markout_layouts.fx_status[w];
    r:update status:st from w;
    r:(`price_t0`price_t1`pips`execution_price_t1`leg1_leg`leg2_leg`leg3_leg!`price`ref_t1`total_pips`ref_t0`leg1`leg2`leg3) xcol r;
    (cols .qpipe.transform.fx_markout_outputs.wide_out)#r}

/ Two executions, three horizons, one with a missing leg and one unpriced horizon.
.qt.qpipe.transform.markout_layouts.fx_two:{[]
    a:.qpipe.transform.fx_markout_outputs.sample_long[];
    b:.qpipe.transform.fx_markout_outputs.sample_result[];
    / a second horizon for the USDPLN sell, unpriced: no markout, no references
    b2:update horizon:0D00:00:10, pips:0n, price_t0:0n, price_t1:0n, status:`unavailable from b;
    b2:update status:`unavailable from b2 where kind=`residual;
    a,b,b2}

.qt.qpipe.transform.markout_layouts.test_long_to_wide_to_long_gives_the_identical_long_rows:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    lg:.qt.qpipe.transform.markout_layouts.fx_two[];
    back:.qpipe.transform.markout_layouts.long[sp;.qpipe.transform.markout_layouts.wide[sp;lg]];
    .qunit.assertEquals[count back;count lg;"every long row comes back"];
    .qunit.assertEquals[0!`venue`fill_id`horizon`kind`step xasc back;0!`venue`fill_id`horizon`kind`step xasc lg;"and identical, nulls and types included"];
    .qunit.assertTrue[(meta back)~meta lg;"with the same column types"]};

.qt.qpipe.transform.markout_layouts.test_a_long_in_canonical_order_comes_back_in_order:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    lg:.qpipe.transform.fx_markout_outputs.sample_long[];
    .qunit.assertTrue[lg~.qpipe.transform.markout_layouts.long[sp;.qpipe.transform.markout_layouts.wide[sp;lg]];"row for row, the same order"];
    r:.qpipe.transform.markout_layouts.canonical[sp;reverse lg];
    .qunit.assertTrue[(`venue`fill_id`horizon`kind`step xasc r)~`venue`fill_id`horizon`kind`step xasc lg;"a reshuffled long holds the same rows"];
    .qunit.assertEquals[r`horizon;asc r`horizon;"canonically ordered by horizon within the one execution"]};

.qt.qpipe.transform.markout_layouts.test_nested_wide_goes_through_wide_to_the_same_long:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    lg:.qt.qpipe.transform.markout_layouts.fx_two[];
    w:.qpipe.transform.markout_layouts.wide[sp;lg];
    n:.qpipe.transform.markout_layouts.nested[sp;lg];
    .qunit.assertEquals[.qpipe.transform.markout_layouts.unnest n;w;"unnest is the wide table"];
    .qunit.assertEquals[.qpipe.transform.markout_layouts.long[sp;.qpipe.transform.markout_layouts.unnest n];.qpipe.transform.markout_layouts.long[sp;w];"and the same long"]};

.qt.qpipe.transform.markout_layouts.test_nested_vectors_are_aligned_and_in_horizon_order:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    n:.qpipe.transform.markout_layouts.nested[sp;reverse .qt.qpipe.transform.markout_layouts.fx_two[]];
    .qunit.assertEquals[count n;2;"one row per execution"];
    listcols:(cols n) except sp[`execution],sp`per_execution;
    lens:{count each x} each (flip n) listcols;
    .qunit.assertTrue[all (lens 0)~/:lens;"every vector the same length as the horizons"];
    .qunit.assertTrue[all {x~asc x} each n`horizon;"each execution's horizons ascend"];
    x1:first select from n where fill_id=`x1;
    .qunit.assertEquals[x1`horizon;(-0D00:00:01;0D00:00:00;0D00:00:01;0D00:00:10);"x1's grid"];
    .qunit.assertEquals[x1`pips;-1 -1 4 8.5;"total pips, aligned to it"];
    .qunit.assertEquals[x1`leg1_pips;0 0 5 9.5;"the leg, aligned to it"];
    .qunit.assertEquals[x1`status;4#`ok;"statuses, aligned to it"];
    .qunit.assertEquals[(x1`sym;x1`size);(`EURUSD;1e6);"per-execution columns stay atoms"]};

.qt.qpipe.transform.markout_layouts.test_todays_fx_wide_output_is_reproduced:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    f:{[sp;lg] .qt.qpipe.transform.markout_layouts.as_fx_wide .qpipe.transform.markout_layouts.wide[sp;lg]};
    one:.qpipe.transform.fx_markout_outputs.sample_result[];
    sixteen:.qpipe.transform.fx_markout_outputs.sample_long[];
    two:.qt.qpipe.transform.markout_layouts.fx_two[];
    .qunit.assertEquals[f[sp;one];.qpipe.transform.fx_markout_outputs.wide one;"the USDPLN three-leg route, two legs used"];
    .qunit.assertEquals[f[sp;sixteen];.qpipe.transform.fx_markout_outputs.sample_wide[];"the hand-worked EURUSD wide rows"];
    .qunit.assertEquals[f[sp;two];.qpipe.transform.fx_markout_outputs.wide two;"two executions, an unpriced horizon: partial and unavailable"]};

.qt.qpipe.transform.markout_layouts.test_wide_rows_carry_the_total_as_core_and_other_components_prefixed:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    w:.qpipe.transform.markout_layouts.wide[sp;.qpipe.transform.fx_markout_outputs.sample_result[]];
    .qunit.assertEquals[first w`pips;-15100f;"unprefixed measure is the total's"];
    .qunit.assertEquals[(first w`execution_pips;first w`residual_pips;first w`leg2_pips);(-100f;0f;-5000f);"the others are prefixed"];
    .qunit.assertTrue[not `kind in cols w;"no kind in a wide row"];
    .qunit.assertTrue[not `step in cols w;"and no step"]};

.qt.qpipe.transform.markout_layouts.test_the_layouts_assume_no_domain:{[tc]
    sp:.qpipe.transform.markout_layouts.sample_spec[];
    lg:.qpipe.transform.markout_layouts.sample_long[];
    w:.qpipe.transform.markout_layouts.wide[sp;lg];
    .qunit.assertEquals[cols w;`venue`fill_id`horizon`sym`move`status`part1_move`part1_status;"a domain's own components"];
    .qunit.assertEquals[w`part1_move;1 2f;"derived by key"];
    .qunit.assertEquals[.qpipe.transform.markout_layouts.long[sp;w];lg;"and back"]};

.qt.qpipe.transform.markout_layouts.test_wide_is_derived_by_key_never_recalculated:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    lg:.qpipe.transform.fx_markout_outputs.sample_result[];
    changed:update pips:pips+1 from lg where kind=`leg, step=2;
    w:.qpipe.transform.markout_layouts.wide[sp;changed];
    .qunit.assertEquals[(first w`leg2_pips;first w`pips);(-4999f;-15100f);"a changed leg changes only itself"]};

.qt.qpipe.transform.markout_layouts.test_a_layout_refuses_what_it_cannot_hold:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    lg:.qpipe.transform.fx_markout_outputs.sample_result[];
    .qunit.assertThrows[.qpipe.transform.markout_layouts.wide[sp;];update step:4 from lg where kind=`leg, leg=`EURPLN;"*not declared by the spec*";"a fourth leg is refused, not truncated"];
    .qunit.assertThrows[.qpipe.transform.markout_layouts.wide[sp;];lg,lg;"*duplicate*";"a duplicate key"];
    .qunit.assertThrows[.qpipe.transform.markout_layouts.wide[sp;];update size:2e6 from lg where kind=`leg;"*varies*";"a shared column that differs between components"];
    .qunit.assertThrows[.qpipe.transform.markout_layouts.wide[sp;];delete from lg where kind=`total;"*no total row*";"a markout without its total"];
    .qunit.assertThrows[.qpipe.transform.markout_layouts.wide[sp;];delete version from lg;"*columns*";"missing column"];
    .qunit.assertThrows[.qpipe.transform.markout_layouts.wide[sp,enlist[`own]!enlist `leg`pips`status;];lg;"*not classified*";"a column the spec does not classify"];
    .qunit.assertThrows[.qpipe.transform.markout_layouts.wide[sp,enlist[`shared]!enlist `sym`side;];lg;"*classified*";"or classifies it twice or not at all"]};

.qt.qpipe.transform.markout_layouts.test_a_per_execution_column_that_varies_is_refused:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    lg:.qpipe.transform.fx_markout_outputs.sample_long[];
    bad:update size:2e6 from lg where horizon=0D00:00:10;
    .qunit.assertThrows[.qpipe.transform.markout_layouts.nested[sp;];bad;"*per-execution*";"nesting would otherwise keep one of two values"]};

.qt.qpipe.transform.markout_layouts.test_empty_long_derives_empty_layouts:{[tc]
    sp:.qt.qpipe.transform.markout_layouts.fx_spec[];
    e:0#.qpipe.transform.fx_markout.result;
    .qunit.assertEquals[(count .qpipe.transform.markout_layouts.wide[sp;e];count .qpipe.transform.markout_layouts.nested[sp;e]);0 0;"nothing in, nothing out"]};

.qt.qpipe.transform.markout_layouts.test_a_contract_dataset_round_trips_and_its_wide_rows_carry_the_total:{[tc]
    / the core's own result: the layouts take its columns as they are
    t:2026.09.17D10:00:00;
    inst:([] sym:enlist `USDJPY; quote_ccy:enlist `JPY; legs:enlist enlist `USDJPY; invert:enlist enlist 0b);
    conv:([] quote_ccy:enlist `JPY; report_ccy:enlist `USD; leg:enlist `USDJPY; invert:enlist 1b);
    dom:.qpipe.transform.markout_core.table_domain[inst;conv];
    bk:{[tm;bid;ask] ([] source_time:enlist tm; sym:enlist `USDJPY; source:enlist `a; bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1e6;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6; clock:enlist `venue)};
    md:raze (bk[t-0D00:00:01;150f;150f];bk[t+0D00:00:09;150.05;150.05]);
    ex:([] source_time:enlist t; venue:enlist `a; sym:enlist `USDJPY; side:enlist 1; size:enlist 1e6; price:enlist 150f; fill_id:enlist `f1; clock:enlist `venue);
    res:.qpipe.transform.markout_core.calc[dom;.qpipe.transform.markout_core.fixture_spec;ex;md];
    sp:.qpipe.transform.markout_layouts.core_spec[cols res;([] kind:enlist `execution; step:enlist 0);`symbol$()];
    w:.qpipe.transform.markout_layouts.wide[sp;res];
    .qunit.assertEquals[count w;count distinct res`horizon;"a row per horizon"];
    .qunit.assertEquals[w`move;(res where (res`kind)=`total)`move;"move is the total's"];
    .qunit.assertEquals[w`execution_move;(res where (res`kind)=`execution)`move;"the execution component is prefixed"];
    .qunit.assertEquals[.qpipe.transform.markout_layouts.long[sp;w];res;"and the core's rows come back identical"];
    .qunit.assertEquals[.qpipe.transform.markout_layouts.long[sp;.qpipe.transform.markout_layouts.unnest .qpipe.transform.markout_layouts.nested[sp;res]];res;"through nested-wide too"]};
