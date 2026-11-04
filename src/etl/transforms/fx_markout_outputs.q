/ fx_markout_outputs.q - long and wide outputs of one markout calculation
/ (.qpipe.transform.fx_markout_outputs, #1395).
/ .
/ The calculation (fx_markout.q) produces ONE result: a row per (execution,
/ horizon, component). What a consumer wants is a layout of it, and there are two
/ the desks read:
/ .
/   long   a row per component, the result as it stands, narrowed to the columns
/          the plant table carries. Filter by kind to get a markout, an execution
/          residual or one leg.
/   wide   a row per (execution, horizon): the markout, the execution residual
/          and the reconciling residual side by side, and the legs one after
/          another in route order (leg1 .. leg3), each with its pips.
/ .
/ Both are declared transforms of the RESULT alone, with their own examples, so a
/ layout is verified apart from the arithmetic that fed it and neither can drift
/ from the other: the wide table is built from the long one by identity, never
/ recomputed. The jobs' scoring transforms (fx_markout_score_long / _wide) are
/ these applied to the calculation, which is how a live job and a bounded twin of
/ the same layout produce the same rows.
/ .
/ A route longer than three legs does not fit the wide layout and is refused, not
/ truncated: dropping a leg would break the reconciliation the layout exists to show.
/ .
/ The wide table is DERIVED from the long rows by key (markout_layouts.q, #1932),
/ under the layout spec below, then named as the desks have always read it
/ (total_pips, leg1, leg1_pips) and given a status that rolls the components up.

\d .

/ The long output: the plant table, without `time`.
.qpipe.transform.fx_markout_outputs.long_out:.qetl.plant.published `fx_markout

/ The wide output: the plant table, without `time`.
.qpipe.transform.fx_markout_outputs.wide_out:.qetl.plant.published `fx_markout_wide

/ How many legs the wide layout has columns for.
.qpipe.transform.fx_markout_outputs.wide_legs:3

/ The identity of a (execution, horizon): what the long and wide rows are keyed on.
.qpipe.transform.fx_markout_outputs.identity:`venue`fill_id`horizon

/ The FX layout spec markout_layouts reads: which long column belongs to the execution,
/ to the (execution, horizon), or to a component, and the components in wide order.
/ @return the layout spec
/ @eg (.qpipe.transform.fx_markout_outputs.layout_spec[])`base  ->  `total
.qpipe.transform.fx_markout_outputs.layout_spec:{[]
    comps:`execution`leg`leg`leg`residual;
    steps:0 1 2 3 0;
    `columns`execution`horizon`shared`own`base`components`present`per_execution!(
        cols .qpipe.transform.fx_markout.result;`venue`fill_id;`horizon;
        `time`source_time`clock`sym`side`size`price`quote_ccy`ref_t0`ref_t1`pip_factor`pip_value`rate`rate_source`rate_sym`rate_time`config_id`version;
        `leg`invert`price_t0`price_t1`move`pips`quote_amount`report_amount`status;`total;
        ([] kind:comps; step:steps);`status;
        `time`source_time`clock`sym`side`size`price`quote_ccy`pip_factor`pip_value`config_id`version)}

/ The long layout of a calculation result.
/ @param res the calculation result
/ @return the plant table's columns, one row per component
/ @eg count .qpipe.transform.fx_markout_outputs.long[0#.qpipe.transform.fx_markout.result]  ->  0
.qpipe.transform.fx_markout_outputs.long:{[res] (cols .qpipe.transform.fx_markout_outputs.long_out)#res}

/ The wide layout of a calculation result: a row per (execution, horizon).
/ .
/ status is the total's (`ok, or `no_rate when no rate converts the money) when
/ every component is priced and the residual reconciles within the spec's
/ tolerance; the total's own `unavailable or `clock_mismatch when the markout
/ itself is not priced; `partial when it is but a component (the execution
/ residual, a leg) is not; and `unreconciled when everything is priced and the legs
/ do not add up.
/ @param spec the spec, at least tolerance
/ @param res the calculation result
/ @return the plant table's columns, with time, one row per (execution, horizon)
/ @throws error when a route has more legs than the layout has columns
/ @eg count .qpipe.transform.fx_markout_outputs.wide[.qpipe.transform.fx_markout.default_spec;0#.qpipe.transform.fx_markout.result]  ->  0
.qpipe.transform.fx_markout_outputs.wide:{[spec;res]
    if[0=count res; :0#.qetl.plant.shape `fx_markout_wide];
    deep:max 0,exec step from res where kind=`leg;
    if[deep>.qpipe.transform.fx_markout_outputs.wide_legs;
        '"fx_markout_outputs: wide: a route of ",string[deep]," legs does not fit ",string[.qpipe.transform.fx_markout_outputs.wide_legs]," leg columns"];
    w:.qpipe.transform.markout_layouts.wide[.qpipe.transform.fx_markout_outputs.layout_spec[];res];
    w:(`pips`leg1_leg`leg2_leg`leg3_leg!`total_pips`leg1`leg2`leg3) xcol w;
    missing:((not null w`leg1)&null w`leg1_pips)|((not null w`leg2)&null w`leg2_pips)|(not null w`leg3)&null w`leg3_pips;
    unpriced:(w`status) in `unavailable`clock_mismatch;
    reconciled:(not null w`residual_pips) and (spec`tolerance)>=abs w`residual_pips;
    st:?[unpriced;w`status;
        ?[missing | null w`execution_pips;`partial;
        ?[reconciled;w`status;`unreconciled]]];
    w:update status:st from w;
    (cols .qetl.plant.shape `fx_markout_wide)#w}

/ ----------------------------------------------------------- THE JOBS' SPEC

/ The grid and policy the shipped jobs and their bounded twins score with - one
/ definition, so a live row and a refilled one cannot differ. The horizons are
/ signed: one second BEFORE the execution, at it, and one and ten seconds after.
/ Everything else is fx_markout's default spec: money in USD, converted at the
/ execution's instant, with no rate declared for any other currency until a site
/ declares one.
.qpipe.transform.fx_markout_outputs.job_spec:.qpipe.transform.fx_markout.default_spec,enlist[`horizons]!enlist(-0D00:00:01;0D00:00:00;0D00:00:01;0D00:00:10)

