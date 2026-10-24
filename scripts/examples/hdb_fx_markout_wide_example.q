// hdb_fx_markout_wide_example.q - run the hdb_fx_markout_wide_backfill bounded worker once, on its
// own fixture, into a throwaway HDB, and show what it published.
//
//   q scripts/examples/hdb_fx_markout_wide_example.q          (from the repository root)
//   q scripts/examples/hdb_fx_markout_wide_example.q -keep    keep what it wrote, under build/
//
// No TorQ and no running stack: everything it writes is under one directory
// in build/, removed at the end unless -keep. The q-scripts lane runs it, and
// it exits 1 unless the run completes and publishes at least one row - so a
// quality check or a derive the scaffold left throwing fails it until written.
// Written by `uqs job new hdb_fx_markout_wide --kind backfill`; grow it with the job.

\l src/init.q
\l src/etl/init.q

\d .qhdb_fx_markout_wideex

dir:(first system"pwd"),"/build/hdb_fx_markout_wide_example_",string .z.i
dst:hsym `$dir,"/destination_hdb"
fixture:.qpipe.source.hdb_fx_markouts.fixture[]

/ Whole days around the fixture's rows, half-open, so every row is inside.
range_from:`timestamp$`date$min fixture[`executions]`source_time
range_to:`timestamp$1+`date$max fixture[`executions]`source_time

/ Run the worker over [range_from;range_to), as the backfill process does.
run:{[]
    ns:(.qetl.job.bounded.def `hdb_fx_markout_wide_backfill)`ns;
    (` sv ns,`init)[`source_version`range_from`range_to!(`v1;range_from;range_to)];
    (` sv ns,`run)[]}

/ What the run left in the destination HDB, read back from disk.
published:{[] .qetl.source.local_read[dst;`fx_markout_wide;range_from;range_to]}

/ A path as one shell word: dir is the absolute workspace path, which may hold
/ spaces or quotes, and an unquoted `rm -rf` would split it into other paths.
quoted:{[path] "'",ssr[path;"'";"'\"'\"'"],"'"}

/ Exit 1 with a message when a check fails, so the q-scripts lane fails too.
check:{[ok;what] $[ok; -1 "  ok    ",what; [-1 "  FAIL  ",what; exit 1]]}

\d .

-1 "\n== 1. hdb_fx_markouts's fixture: ",string[count .qhdb_fx_markout_wideex.fixture`executions]," execution(s)";
-1 "  run over [",string[.qhdb_fx_markout_wideex.range_from],";",string[.qhdb_fx_markout_wideex.range_to],")";
system "rm -rf ",.qhdb_fx_markout_wideex.quoted .qhdb_fx_markout_wideex.dir;
system "mkdir -p ",.qhdb_fx_markout_wideex.quoted .qhdb_fx_markout_wideex.dir,"/status";
setenv[`UQF_STATUS_DIR;.qhdb_fx_markout_wideex.dir,"/status"];
/ No credential: the worker reads the fixture, the demo path it warns about,
/ which a run must ask for before it may write it (#1082).
setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
setenv[`UQF_FIXTURE_WRITES;enlist "1"];

-1 "\n== 2. run hdb_fx_markout_wide_backfill into ",1_string .qhdb_fx_markout_wideex.dst;
/ Where rows go is the runner's choice: here, an HDB partitioned on `time`.
.qetl.io.default:.qetl.io.hdb[.qhdb_fx_markout_wideex.dst;`time];
r:.qhdb_fx_markout_wideex.run[];
-1 "  ",string[r`state],", ",string[r`rows_published]," row(s) published";
.qhdb_fx_markout_wideex.check[`completed~r`state;"the run completed"];

-1 "\n== 3. read fx_markout_wide back from disk";
out:.qhdb_fx_markout_wideex.published[];
show out;
.qhdb_fx_markout_wideex.check[0<count out;"at least one row was written"];
.qhdb_fx_markout_wideex.check[(count out)=r`rows_published;"every published row is on disk"];

$[`keep in key .Q.opt .z.x;
    -1 "\nkept: ",.qhdb_fx_markout_wideex.dir;
    [system "rm -rf ",.qhdb_fx_markout_wideex.quoted .qhdb_fx_markout_wideex.dir; -1 "\nremoved ",.qhdb_fx_markout_wideex.dir," - pass -keep to look at it"]];
exit 0
