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

\d .qpipe.transform.fx_markout_outputs

/ The long output: the plant table, without `time`.
long_out:.qetl.plant.published `fx_markout

/ The wide output: the plant table, without `time`.
wide_out:.qetl.plant.published `fx_markout_wide

/ How many legs the wide layout has columns for.
wide_legs:3

/ The identity of a (execution, horizon): what the long and wide rows are keyed on.
identity:`venue`fill_id`horizon

/ The long layout of a calculation result.
/ @param res the calculation result
/ @return the plant table's columns, one row per component
/ @eg count .qpipe.transform.fx_markout_outputs.long[0#.qpipe.transform.fx_markout.result]  ->  0
long:{[res] (cols .qpipe.transform.fx_markout_outputs.long_out)#res}

/ One component kind of a result, keyed on the identity, its value columns renamed.
/ @private
by_kind:{[res;knd;want;names]
    id:.qpipe.transform.fx_markout_outputs.identity;
    t:?[res;enlist (=;`kind;enlist knd);0b;(id,want)!id,want];
    id xkey (id,names) xcol t}

/ One route step of a result, keyed on the identity.
/ @private
by_step:{[res;n]
    id:.qpipe.transform.fx_markout_outputs.identity;
    t:select venue, fill_id, horizon, leg, invert, pips from res where kind=`leg, step=n;
    sfx:string n;
    id xkey (id,`$("leg",sfx;"leg",sfx,"_invert";"leg",sfx,"_pips")) xcol t}

