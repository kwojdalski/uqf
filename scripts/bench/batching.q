// batching.q - compare unbatched, per-symbol and grouped-symbol markout scoring (#1921).
//
//   q scripts/bench/batching.q [-execs 20000] [-quotes 200] [-groups 2,4] [-reps 3]
//
//   -execs   executions in the window (default 20000)
//   -quotes  quotes per book per symbol (default 200)
//   -groups  symbols per batch for the grouped runs, comma separated (default 2,4)
//   -reps    timed repetitions per configuration; the fastest is reported (default 3)
//   -reuse   rows the reuse cache may hold between batches in the source-side runs (default 4 x quotes)
//   -pool    the pairs executions are drawn from, comma separated (default: the router's pool). A pool
//            with a cross and its legs (EURPLN,EURUSD,USDPLN) is a window whose batches share books
//   -stages  skip the in-process transform table and run only the end-to-end one
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
// SOURCE-SIDE READS (#1921). The second table runs the worker against a real second q process
// that serves the same window as an HDB would (`executions`, `market_data`, date-partitioned in
// name only: the tables carry a `date` column), so the fetch is a real IPC read, not a function call
// in the worker's own process, and its memory is not charged to the worker. Configurations:
//
//   unbatched                            one transform, the whole window read once
//   per symbol, window read once         batches over a window fetched whole, each given the books
//                                        its legs need (#1922's design: less compute, same read)
//   per symbol, source-side              the window read as executions and a presence summary; each
//                                        batch reads from the source the books IT needs
//   per symbol, source-side, reuse       the same, with a book one batch read kept for the next
//   grouped xK ...                       K symbols a batch, the last two of those
//
// The server counts what it was asked, so the load on the HDB is a number the HDB reports: queries,
// rows and bytes returned (executions and books apart), and the milliseconds it spent. Rows SCANNED
// are not counted: the stand-in is a table in memory, and a scan there says nothing about a
// partitioned store. Peak memory is the worker process's (`\ts` per stage).
//
// WHAT IT DOES NOT. Read a production HDB: the window is generated, and the server holds it in memory.
// Rows returned, queries and the worker's memory are real; the HDB's scan cost is not measured.
//
// Numbers from one machine are a comparison between rows of one run, not a figure to quote.
// PeachQ reports no usable space figure for `\ts` (every row shows the same `mb`): read memory
// from KDB-X. Times, and the `same` check, mean the same on both.

