// bench_batching.q - compare unbatched, per-symbol and grouped-symbol markout scoring (#1921).
//
//   q scripts/dev/bench_batching.q [-n 20000] [-quotes 200] [-groups 2,4] [-reps 3]
//
//   -n       executions in the window (default 20000)
//   -quotes  quotes per book per symbol (default 200)
//   -groups  symbols per batch for the grouped runs, comma separated (default 2,4)
//   -reps    timed repetitions per configuration; the fastest is reported (default 3)
//
// WHAT IT MEASURES. The markout worker's TRANSFORM over one synthetic window - what
// batching changes - with the stages timed apart: planning the batches, then scoring them.
// Each run is the real hdb_fx_markouts_backfill declaration with batching switched on in
// memory for the run (`.qdev.bench.use`) and off after. `ms` and `mb` are q's own `\ts`: time,
// and the memory the expression needed at its peak. Every batched output is compared with
// the unbatched one by identity (`same`); a configuration that disagrees says so, and the
// run exits non-zero.
//
// WHAT IT DOES NOT. Reading the HDB: the window is generated, and the books are handed to
// every configuration as they are, whole or narrowed to a batch's legs, so a result here is
// the compute and working-set effect and not a claim about HDB load. That is measured
// against a real HDB, where reads - not these timings - decide whether the batches should
// read less than the window (see #1921).
//
// Numbers from one machine are a comparison between rows of one run, not a figure to quote.
// PeachQ reports no usable space figure for `\ts` (every row shows the same `mb`): read memory
// from KDB-X. Times, and the `same` check, mean the same on both.

\l src/init.q
\l src/etl/init.q

\d .

.qdev.bench.worker:`hdb_fx_markouts_backfill

/ The pairs the synthetic window draws executions from, kept to those the router can price.
.qdev.bench.pool:{[]
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    cand:`EURUSD`GBPUSD`USDJPY`USDCHF`AUDUSD`USDCAD`USDPLN`EURGBP`EURJPY`EURPLN`NZDUSD`USDSEK;
    cand where .qpipe.transform.fx_markout.routable[spec`convention;cand]}

/ A window of `n executions and the books their legs need, `q quotes each.
/ @param n executions
/ @param q quotes per book
/ @return the source's inputs: executions and market_data
.qdev.bench.window:{[n;q]
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    pool:.qdev.bench.pool[];
    t0:2026.09.17D10:00:00;
    span:0D01;
    exe:([] source_time:t0+asc n?span; venue:n#`a; sym:n?pool; side:n?1 -1; size:n#1e6;
        price:1.0+n?0.5; fill_id:`$"f",/:string til n);
    legs:distinct raze .qpipe.transform.fx_markout.legs_of[spec;exe];
    m:count legs;
    mid:1.0+0.5*m?1f;
    s:(m*q)#legs;
    mids:(m*q)#mid;
    books:([] source_time:(t0-0D00:00:01)+asc (m*q)?span+0D00:00:20; sym:s; source:(m*q)#`a;
        bid_prices:enlist each mids-0.0001; bid_sizes:(m*q)#enlist enlist 1e6;
        ask_prices:enlist each mids+0.0001; ask_sizes:(m*q)#enlist enlist 1e6);
    `executions`market_data!(exe;`source_time xasc books)}

/ Switch the markout worker's batching on (a declaration) or off ((::)), as its config stores it.
.qdev.bench.use:{[v]
    d:.qetl.job.bounded.def .qdev.bench.worker;
    d[`batching]:v;
    `.qetl.job.bounded.worker_cfg upsert (.qdev.bench.worker,d cols value .qetl.job.bounded.worker_cfg);
    v}

/ The books a batch's executions need, as the source's own query derives them.
.qdev.bench.legs_context:{[sup;rows]
    pairs:distinct raze .qpipe.transform.fx_markout.legs_of[.qpipe.transform.fx_markout_outputs.job_spec;rows];
    enlist[`market_data]!enlist select from sup`market_data where sym in pairs}

/ A declaration: k execution symbols a batch (1 is a batch per symbol), books narrowed or whole.
.qdev.bench.spec:{[k;narrow]
    `group_by`max_rows`max_groups`context!(enlist `sym;1000000000;k;$[narrow; .qdev.bench.legs_context; (::)])}

/ The fastest of `reps` timed runs of an expression, after one warm-up.
/ @param expr the expression, as text evaluated in the root context
/ @param reps timed runs
/ @return (ms; bytes) of the fastest, from q's \ts
.qdev.bench.time:{[expr;reps]
    system "ts ",expr;
    rs:{system "ts ",x} each reps#enlist expr;
    rs first iasc rs[;0]}

/ The window being benchmarked, where the timed expressions can reach it.
.qdev.bench.inputs:()

/ One configuration over the window: a row of results.
/ @param c a row of name, k (symbols per batch, null for none) and narrow
/ @param base the unbatched output, sorted by identity
/ @param reps timed runs
.qdev.bench.measure:{[c;base;reps]
    w:.qdev.bench.worker;
    batched:not null c`k;
    spec:$[batched;
        .qetl.job.bounded.batching.require[w;`source`batching!((.qetl.job.bounded.def w)`source;.qdev.bench.spec[c`k;c`narrow])];
        (::)];
    .qdev.bench.use[spec];
    pl:$[batched;
        .qdev.bench.time["count .qetl.job.bounded.batching.plan[(.qetl.job.bounded.def `",string[w],")`batching;.qdev.bench.inputs`executions]";reps];
        0 0];
    tm:.qdev.bench.time[".qdev.bench.out:.qetl.job.bounded.transform_window[`",string[w],";.qdev.bench.inputs]";reps];
    k:`venue`fill_id`horizon`kind`step;
    `config`batches`plan_ms`ms`mb`rows`same!(c`name;
        $[batched; count .qetl.job.bounded.batching.plan[spec;.qdev.bench.inputs`executions]; 1];
        pl 0;tm 0;`float$tm[1]%1e6;count .qdev.bench.out;(k xasc .qdev.bench.out)~base)}

/ Compare configurations on one window.
/ @param n executions
/ @param q quotes per book
/ @param groups symbols per batch for the grouped runs
/ @param reps timed runs per configuration
/ @return a table, one row per configuration
.qdev.bench.run:{[n;q;groups;reps]
    `.qdev.bench.inputs set .qdev.bench.window[n;q];
    .qdev.bench.use[(::)];
    base:`venue`fill_id`horizon`kind`step xasc .qetl.job.bounded.transform_window[.qdev.bench.worker;.qdev.bench.inputs];
    cfg:([] name:("unbatched";"per symbol";"per symbol, books narrowed"); k:0N 1 1; narrow:001b);
    cfg,:([] name:{"grouped x",string x} each groups; k:groups; narrow:(count groups)#1b);
    out:.qdev.bench.measure[;base;reps] each cfg;
    .qdev.bench.use[(::)];
    out}

\d .

o:.Q.opt .z.x;
opt:{[o;k;d] $[k in key o; first o k; d]};
n:"J"$opt[o;`n;"20000"];
q:"J"$opt[o;`quotes;"200"];
groups:"J"$"," vs opt[o;`groups;"2,4"];
reps:"J"$opt[o;`reps;"3"];
-1 "bench_batching: ",string[n]," executions, ",string[q]," quotes per book, ",string[reps]," timed runs each";
rows:.qdev.bench.run[n;q;groups;reps];
-1 .Q.s rows;
bad:exec config from rows where not same;
if[count bad; -1 "DIFFERS from the unbatched output: ",", " sv bad; exit 1];
-1 "every batched run matches the unbatched output";
exit 0
