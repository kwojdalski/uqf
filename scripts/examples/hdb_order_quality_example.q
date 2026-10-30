// hdb_order_quality_example.q - run the hdb_order_quality_backfill bounded worker once, on its
// own fixture, into a throwaway HDB, and show what it published.
//
//   q scripts/examples/hdb_order_quality_example.q          (from the repository root)
//   q scripts/examples/hdb_order_quality_example.q -keep    keep what it wrote, under build/
//
// No TorQ and no running stack: everything it writes is under one directory
// in build/, removed at the end unless -keep. The worked-examples test runs it, and
// it exits 1 unless the run completes and publishes at least one row - so a
// quality check or a derive the scaffold left throwing fails it until written.
// Written by `uqs job new hdb_order_quality --kind backfill`; grown with the job.

\l src/init.q
\l src/etl/init.q

\d .qhdb_order_qualityex

dir:(first system"pwd"),"/build/hdb_order_quality_example_",string .z.i
dst:hsym `$dir,"/destination_hdb"
fixture:.qpipe.source.hdb_orders.fixture[]`orders

/ Whole days around the fixture's rows, half-open, so every row is inside.
range_from:`timestamp$`date$min fixture`time
range_to:`timestamp$1+`date$max fixture`time

/ Run the worker over [range_from;range_to), as the backfill process does.
run:{[]
    ns:(.qetl.job.bounded.def `hdb_order_quality_backfill)`ns;
    (` sv ns,`init)[`source_version`range_from`range_to!(`v1;range_from;range_to)];
    (` sv ns,`run)[]}

/ What the run left in the destination HDB, read back from disk.
published:{[] .qetl.source.local_read[dst;`order_quality;range_from;range_to]}

/ A path as one shell word: dir is the absolute workspace path, which may hold
/ spaces or quotes, and an unquoted `rm -rf` would split it into other paths.
quoted:{[path] "'",ssr[path;"'";"'\"'\"'"],"'"}

/ Exit 1 with a message when a check fails, so the worked-examples test fails too.
check:{[ok;what] $[ok; -1 "  ok    ",what; [-1 "  FAIL  ",what; exit 1]]}

\d .

-1 "\n== 1. hdb_orders' fixture: ",string[count .qhdb_order_qualityex.fixture]," row(s)";
-1 "  run over [",string[.qhdb_order_qualityex.range_from],";",string[.qhdb_order_qualityex.range_to],")";
system "rm -rf ",.qhdb_order_qualityex.quoted .qhdb_order_qualityex.dir;
system "mkdir -p ",.qhdb_order_qualityex.quoted .qhdb_order_qualityex.dir,"/status";
setenv[`UQF_STATUS_DIR;.qhdb_order_qualityex.dir,"/status"];
/ No credential: the worker reads the fixture, the demo path it warns about,
/ which a run must ask for before it may write it (#1082).
setenv[`UQF_SOURCE_CRED_HDB_ORDERS;""];
setenv[`UQF_FIXTURE_WRITES;enlist "1"];

-1 "\n== 2. run hdb_order_quality_backfill into ",1_string .qhdb_order_qualityex.dst;
/ Where rows go is the runner's choice: here, an HDB partitioned on `time`.
.qetl.io.default:.qetl.io.hdb[.qhdb_order_qualityex.dst;`time];
r:.qhdb_order_qualityex.run[];
-1 "  ",string[r`state],", ",string[r`rows_published]," row(s) published";
.qhdb_order_qualityex.check[`completed~r`state;"the run completed"];

-1 "\n== 3. read order_quality back from disk";
out:.qhdb_order_qualityex.published[];
show out;
.qhdb_order_qualityex.check[0<count out;"at least one row was written"];
.qhdb_order_qualityex.check[(count out)=r`rows_published;"every published row is on disk"];

$[`keep in key .Q.opt .z.x;
    -1 "\nkept: ",.qhdb_order_qualityex.dir;
    [system "rm -rf ",.qhdb_order_qualityex.quoted .qhdb_order_qualityex.dir; -1 "\nremoved ",.qhdb_order_qualityex.dir," - pass -keep to look at it"]];
exit 0
