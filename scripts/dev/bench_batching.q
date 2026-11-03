// bench_batching.q - compare unbatched, per-symbol and grouped-symbol markout scoring (#1921).
//
//   q scripts/dev/bench_batching.q [-execs 20000] [-quotes 200] [-groups 2,4] [-reps 3]
//
//   -execs   executions in the window (default 20000)
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
// THEN THE WHOLE WINDOW, stage by stage (the second table). Each configuration runs the real
// worker end to end - fetch, ready, transform, check, publish - over the same synthetic
// window, into a real HDB on a temporary root, with every stage of `window_stages` timed and
// its peak memory read (`\ts`), and the source and the HDB counted: how many times the window
// was fetched and how many rows that read, how many times the writer was called and how many
// rows it wrote, and how many date partitions the root holds after. That count is the load the
// run puts on the HDB: batching splits the compute, so it must not change it, and the second
// table shows whether it does. The rows read back from the HDB are compared with the
// unbatched run's (`same`).
//
// WHAT IT DOES NOT. Read a production HDB: the window is generated, so "rows fetched" is
// what the worker asks the source for, not a measurement of a remote store. A change that
// made batches read less than the window (per-batch source reads) would show here as more
// fetch calls and fewer rows each; this design reads once.
//
// Numbers from one machine are a comparison between rows of one run, not a figure to quote.
// PeachQ reports no usable space figure for `\ts` (every row shows the same `mb`): read memory
// from KDB-X. Times, and the `same` check, mean the same on both.

\l src/init.q
\l src/etl/init.q

\d .

.qdev.bench.worker:`hdb_fx_markouts_backfill

/ What the instrumented run records: stage timings, fetches, writes.
.qdev.bench.stagelog:()
.qdev.bench.fetchlog:()
.qdev.bench.writelog:()
.qdev.bench.arg:()
.qdev.bench.state:()

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
        price:1.0+n?0.5; fill_id:`$"f",/:string til n; clock:n#`venue);
    legs:distinct raze .qpipe.transform.fx_markout.legs_of[spec;exe];
    m:count legs;
    mid:1.0+0.5*m?1f;
    s:(m*q)#legs;
    mids:(m*q)#mid;
    books:([] source_time:(t0-0D00:00:01)+asc (m*q)?span+0D00:00:20; sym:s; source:(m*q)#`a;
        bid_prices:enlist each mids-0.0001; bid_sizes:(m*q)#enlist enlist 1e6;
        ask_prices:enlist each mids+0.0001; ask_sizes:(m*q)#enlist enlist 1e6; clock:(m*q)#`venue);
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

/ ---------------------------------------------------------------- the whole window, stage by stage

/ Run one window stage, recording its name, time (ms) and peak memory (bytes) from q's \ts.
/ @param name the stage
/ @param f the stage function {[worker;state]}
/ @param w the worker
/ @param s the window's state
/ @return the stage's output state
.qdev.bench.stage:{[name;f;w;s]
    `.qdev.bench.arg set (f;w;s);
    r:system "ts .qdev.bench.state:.[.qdev.bench.arg 0;(.qdev.bench.arg 1;.qdev.bench.arg 2)]";
    .qdev.bench.stagelog,:enlist (name;r 0;r 1);
    .qdev.bench.state}

/ The date partitions a root holds.
/ @param root the HDB root
/ @return the partition directories' names, as symbols
.qdev.bench.dates:{[root] ds where {x like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"} each string ds:key root}

/ The window's table, read back from the HDB root with its symbols as symbols.
/ @param root the HDB root
/ @return the fx_markout rows of every date partition, sorted by identity
.qdev.bench.hdb_rows:{[root]
    @[`.;`sym;:;get ` sv root,`sym];
    ds:.qdev.bench.dates root;
    unsym:{$[(abs type x) within 20 76h; `$string x; x]};
    one:{[root;unsym;d]
        dir:hsym `$(string .Q.par[root;"D"$string d;`fx_markout]),"/";
        if[()~key dir; :()];
        t:flip {x til count x} each flip select from get dir;
        flip (cols t)!unsym each value flip t}[root;unsym] each `$string ds;
    r:raze one where 0<count each one;
    `venue`fill_id`horizon`kind`step xasc r}

/ A clean slate for one run: configuration, status directory, a fixture-writing run, no coverage,
/ lock or checkpoint, and empty logs.
.qdev.bench.fresh:{[]
    w:.qdev.bench.worker;
    setenv[`UQF_STATUS_DIR;"build/bench-status"]; system "mkdir -p build/bench-status";
    .qetl.cfg.reset[]; .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""]; setenv[`UQF_FIXTURE_WRITES;enlist "1"]; setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
    `.qdev.bench.stagelog`.qdev.bench.fetchlog`.qdev.bench.writelog set' (();();());
    ![`.;();0b;enlist `etl_coverage];
    @[{system "rm -f ",x};.qetl.coverage.ledger_path[];{[e] (::)}];
    .qetl.job.bounded.state.release_lock w;
    .qetl.job.bounded.state.clear_checkpoint w;}

/ Put the counters in: the window "fetched" from memory with every fetch counted, every stage timed,
/ every write counted.
/ @param w the worker
/ @param inputs the synthetic window
/ @return what restore needs to take them out again
.qdev.bench.instrument:{[w;inputs]
    fkey:`$".qpipe.job.",string[w],".fetch";
    saved:(fkey;get fkey;.qetl.job.bounded.window_stages;get `.qetl.io.write_keyed);
    fkey set {[inputs;from_ts;to_ts]
        r:@[inputs;`executions;{[e;a;b] select from e where source_time>=a, source_time<b}[;from_ts;to_ts]];
        .qdev.bench.fetchlog,:enlist (count r`executions;count r`market_data);
        `state`result`attempts!(`ok;r;1)}[inputs];
    stages0:saved 2;
    `.qetl.job.bounded.window_stages set key[stages0]!{[n;f] .qdev.bench.stage[n;f;;]}'[key stages0;value stages0];
    `.qetl.io.write_keyed set {[w0;m;t;b;o] .qdev.bench.writelog,:enlist enlist count b; w0[m;t;b;o]}[saved 3];
    saved}