/ The snapshot id the job spec's results belong to: the shipped jobs, the bounded twins and the
/ hand-worked examples below all stamp it. config_markout.id takes a validated spec and the job
/ spec is complete, so it is that spec as it stands; tests/q/test_config_markout.q holds the two
/ ways of computing it equal.
.qpipe.transform.fx_markout_outputs.config_id:.qpipe.transform.config_markout.id[.qpipe.transform.fx_domain.domain;.qpipe.transform.fx_markout_outputs.job_spec]

/ Pin the job spec's configuration snapshot: the live jobs' `on_start` and the bounded twins' `pin`
/ call it, so the snapshot is durable before the first calculation (#1999).
/ @return the snapshot id, as a one-item list
/ @eg count .qpipe.transform.fx_markout_outputs.pin[]  ->  1
.qpipe.transform.fx_markout_outputs.pin:{[]
    enlist .qpipe.transform.config_markout.pin[.qpipe.transform.fx_domain.domain;.qpipe.transform.fx_markout_outputs.job_spec;"fx_markout job spec: the live jobs fxmarkout1 and fxmarkoutwide1 and their bounded twins"]}

/ The least a job waits before scoring, however early its horizons are.
.qpipe.transform.fx_markout_outputs.settle:0D00:00:01

/ How long a job waits for an execution's references before giving up on it: the
/ wait for its horizons, and five minutes more.
.qpipe.transform.fx_markout_outputs.expire_after:.qpipe.transform.fx_markout.wait[.qpipe.transform.fx_markout_outputs.job_spec;.qpipe.transform.fx_markout_outputs.settle]+0D00:05

/ How long a job remembers an execution it has scored, to drop a redelivery.
.qpipe.transform.fx_markout_outputs.remember:0D01

/ How much older than its arrival an execution's source_time can be: the bound on
/ the books the horizon kind keeps for a late execution. Declared, not left to the
/ kind's day-long default for an event on source_time, which would hold a day of
/ every pair's books. An execution that arrives later than this after it happened
/ may find its books evicted and expires unscored, with the reason.
.qpipe.transform.fx_markout_outputs.max_lateness:0D00:00:30

/ The calculation and one layout, with `time` as the core stamps it: the execution's
/ time plus the furthest horizon, the earliest a live row could have been stamped (all
/ of an execution's horizons are published together, once the last is due). It is
/ what a bounded twin writes.
/ @param spec the spec
/ @param layout `long or `wide
/ @param exe executions
/ @param md market_data
/ @return the layout's rows, with `time` first
/ @throws error when the layout is neither
/ @eg count .qpipe.transform.fx_markout_outputs.stamped_with[.qpipe.transform.fx_markout_outputs.job_spec;`long;0#.qpipe.transform.fx_markout.executions_in;0#.qpipe.transform.fx_markout.market_data_in]  ->  0
.qpipe.transform.fx_markout_outputs.stamped_with:{[spec;layout;exe;md]
    if[not layout in `long`wide; '"fx_markout_outputs: layout must be long or wide, got ",.Q.s1 layout];
    res:.qpipe.transform.fx_markout.calc[spec;exe;md];
    $[layout=`long; res; .qpipe.transform.fx_markout_outputs.wide[spec;res]]}

/ The calculation and one layout, as the horizon job's transform: the rows without `time`,
/ which the job stamps when it publishes.
/ @param spec the spec
/ @param layout `long or `wide
/ @param exe executions
/ @param md market_data
/ @return the layout's rows, without `time`
/ @throws error when the layout is neither
/ @eg count .qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`long;0#.qpipe.transform.fx_markout.executions_in;0#.qpipe.transform.fx_markout.market_data_in]  ->  0
.qpipe.transform.fx_markout_outputs.score_with:{[spec;layout;exe;md]
    rows:.qpipe.transform.fx_markout_outputs.stamped_with[spec;layout;exe;md];
    (cols $[layout=`long; .qpipe.transform.fx_markout_outputs.long_out; .qpipe.transform.fx_markout_outputs.wide_out])#rows}