/ The wide layout of a calculation result: a row per (execution, horizon).
/ .
/ status is `ok when every component is priced and the residual reconciles,
/ `unavailable when the markout itself is not priced, `partial when it is but a
/ component (the execution residual, a leg) is not, and `unreconciled when
/ everything is priced and the legs do not add up.
/ @param res the calculation result
/ @return the plant table's columns, one row per (execution, horizon)
/ @throws error when a route has more legs than the layout has columns
/ @eg count .qpipe.transform.fx_markout_outputs.wide[0#.qpipe.transform.fx_markout.result]  ->  0
wide:{[res]
    if[0=count res; :0#.qpipe.transform.fx_markout_outputs.wide_out];
    id:.qpipe.transform.fx_markout_outputs.identity;
    deep:max 0,exec step from res where kind=`leg;
    if[deep>.qpipe.transform.fx_markout_outputs.wide_legs;
        '"fx_markout_outputs: wide: a route of ",string[deep]," legs does not fit ",string[.qpipe.transform.fx_markout_outputs.wide_legs]," leg columns"];
    totals:select source_time, venue, fill_id, sym, side, size, horizon, price:price_t0, ref_t1:price_t1,
        pip_factor, total_pips:pips, total_status:status, version from res where kind=`total;
    ex:.qpipe.transform.fx_markout_outputs.by_kind[res;`execution;`price_t1`pips`status;`ref_t0`execution_pips`execution_status];
    rs:.qpipe.transform.fx_markout_outputs.by_kind[res;`residual;`pips`status;`residual_pips`residual_status];
    j:(id xkey totals) lj ex lj rs;
    k:1;
    while[k<=.qpipe.transform.fx_markout_outputs.wide_legs;
        j:j lj .qpipe.transform.fx_markout_outputs.by_step[res;k];
        k+:1];
    j:0!j;
    leg_missing:(not null j`leg1) & null j`leg1_pips;
    leg_missing:leg_missing | (not null j`leg2) & null j`leg2_pips;
    leg_missing:leg_missing | (not null j`leg3) & null j`leg3_pips;
    priced:not null j`total_pips;
    status:?[not priced;`unavailable;
        ?[leg_missing | null j`execution_pips;`partial;
        ?[(j`residual_status)=`reconciled;`ok;`unreconciled]]];
    j:update status:status from j;
    (cols .qpipe.transform.fx_markout_outputs.wide_out)#j}

/ ----------------------------------------------------------- THE JOBS' SPEC

/ The grid and policy the shipped jobs and their bounded twins score with - one
/ definition, so a live row and a refilled one cannot differ. The horizons are
/ signed: one second BEFORE the execution, at it, and one and ten seconds after.
/ Everything else is fx_markout's default spec.
job_spec:.qpipe.transform.fx_markout.default_spec,enlist[`horizons]!enlist(-0D00:00:01;0D00:00:00;0D00:00:01;0D00:00:10)

/ The least a job waits before scoring, however early its horizons are.
settle:0D00:00:01

/ How long a job waits for an execution's references before giving up on it: the
/ wait for its horizons, and five minutes more.
expire_after:.qpipe.transform.fx_markout.wait[job_spec;settle]+0D00:05

/ How long a job remembers an execution it has scored, to drop a redelivery.
remember:0D01

/ How much older than its arrival an execution's source_time can be: the bound on
/ the books the horizon kind keeps for a late execution. Declared, not left to the
/ kind's day-long default for an event on source_time, which would hold a day of
/ every pair's books. An execution that arrives later than this after it happened
/ may find its books evicted and expires unscored, with the reason.
max_lateness:0D00:00:30

/ The calculation and one layout, as the horizon job's and the bounded twin's transform.
/ @param spec the spec
/ @param layout `long or `wide
/ @param exe executions
/ @param md market_data
/ @return the layout's rows, without `time`
/ @throws error when the layout is neither
/ @eg count .qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`long;0#.qpipe.transform.fx_markout.executions_in;0#.qpipe.transform.fx_markout.market_data_in]  ->  0
score_with:{[spec;layout;exe;md]
    if[not layout in `long`wide; '"fx_markout_outputs: layout must be long or wide, got ",.Q.s1 layout];
    res:.qpipe.transform.fx_markout.calc[spec;exe;md];
    $[layout=`long; .qpipe.transform.fx_markout_outputs.long[res]; .qpipe.transform.fx_markout_outputs.wide[res]]}

/ A layout's rows with the time a backfill gives them: the execution's time plus
/ the furthest horizon, the earliest a live row could have been stamped (all of an
/ execution's horizons are published together, once the last is due).
/ @param spec the spec
/ @param rows a layout's rows, with source_time
/ @return the rows with `time` first
/ @eg count .qpipe.transform.fx_markout_outputs.stamped[.qpipe.transform.fx_markout_outputs.job_spec;0#.qpipe.transform.fx_markout_outputs.long_out]  ->  0
stamped:{[spec;rows]
    (`time,cols rows) xcols update time:source_time+max spec`horizons from rows}

/ ------------------------------------------------ THE EXAMPLES' INPUTS AND ROWS
/ .
/ Worked by hand; none is read back from the function it checks. See the
/ declarations below for the arithmetic.

/ One USDPLN sell, as the calculation leaves it.
/ @private
sample_result:{[]
    ([] source_time:5#2026.09.17D10:00:00; venue:5#`a; fill_id:5#`y1; sym:5#`USDPLN; side:5#-1; size:5#1e6;
        horizon:5#0D00:00:01; kind:`total`execution`leg`leg`residual; step:0 0 1 2 0;
        leg:``,`EURUSD`EURPLN,`; invert:0b,0b,1b,0b,0b;
        price_t0:3.99 3.99 1.25 5 0n; price_t1:5.5 4 1 5.5 0n;
        pips:-15100 -100 -10000 -5000 0f; status:`ok`ok`ok`ok`reconciled; pip_factor:(5#.qf.px.pip_factor `USDPLN); version:5#`proposed_v1)}

/ A EURUSD buy at 1.1001, as the executions normalizer publishes it.
/ @private
sample_executions:{[]
    ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; sym:enlist `EURUSD; side:enlist 1;
        size:enlist 1e6; price:enlist 1.1001; fill_id:enlist `x1)}

/ Two sources' books around it.
/ @private
sample_books:{[]
    ([] source_time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00.5 2026.09.17D10:00:08;
        sym:5#`EURUSD; source:`a`a`a`b`b;
        bid_prices:(enlist 1.0999;enlist 1.1004;enlist 1.1009;enlist 1.1003;enlist 1.1008);
        bid_sizes:5#enlist enlist 1e6;
        ask_prices:(enlist 1.1001;enlist 1.1006;enlist 1.1011;enlist 1.1007;enlist 1.1010);
        ask_sizes:5#enlist enlist 1e6)}

/ Its long rows under the job grid.
/ @private
sample_long:{[]
    ([] source_time:16#2026.09.17D10:00:00; venue:16#`a; fill_id:16#`x1; sym:16#`EURUSD; side:16#1; size:16#1e6;
        horizon:(4#-0D00:00:01),(4#0D00:00:00),(4#0D00:00:01),4#0D00:00:10;
        kind:16#`total`execution`leg`residual; step:16#0 0 1 0; leg:16#``,`EURUSD,`; invert:16#0b;
        price_t0:16#1.1001 1.1001 1.1 0n;
        price_t1:1.1 1.1 1.1 0n,1.1 1.1 1.1 0n,1.1005 1.1 1.1005 0n,1.10095 1.1 1.10095 0n;
        pips:-1 -1 0 0f,-1 -1 0 0f,4 -1 5 0f,8.5 -1 9.5 0f; status:16#`ok`ok`ok`reconciled;
        pip_factor:(16#.qf.px.pip_factor `EURUSD); version:16#`proposed_v1)}

/ Its wide rows under the job grid.
/ @private
sample_wide:{[]
    ([] source_time:4#2026.09.17D10:00:00; venue:4#`a; fill_id:4#`x1; sym:4#`EURUSD; side:4#1; size:4#1e6;
        horizon:(-0D00:00:01;0D00:00:00;0D00:00:01;0D00:00:10); price:4#1.1001; pip_factor:(4#.qf.px.pip_factor `EURUSD);
        ref_t0:4#1.1; ref_t1:1.1 1.1 1.1005 1.10095; total_pips:-1 -1 4 8.5; execution_pips:4#-1f; residual_pips:4#0f;
        leg1:4#`EURUSD; leg1_invert:4#0b; leg1_pips:0 0 5 9.5;
        leg2:4#`; leg2_invert:4#0b; leg2_pips:4#0n;
        leg3:4#`; leg3_invert:4#0b; leg3_pips:4#0n;
        status:4#`ok; version:4#`proposed_v1)}

\d .

/ ------------------------------------------------------------- THE EXAMPLES
/ .
/ All worked by hand; none is read back from the function it checks.

/ One USDPLN sell, as the calculation leaves it: a sell at 3.99 against a cross of
/ 4.0 that moved to 5.5 through EURUSD (inverted, 1.25 -> 1.0) and EURPLN (5.0 ->
/ 5.5). Execution residual 10000*(4-3.99)*-1 = -100; the legs -10000 and -5000;
/ the markout -15100; nothing left over.
.qetl.transform.define[`fx_markout_layout_long;`inputs`output`fn`examples!(
    (enlist `result)!enlist .qpipe.transform.fx_markout.result;
    .qpipe.transform.fx_markout_outputs.long_out;
    .qpipe.transform.fx_markout_outputs.long;
    enlist `inputs`expected!(
        (enlist `result)!enlist .qpipe.transform.fx_markout_outputs.sample_result[];
        .qpipe.transform.fx_markout_outputs.sample_result[]))];

.qetl.transform.define[`fx_markout_layout_wide;`inputs`output`fn`examples!(
    (enlist `result)!enlist .qpipe.transform.fx_markout.result;
    .qpipe.transform.fx_markout_outputs.wide_out;
    .qpipe.transform.fx_markout_outputs.wide;
    enlist `inputs`expected!(
        (enlist `result)!enlist .qpipe.transform.fx_markout_outputs.sample_result[];
        ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; fill_id:enlist `y1; sym:enlist `USDPLN;
            side:enlist -1; size:enlist 1e6; horizon:enlist 0D00:00:01; price:enlist 3.99; pip_factor:enlist 10000;
            ref_t0:enlist 4.0; ref_t1:enlist 5.5; total_pips:enlist -15100f; execution_pips:enlist -100f; residual_pips:enlist 0f;
            leg1:enlist `EURUSD; leg1_invert:enlist 1b; leg1_pips:enlist -10000f;
            leg2:enlist `EURPLN; leg2_invert:enlist 0b; leg2_pips:enlist -5000f;
            leg3:enlist `; leg3_invert:enlist 0b; leg3_pips:enlist 0n;
            status:enlist `ok; version:enlist `proposed_v1)))];

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
        .qpipe.transform.fx_markout_outputs.sample_long[]))];

.qetl.transform.define[`fx_markout_score_wide;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.fx_markout.executions_in;.qpipe.transform.fx_markout.market_data_in);
    .qpipe.transform.fx_markout_outputs.wide_out;
    .qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`wide;;];
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.fx_markout_outputs.sample_executions[];.qpipe.transform.fx_markout_outputs.sample_books[]);
        .qpipe.transform.fx_markout_outputs.sample_wide[]))];
