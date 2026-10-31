// hdb_fx_markouts_example.q - run both FX markout backfills once, on the
// hdb_fx_markouts source's fixture, into throwaway HDBs, and show what each
// published:
//
//   hdb_fx_markouts_backfill      -> fx_markout       (long: one row per horizon, kind and step)
//   hdb_fx_markout_wide_backfill  -> fx_markout_wide  (wide: one row per horizon)
//
//   q scripts/examples/hdb_fx_markouts_example.q          (from the repository root)
//   q scripts/examples/hdb_fx_markouts_example.q -keep    keep what it wrote, under build/
//
// No TorQ and no running stack: everything it writes is under one directory
// in build/, removed at the end unless -keep. The worked-examples test runs it, and
// it exits 1 unless each run completes and publishes at least one row.
//
// TWO RUNS, KEPT APART. Both workers read one source over one window, so the
// fixture, the range and the build directory are shared. Their state is not:
// each run gets its own status directory - where its status file, checkpoint,
// lock and coverage ledger live - and its own destination HDB, and starts with
// no coverage ledger in memory, as a backfill process of its own would. So
// neither run can find the other's coverage and report idle.

\l src/init.q
\l src/etl/init.q

\d .qhdb_fx_markoutsex

dir:(first system"pwd"),"/build/hdb_fx_markouts_example_",string .z.i
fixture:.qpipe.source.hdb_fx_markouts.fixture[]

/ Whole days around the fixture's rows, half-open, so every row is inside.
range_from:`timestamp$`date$min fixture[`executions]`source_time
range_to:`timestamp$1+`date$max fixture[`executions]`source_time

/ A worker's own corner of dir: its destination HDB and its status directory.
dst:{[worker] hsym `$dir,"/",string[worker],"/destination_hdb"}
status:{[worker] dir,"/",string[worker],"/status"}

/ Run worker over [range_from;range_to), as its backfill process does, with
/ nothing of an earlier run in reach: its own status directory, its own
/ destination, and no coverage ledger left in memory by the run before.
run:{[worker]
    system "mkdir -p ",quoted status worker;
    setenv[`UQF_STATUS_DIR;status worker];
    ![`.;();0b;enlist`etl_coverage];
    / Where rows go is the runner's choice: here, an HDB partitioned on `time`.
    .qetl.io.default:.qetl.io.hdb[dst worker;`time];
    ns:(.qetl.job.bounded.def worker)`ns;
    (` sv ns,`init)[`source_version`range_from`range_to!(`v1;range_from;range_to)];
    (` sv ns,`run)[]}

/ What a run left in its destination HDB, read back from disk.
published:{[worker;table] .qetl.source.local_read[dst worker;table;range_from;range_to]}

/ The rows the coverage ledger in memory holds for dataset ds. The ledger is
/ the root's etl_coverage, named through `. because a bare name here would
/ mean one in this namespace.
covered:{[ds] $[`etl_coverage in tables `.; count select from `.[`etl_coverage] where dataset=ds; 0]}

/ Whether each worker's status directory holds its own checkpoint and not
/ the other's: the file a rerun resumes from is never shared.
own_state:{[]
    w:`hdb_fx_markouts_backfill`hdb_fx_markout_wide_backfill;
    has:{[dirof;worker] (`$string[worker],".checkpoint") in key hsym `$status dirof};
    (all has'[w;w]) and not any has'[w;reverse w]}

/ A path as one shell word: dir is the absolute workspace path, which may hold
/ spaces or quotes, and an unquoted `rm -rf` would split it into other paths.
quoted:{[path] "'",ssr[path;"'";"'\"'\"'"],"'"}

/ Exit 1 with a message when a check fails, so the worked-examples test fails too.
check:{[ok;what] $[ok; -1 "  ok    ",what; [-1 "  FAIL  ",what; exit 1]]}

\d .

-1 "\n== 1. hdb_fx_markouts's fixture: ",string[count .qhdb_fx_markoutsex.fixture`executions]," execution(s)";
-1 "  both workers run over [",string[.qhdb_fx_markoutsex.range_from],";",string[.qhdb_fx_markoutsex.range_to],")";
system "rm -rf ",.qhdb_fx_markoutsex.quoted .qhdb_fx_markoutsex.dir;
/ No credential: the workers read the fixture, the demo path it warns about,
/ which a run must ask for before it may write it (#1082).
setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
setenv[`UQF_FIXTURE_WRITES;enlist "1"];

-1 "\n== 2. run hdb_fx_markouts_backfill into ",1_string .qhdb_fx_markoutsex.dst`hdb_fx_markouts_backfill;
long_run:.qhdb_fx_markoutsex.run`hdb_fx_markouts_backfill;
-1 "  ",string[long_run`state],", ",string[long_run`rows_published]," row(s) published";
.qhdb_fx_markoutsex.check[`completed~long_run`state;"the run completed"];
.qhdb_fx_markoutsex.check[0<long_run`rows_published;"it published rows"];
.qhdb_fx_markoutsex.check[0<.qhdb_fx_markoutsex.covered`fx_markout;"it recorded coverage of fx_markout"];

-1 "\n== 3. read fx_markout back from disk (long: a row per horizon, kind and step)";
long_out:.qhdb_fx_markoutsex.published[`hdb_fx_markouts_backfill;`fx_markout];
show long_out;
.qhdb_fx_markoutsex.check[0<count long_out;"at least one row was written"];
.qhdb_fx_markoutsex.check[(count long_out)=long_run`rows_published;"every published row is on disk"];

-1 "\n== 4. run hdb_fx_markout_wide_backfill into ",1_string .qhdb_fx_markoutsex.dst`hdb_fx_markout_wide_backfill;
wide_run:.qhdb_fx_markoutsex.run`hdb_fx_markout_wide_backfill;
-1 "  ",string[wide_run`state],", ",string[wide_run`rows_published]," row(s) published";
.qhdb_fx_markoutsex.check[`completed~wide_run`state;"the run completed, not idle on the first run's state"];
.qhdb_fx_markoutsex.check[0<wide_run`rows_published;"it published rows"];
.qhdb_fx_markoutsex.check[0<.qhdb_fx_markoutsex.covered`fx_markout_wide;"it recorded coverage of fx_markout_wide"];
.qhdb_fx_markoutsex.check[0=.qhdb_fx_markoutsex.covered`fx_markout;"its ledger holds none of the first run's coverage"];
.qhdb_fx_markoutsex.check[.qhdb_fx_markoutsex.own_state[];"each status directory holds its own worker's checkpoint and none of the other's"];

-1 "\n== 5. read fx_markout_wide back from disk (wide: a row per horizon)";
wide_out:.qhdb_fx_markoutsex.published[`hdb_fx_markout_wide_backfill;`fx_markout_wide];
show wide_out;
.qhdb_fx_markoutsex.check[0<count wide_out;"at least one row was written"];
.qhdb_fx_markoutsex.check[(count wide_out)=wide_run`rows_published;"every published row is on disk"];

$[`keep in key .Q.opt .z.x;
    -1 "\nkept: ",.qhdb_fx_markoutsex.dir;
    [system "rm -rf ",.qhdb_fx_markoutsex.quoted .qhdb_fx_markoutsex.dir; -1 "\nremoved ",.qhdb_fx_markoutsex.dir," - pass -keep to look at it"]];
exit 0