/ The HDB stand-in (-serve) is a plain q process: it needs none of the library.
if[not `serve in key .Q.opt .z.x; system "l src/init.q"; system "l src/etl/init.q"];

\d .

.qdev.bench.worker:`hdb_fx_markouts_backfill

/ The hour the synthetic executions fall in.
.qdev.bench.hour:`range_from`range_to!(2026.09.17D10:00:00.000000000;2026.09.17D11:00:00.000000000)

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

/ The pairs the window draws from: the default pool, or those named by -pool.
.qdev.bench.chosen:()

/ A window of `n executions and the books their legs need, `q quotes each.
/ @param n executions
/ @param q quotes per book
/ @return the source's inputs: executions and market_data
.qdev.bench.window:{[n;q]
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    pool:$[count .qdev.bench.chosen; .qdev.bench.chosen; .qdev.bench.pool[]];
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
    tm:.qdev.bench.time[".qdev.bench.out:.qetl.job.bounded.transform_window[`",string[w],";.qdev.bench.hour;.qdev.bench.inputs]";reps];
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
    base:`venue`fill_id`horizon`kind`step xasc .qetl.job.bounded.transform_window[.qdev.bench.worker;.qdev.bench.hour;.qdev.bench.inputs];
    cfg:([] name:("unbatched";"per symbol";"per symbol, books narrowed"); k:0N 1 1; narrow:001b);
    cfg,:([] name:{"grouped x",string x} each groups; k:groups; narrow:(count groups)#1b);
    out:.qdev.bench.measure[;base;reps] each cfg;
    .qdev.bench.use[(::)];
    out}


/ ---------------------------------------------------------------- the HDB, as a second process

/ What the server was asked since it was last read: (kind; rows; bytes; ms) per query.
.qdev.bench.hdbstats:()

/ Read and reset the server's counts. Run on the server.
/ @return the list of (kind; rows returned; bytes returned; ms) since the last read
.qdev.bench.take_stats:{[] r:.qdev.bench.hdbstats; `.qdev.bench.hdbstats set (); r}

/ Become the HDB: hold the saved window and answer queries, counting each. Run on the server.
/ @param file the saved window, `executions`market_data
.qdev.bench.serve:{[file]
    d:get hsym `$file;
    `executions set update date:`date$source_time from d`executions;
    `market_data set update date:`date$source_time from d`market_data;
    / the benchmark that started this process is its client, and so is each worker run it makes:
    / when the last of them has gone, so does the server
    `.z.pc set {[h] if[(h>0) and 0=count key .z.W; exit 0]};
    `.z.pg set {[x]
        if[(first x)~`.qdev.bench.take_stats; :.qdev.bench.take_stats[]];
        t0:.z.p;
        r:value x;
        ms:`long$(.z.p-t0)%1000000;
        k:$[98h=type r; $[`bid_prices in cols r; `books; `executions]; `other];
        .qdev.bench.hdbstats,:enlist (k;count r;count -8!r;ms);
        r};}

/ Start the server process on a port nothing else is listening on, and wait until it answers.
/ A port is tried, and another when nothing answers on it in ten seconds: the machine may be
/ running other processes, and this one chooses its port at random.
/ @param inputs the synthetic window
/ @return `port`dir`handle!(...)
.qdev.bench.start_hdb:{[inputs]
    dir:first system "mktemp -d";
    (hsym `$dir,"/inputs") set inputs;
    system "S ",string `int$.z.p mod 1000003;
    h:0Ni;
    attempts:0;
    while[(null h) and attempts<3;
        port:20000+first 1?20000;
        system (first .z.X)," scripts/bench/batching.q -serve ",dir,"/inputs -p ",string[port]," -q < /dev/null > ",dir,"/server.log 2>&1 &";
        tries:0;
        while[(null h) and tries<150;
            h:@[{hopen (`$"::",string x;2000)};port;{0Ni}];
            if[null h; system "sleep 0.2"];
            tries+:1];
        attempts+:1];
    if[null h; -1 "the HDB stand-in did not start; see ",dir,"/server.log"; exit 1];
    `port`dir`handle!(port;dir;h)}

/ Stop the server and remove its files.
/ @param hdb what start_hdb returned
.qdev.bench.stop_hdb:{[hdb]
    @[hdb`handle;"exit 0";{[e] (::)}];
    system "rm -rf ",hdb`dir;}

/ ---------------------------------------------------------------- the whole window, stage by stage

/ What the instrumented run records: stage timings and writes.
.qdev.bench.stagelog:()
.qdev.bench.writelog:()
.qdev.bench.arg:()
.qdev.bench.state:()

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

/ A clean slate for one run: configuration, status directory, the credential of the HDB stand-in,
/ no coverage, lock or checkpoint, and empty logs.
/ @param port the stand-in's port
.qdev.bench.fresh:{[port]
    w:.qdev.bench.worker;
    setenv[`UQF_STATUS_DIR;"build/bench-status"]; system "rm -rf build/bench-status; mkdir -p build/bench-status";
    .qetl.cfg.reset[]; .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""]; setenv[`UQF_FIXTURE_WRITES;enlist "1"];
    setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;"localhost:",string port];
    `.qdev.bench.stagelog`.qdev.bench.writelog set' (();());
    ![`.;();0b;enlist `etl_coverage];
    @[{system "rm -f ",x};.qetl.coverage.ledger_path[];{[e] (::)}];
    .qetl.job.bounded.state.release_lock w;
    .qetl.job.bounded.state.clear_checkpoint w;}

/ Put the counters in: every stage timed, every write counted.
/ @return what restore needs to take them out again
.qdev.bench.instrument:{[]
    saved:(.qetl.job.bounded.window_stages;get `.qetl.io.write_keyed);
    stages0:saved 0;
    `.qetl.job.bounded.window_stages set key[stages0]!{[n;f] .qdev.bench.stage[n;f;;]}'[key stages0;value stages0];
    `.qetl.io.write_keyed set {[w0;m;t;b;o] .qdev.bench.writelog,:enlist enlist count b; w0[m;t;b;o]}[saved 1];
    saved}

/ Take the counters out again.
/ @param saved what instrument returned
.qdev.bench.restore:{[saved]
    `.qetl.job.bounded.window_stages set saved 0;
    `.qetl.io.write_keyed set saved 1;}

/ One end-to-end run of the worker over the window, with its stages and writes recorded and the
/ HDB stand-in's counts read after.
/ @param spec the batching declaration, or (::)
/ @param hdb what start_hdb returned
/ @return a dictionary of the run's measurements and the rows it left in the HDB
.qdev.bench.once:{[spec;hdb]
    w:.qdev.bench.worker;
    .qdev.bench.fresh[hdb`port];
    hdb[`handle] (`.qdev.bench.take_stats;::);
    .qetl.job.bounded.batching.reset_reads[];
    .qdev.bench.use[spec];
    root:`$":",first system "mktemp -d";
    d:.qetl.job.bounded.def w;
    d[`io]:.qetl.io.hdb[root;`time];
    `.qetl.job.bounded.worker_cfg upsert (w,d cols value .qetl.job.bounded.worker_cfg);
    saved:.qdev.bench.instrument[];
    r:@[{[w]
        .qpipe.job.hdb_fx_markouts_backfill.init`source_version`range_from`range_to!(`bench;2026.09.17D00:00:00;2026.09.18D00:00:00);
        .qpipe.job.hdb_fx_markouts_backfill.run[]};w;{[e] `state`error!(`error;e)}];
    .qdev.bench.restore saved;
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    if[not `completed~r`state; -1 "run did not complete: ",.Q.s1 r; exit 1];
    served:hdb[`handle] (`.qdev.bench.take_stats;::);
    rows:.qdev.bench.hdb_rows root;
    parts:count .qdev.bench.dates root;
    system "rm -rf ",1_string root;
    `state`rows`stages`served`writes`partitions`reads!(r`state;rows;.qdev.bench.stagelog;served;.qdev.bench.writelog;parts;.qetl.job.bounded.batching.counts)}

/ A declaration: k execution symbols a batch (1 is a batch per symbol). `mode` is how the books
/ reach the batch: `window (the whole window's, narrowed to the batch's legs), `source (read from
/ the source by the batch), `reuse (the same, keeping `reuse` rows between batches).
/ @param k symbols per batch
/ @param mode `window, `source or `reuse
/ @param reuse rows kept between batches under `reuse
/ @return the declaration, as define stores it
.qdev.bench.spec_for:{[k;mode;reuse]
    w:.qdev.bench.worker;
    b:`group_by`max_rows`max_groups!(enlist `sym;1000000000;k);
    b:$[mode=`window; b,enlist[`context]!enlist .qdev.bench.legs_context;
        mode=`source; b,enlist[`source_read]!enlist `batch;
        b,`source_read`reuse_rows!(`batch;reuse)];
    .qetl.job.bounded.batching.require[w;`source`batching!((.qetl.job.bounded.def w)`source;b)]}

/ Compare configurations end to end, stage by stage, against a real second process.
/ @param hdb what start_hdb returned
/ @param groups symbols per batch for the grouped runs
/ @param reuse rows kept between batches
/ @param reps timed runs per configuration; the fastest total is reported
/ @return (stage table; load table), one row per configuration and stage / configuration
.qdev.bench.run_stages:{[hdb;groups;reuse;reps]
    base:.qdev.bench.once[(::);hdb];
    cfg:([] name:("unbatched";"per symbol, window read once";"per symbol, source-side";"per symbol, source-side, reuse");
        k:0N 1 1 1; mode:`none`window`source`reuse);
    cfg,:([] name:{"grouped x",string[x],", window read once"} each groups; k:groups; mode:(count groups)#`window);
    cfg,:([] name:{"grouped x",string[x],", source-side, reuse"} each groups; k:groups; mode:(count groups)#`reuse);
    one:{[c;hdb;base;reuse;reps]
        spec:$[null c`k; (::); .qdev.bench.spec_for[c`k;c`mode;reuse]];
        rs:{[spec;hdb;unused] .qdev.bench.once[spec;hdb]}[spec;hdb] each reps#0;
        tot:{sum x[;1]} each rs`stages;
        r:rs first iasc tot;
        `config`state`same`stages`served`writes`partitions`reads!(`$c`name;r`state;(r`rows)~base`rows;r`stages;r`served;r`writes;r`partitions;r`reads)}[;hdb;base;reuse;reps] each cfg;
    st:raze {([] config:count[x`stages]#enlist x`config; stage:x[`stages][;0]; ms:x[`stages][;1]; mb:`float$x[`stages][;2]%1e6)} each one;
    / one row per configuration and stage: time summed over the window's hourly sub-windows, the peak memory the largest of them
    st:0!select windows:count i, ms:sum ms, peak_mb:max mb by config, stage from st;
    cfgrank:(distinct one`config)?st`config;
    stagerank:`fetch`ready`transform`check`publish?st`stage;
    st:st iasc (10*cfgrank)+stagerank;
    pk:{([] config:enlist x`config; worker_peak_mb:enlist max `float$x[`stages][;2]%1e6)} each one;
    ld:{[x]
        sq:x`served;
        ex:sq where `executions=sq[;0];
        bk:sq where `books=sq[;0];
        ([] config:enlist x`config; state:enlist x`state;
            hdb_queries:count sq; exec_rows:sum ex[;1]; book_rows:sum bk[;1]; mb_returned:`float$(sum sq[;2])%1e6; hdb_ms:sum sq[;3];
            keys_read:(x`reads)`reads; keys_reused:(x`reads)`hits; evicted:(x`reads)`evicted;
            writes:count x`writes; rows_written:sum raze x`writes; partitions:x`partitions; same:enlist x`same)} each one;
    (st;(raze pk) lj `config xkey raze ld)}

\d .

/ The run.
/ @param o the command line, parsed
.qdev.bench.main:{[o]
    opt:{[o;k;d] $[k in key o; first o k; d]};
    if[`pool in key o; `.qdev.bench.chosen set `$"," vs first o`pool];
    n:"J"$opt[o;`execs;"20000"];
    q:"J"$opt[o;`quotes;"200"];
    groups:"J"$"," vs opt[o;`groups;"2,4"];
    reps:"J"$opt[o;`reps;"3"];
    reuse:"J"$opt[o;`reuse;string 4*q];
    -1 "bench_batching: ",string[n]," executions, ",string[q]," quotes per book, ",string[reps]," timed runs each, reuse bound ",string[reuse]," rows";
    if[not `stages in key o;
        rows:.qdev.bench.run[n;q;groups;reps];
        -1 .Q.s rows;
        bad:exec config from rows where not same;
        if[count bad; -1 "DIFFERS from the unbatched output: ",", " sv bad; exit 1];
        -1 "every batched run matches the unbatched output";
        -1 ""];
    `.qdev.bench.inputs set $[`stages in key o; .qdev.bench.window[n;q]; .qdev.bench.inputs];
    -1 "the whole window against a second process, stage by stage (ms, peak MB), and the load on the HDB:";
    hdb:.qdev.bench.start_hdb[.qdev.bench.inputs];
    res:.qdev.bench.run_stages[hdb;groups;reuse;reps];
    .qdev.bench.stop_hdb hdb;
    -1 .Q.s res 0;
    -1 .Q.s res 1;
    bad2:exec config from res 1 where not same;
    if[count bad2; -1 "HDB rows DIFFER from the unbatched run: ",", " sv bad2; exit 1];
    bad3:exec config from res 1 where not state=`completed;
    if[count bad3; -1 "run did not complete: ",", " sv bad3; exit 1];
    -1 "every end-to-end run completed and left the unbatched run's rows in the HDB";
    exit 0}

system "c 60 300";
o:.Q.opt .z.x;
$[`serve in key o; .qdev.bench.serve first o`serve; .qdev.bench.main o];