/ Take the counters out again.
/ @param saved what instrument returned
.qdev.bench.restore:{[saved]
    (saved 0) set saved 1;
    `.qetl.job.bounded.window_stages set saved 2;
    `.qetl.io.write_keyed set saved 3;}

/ One end-to-end run of the worker over the window, with its stages, fetches and writes recorded.
/ @param spec the batching declaration, or (::)
/ @param inputs the synthetic window
/ @return a dictionary of the run's measurements and the rows it left in the HDB
.qdev.bench.once:{[spec;inputs]
    w:.qdev.bench.worker;
    .qdev.bench.fresh[];
    .qdev.bench.use[spec];
    root:`$":",first system "mktemp -d";
    d:.qetl.job.bounded.def w;
    d[`io]:.qetl.io.hdb[root;`time];
    `.qetl.job.bounded.worker_cfg upsert (w,d cols value .qetl.job.bounded.worker_cfg);
    saved:.qdev.bench.instrument[w;inputs];
    r:@[{[w]
        .qpipe.job.hdb_fx_markouts_backfill.init`source_version`range_from`range_to!(`bench;2026.09.17D00:00:00;2026.09.18D00:00:00);
        .qpipe.job.hdb_fx_markouts_backfill.run[]};w;{[e] `state`error!(`error;e)}];
    .qdev.bench.restore saved;
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    if[not `completed~r`state; -1 "run did not complete: ",.Q.s1 r; exit 1];
    rows:.qdev.bench.hdb_rows root;
    parts:count .qdev.bench.dates root;
    system "rm -rf ",1_string root;
    `state`rows`stages`fetches`writes`partitions!(r`state;rows;.qdev.bench.stagelog;.qdev.bench.fetchlog;.qdev.bench.writelog;parts)}

/ Compare configurations end to end, stage by stage.
/ @param inputs the synthetic window
/ @param groups symbols per batch for the grouped runs
/ @param reps timed runs per configuration; the fastest total is reported
/ @return (stage table; load table), one row per configuration and stage / configuration
.qdev.bench.run_stages:{[inputs;groups;reps]
    base:.qdev.bench.once[(::);inputs];
    cfg:([] name:("unbatched";"per symbol, books narrowed"); k:0N 1; narrow:01b);
    cfg,:([] name:{"grouped x",string x} each groups; k:groups; narrow:(count groups)#1b);
    one:{[c;inputs;base;reps]
        w:.qdev.bench.worker;
        spec:$[null c`k; (::); .qetl.job.bounded.batching.require[w;`source`batching!((.qetl.job.bounded.def w)`source;.qdev.bench.spec[c`k;c`narrow])]];
        runs:reps#enlist spec;
        rs:.qdev.bench.once[;inputs] each runs;
        tot:{sum x[;1]} each rs`stages;
        r:rs first iasc tot;
        `config`state`same`stages`fetches`writes`partitions!(c`name;r`state;(r`rows)~base`rows;r`stages;r`fetches;r`writes;r`partitions)}[;inputs;base;reps] each cfg;
    st:raze {([] config:count[x`stages]#enlist x`config; stage:x[`stages][;0]; ms:x[`stages][;1]; mb:`float$x[`stages][;2]%1e6)} each one;
    / one row per configuration and stage: time summed over the window's hourly sub-windows, the peak memory the largest of them
    st:0!select windows:count i, ms:sum ms, peak_mb:max mb by config, stage from st;
    cfgrank:(distinct one`config)?st`config;
    stagerank:`fetch`ready`transform`check`publish?st`stage;
    st:st iasc (10*cfgrank)+stagerank;
    ld:{([] config:enlist x`config; state:enlist x`state; fetches:count x`fetches; rows_read:sum x[`fetches][;0]; book_rows_read:sum x[`fetches][;1];
        writes:count x`writes; rows_written:sum raze x`writes; partitions:x`partitions; same:enlist x`same)} each one;
    (st;raze ld)}

\d .

system "c 60 300";
o:.Q.opt .z.x;
opt:{[o;k;d] $[k in key o; first o k; d]};
n:"J"$opt[o;`execs;"20000"];
q:"J"$opt[o;`quotes;"200"];
groups:"J"$"," vs opt[o;`groups;"2,4"];
reps:"J"$opt[o;`reps;"3"];
-1 "bench_batching: ",string[n]," executions, ",string[q]," quotes per book, ",string[reps]," timed runs each";
rows:.qdev.bench.run[n;q;groups;reps];
-1 .Q.s rows;
bad:exec config from rows where not same;
if[count bad; -1 "DIFFERS from the unbatched output: ",", " sv bad; exit 1];
-1 "every batched run matches the unbatched output";
-1 "";
-1 "the whole window, stage by stage (ms, peak MB), and the load on the source and the HDB:";
res:.qdev.bench.run_stages[.qdev.bench.inputs;groups;reps];
-1 .Q.s res 0;
-1 .Q.s res 1;
bad2:exec config from res 1 where not same;
if[count bad2; -1 "HDB rows DIFFER from the unbatched run: ",", " sv bad2; exit 1];
bad3:exec config from res 1 where not state=`completed;
if[count bad3; -1 "run did not complete: ",", " sv bad3; exit 1];
-1 "every end-to-end run completed and left the unbatched run's rows in the HDB";
exit 0