/ ------------------------------------------------ THE EXAMPLES' INPUTS AND ROWS
/ .
/ Worked by hand; none is read back from the function it checks. Every example
/ reports in USD at the execution's instant (the job spec), so EURUSD converts at
/ rate 1 (source `fixed, the reporting currency itself) and a pair quoted in PLN
/ has no declared rate: its status is `no_rate and report_amount is null.

/ One USDPLN sell, as the calculation leaves it, at the one horizon of 1s.
/ @private
.qpipe.transform.fx_markout_outputs.sample_result:{[]
    ([] time:5#2026.09.17D10:00:01; source_time:5#2026.09.17D10:00:00; clock:5#`venue; venue:5#`a; fill_id:5#`y1; sym:5#`USDPLN; side:5#-1; size:5#1e6;
        horizon:5#0D00:00:01; kind:`total`execution`leg`leg`residual; step:0 0 1 2 0;
        leg:``,`EURUSD`EURPLN,`; invert:0b,0b,1b,0b,0b; price:5#3.99;
        price_t0:3.99 3.99 1.25 5 0n; price_t1:5.5 4 1 5.5 0n;
        quote_ccy:5#`PLN; ref_t0:5#4.0; ref_t1:5#5.5;
        move:-1.51 -0.01 -1 -0.5 0f; pips:-15100 -100 -10000 -5000 0f; pip_factor:(5#.qf.px.pip_factor `USDPLN); pip_value:5#100f;
        quote_amount:-1510000 -10000 -1000000 -500000 0f; rate:5#0n; rate_source:5#`; rate_sym:5#`;
        rate_time:5#0Np; report_amount:5#0n; status:5#`no_rate; config_id:5#.qpipe.transform.fx_markout_outputs.config_id; version:5#`proposed_v1)}

/ A EURUSD buy at 1.1001, as the executions normalizer publishes it.
/ @private
.qpipe.transform.fx_markout_outputs.sample_executions:{[]
    ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; sym:enlist `EURUSD; side:enlist 1;
        size:enlist 1e6; price:enlist 1.1001; fill_id:enlist `x1; clock:enlist `venue)}

/ Two sources' books around it.
/ @private
.qpipe.transform.fx_markout_outputs.sample_books:{[]
    ([] source_time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00.5 2026.09.17D10:00:08;
        sym:5#`EURUSD; source:`a`a`a`b`b;
        bid_prices:(enlist 1.0999;enlist 1.1004;enlist 1.1009;enlist 1.1003;enlist 1.1008);
        bid_sizes:5#enlist enlist 1e6;
        ask_prices:(enlist 1.1001;enlist 1.1006;enlist 1.1011;enlist 1.1007;enlist 1.1010);
        ask_sizes:5#enlist enlist 1e6; clock:5#`venue)}

/ Its long rows under the job grid: the markout, the execution residual, the one
/ leg and the residual at -1s, 0s, +1s and +10s. 1e6 EURUSD is 100 USD a pip, and
/ the rate is 1 (USD is the reporting currency), so the money is 100 times the pips.
/ @private
.qpipe.transform.fx_markout_outputs.sample_long:{[]
    ([] source_time:16#2026.09.17D10:00:00; clock:16#`venue; venue:16#`a; fill_id:16#`x1; sym:16#`EURUSD; side:16#1; size:16#1e6;
        horizon:(4#-0D00:00:01),(4#0D00:00:00),(4#0D00:00:01),4#0D00:00:10;
        kind:16#`total`execution`leg`residual; step:16#0 0 1 0; leg:16#``,`EURUSD,`; invert:16#0b; price:16#1.1001;
        price_t0:16#1.1001 1.1001 1.1 0n;
        price_t1:1.1 1.1 1.1 0n,1.1 1.1 1.1 0n,1.1005 1.1 1.1005 0n,1.10095 1.1 1.10095 0n;
        quote_ccy:16#`USD; ref_t0:16#1.1; ref_t1:(8#1.1),(4#1.1005),4#1.10095;
        move:-1e-4 -1e-4 0 0f,-1e-4 -1e-4 0 0f,4e-4 -1e-4 5e-4 0f,8.5e-4 -1e-4 9.5e-4 0f;
        pips:-1 -1 0 0f,-1 -1 0 0f,4 -1 5 0f,8.5 -1 9.5 0f; pip_factor:(16#.qf.px.pip_factor `EURUSD); pip_value:16#100f;
        quote_amount:-100 -100 0 0f,-100 -100 0 0f,400 -100 500 0f,850 -100 950 0f;
        rate:16#1f; rate_source:16#`fixed; rate_sym:16#`; rate_time:16#0Np;
        report_amount:-100 -100 0 0f,-100 -100 0 0f,400 -100 500 0f,850 -100 950 0f;
        status:16#`ok`ok`ok`reconciled; config_id:16#.qpipe.transform.fx_markout_outputs.config_id; version:16#`proposed_v1)}

/ Its wide rows under the job grid.
/ @private
.qpipe.transform.fx_markout_outputs.sample_wide:{[]
    ([] source_time:4#2026.09.17D10:00:00; clock:4#`venue; venue:4#`a; fill_id:4#`x1; sym:4#`EURUSD; side:4#1; size:4#1e6;
        horizon:(-0D00:00:01;0D00:00:00;0D00:00:01;0D00:00:10); pip_factor:(4#.qf.px.pip_factor `EURUSD); pip_value:4#100f;
        price:4#1.1001; quote_ccy:4#`USD; ref_t0:4#1.1; ref_t1:1.1 1.1 1.1005 1.10095; move:-1e-4 -1e-4 4e-4 8.5e-4;
        total_pips:-1 -1 4 8.5; execution_pips:4#-1f; residual_pips:4#0f;
        leg1:4#`EURUSD; leg1_invert:4#0b; leg1_pips:0 0 5 9.5;
        leg2:4#`; leg2_invert:4#0b; leg2_pips:4#0n;
        leg3:4#`; leg3_invert:4#0b; leg3_pips:4#0n;
        quote_amount:-100 -100 400 850f; rate:4#1f; rate_source:4#`fixed; rate_sym:4#`; rate_time:4#0Np;
        report_amount:-100 -100 400 850f; status:4#`ok; config_id:4#.qpipe.transform.fx_markout_outputs.config_id; version:4#`proposed_v1)}

\d .

/ ------------------------------------------------------------- THE EXAMPLES
/ .
/ All worked by hand; none is read back from the function it checks.

/ One USDPLN sell, as the calculation leaves it: a sell at 3.99 against a cross of
/ 4.0 that moved to 5.5 through EURUSD (inverted, 1.25 -> 1.0) and EURPLN (5.0 ->
/ 5.5). Execution residual 10000*(4-3.99)*-1 = -100; the legs -10000 and -5000;
/ the markout -15100; nothing left over. PLN has no declared rate: no_rate.
.qetl.transform.define[`fx_markout_layout_long;`inputs`output`fn`examples!(
    (enlist `fx_markout)!enlist .qpipe.transform.fx_markout.result;
    .qpipe.transform.fx_markout_outputs.long_out;
    .qpipe.transform.fx_markout_outputs.long;
    enlist `inputs`expected!(
        (enlist `fx_markout)!enlist .qpipe.transform.fx_markout_outputs.sample_result[];
        (cols .qpipe.transform.fx_markout_outputs.long_out)#.qpipe.transform.fx_markout_outputs.sample_result[]))];

.qetl.transform.define[`fx_markout_layout_wide;`inputs`output`fn`examples!(
    (enlist `fx_markout)!enlist .qpipe.transform.fx_markout.result;
    .qpipe.transform.fx_markout_outputs.wide_out;
    {[res] (cols .qpipe.transform.fx_markout_outputs.wide_out)#.qpipe.transform.fx_markout_outputs.wide[.qpipe.transform.fx_markout_outputs.job_spec;res]};
    enlist `inputs`expected!(
        (enlist `fx_markout)!enlist .qpipe.transform.fx_markout_outputs.sample_result[];
        ([] source_time:enlist 2026.09.17D10:00:00; clock:enlist `venue; venue:enlist `a; fill_id:enlist `y1; sym:enlist `USDPLN;
            side:enlist -1; size:enlist 1e6; horizon:enlist 0D00:00:01; pip_factor:enlist 10000; pip_value:enlist 100f;
            price:enlist 3.99; quote_ccy:enlist `PLN; ref_t0:enlist 4.0; ref_t1:enlist 5.5; move:enlist -1.51;
            total_pips:enlist -15100f; execution_pips:enlist -100f; residual_pips:enlist 0f;
            leg1:enlist `EURUSD; leg1_invert:enlist 1b; leg1_pips:enlist -10000f;
            leg2:enlist `EURPLN; leg2_invert:enlist 0b; leg2_pips:enlist -5000f;
            leg3:enlist `; leg3_invert:enlist 0b; leg3_pips:enlist 0n;
            quote_amount:enlist -1510000f; rate:enlist 0n; rate_source:enlist `; rate_sym:enlist `;
            rate_time:enlist 0Np; report_amount:enlist 0n; status:enlist `no_rate;
            config_id:enlist .qpipe.transform.fx_markout_outputs.config_id; version:enlist `proposed_v1)))];

/ A EURUSD buy at 1.1001 against two sources, the job grid. At the execution a's
/ 1.0999/1.1001 is the only book (mid 1.1000); at -1s it is the same book. +1s: the best
/ bid and ask across a and b are 1.1004/1.1006 (mid 1.1005); +10s: 1.1009/1.1010
/ (mid 1.10095). Execution residual -1 throughout; the leg's allocation 0, 0, 5, 9.5;
/ the markout -1, -1, 4, 8.5.
/ The live scoring, per layout: the horizon job's transform, and the twin's.
.qetl.transform.define[`fx_markout_score_long;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.fx_markout.executions_in;.qpipe.transform.fx_markout.market_data_in);
    .qpipe.transform.fx_markout_outputs.long_out;
    .qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`long;;];
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.fx_markout_outputs.sample_executions[];.qpipe.transform.fx_markout_outputs.sample_books[]);
        (cols .qpipe.transform.fx_markout_outputs.long_out)#.qpipe.transform.fx_markout_outputs.sample_long[]))];

.qetl.transform.define[`fx_markout_score_wide;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.fx_markout.executions_in;.qpipe.transform.fx_markout.market_data_in);
    .qpipe.transform.fx_markout_outputs.wide_out;
    .qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`wide;;];
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.fx_markout_outputs.sample_executions[];.qpipe.transform.fx_markout_outputs.sample_books[]);
        (cols .qpipe.transform.fx_markout_outputs.wide_out)#.qpipe.transform.fx_markout_outputs.sample_wide[]))];
