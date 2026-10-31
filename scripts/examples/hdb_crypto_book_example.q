// hdb_crypto_book_example.q - run the hdb_crypto_book_backfill bounded worker once, on its
// own fixture, into a throwaway HDB, and show what it published.
//
//   q scripts/examples/hdb_crypto_book_example.q          (from the repository root)
//   q scripts/examples/hdb_crypto_book_example.q -keep    keep what it wrote, under build/
//
// No TorQ and no running stack: everything it writes is under one directory
// in build/, removed at the end unless -keep. The worked-examples test runs it, and
// it exits 1 unless the run completes and publishes at least one row - so a
// quality check or a derive the scaffold left throwing fails it until written.
// Written by `uqs job new hdb_crypto_book --kind backfill`; grow it with the job.

\l src/init.q
\l src/etl/init.q

\d .qcbookex

dir:(first system"pwd"),"/build/hdb_crypto_book_example_",string .z.i
dst:hsym `$dir,"/destination_hdb"
fixture:.qpipe.source.hdb_crypto_book.fixture[]

/ Whole days around the fixture's rows, half-open, so every row is inside.
range_from:`timestamp$`date$min fixture`source_time
range_to:`timestamp$1+`date$max fixture`source_time

/ Run the worker over [range_from;range_to), as the backfill process does.
run:{[]
    ns:(.qetl.job.bounded.def `hdb_crypto_book_backfill)`ns;
    (` sv ns,`init)[`source_version`range_from`range_to!(`v1;range_from;range_to)];
    (` sv ns,`run)[]}

/ What the run left in the destination HDB, read back from disk.
published:{[] .qetl.source.local_read[dst;`crypto_book_1s;range_from;range_to]}

/ A path as one shell word: dir is the absolute workspace path, which may hold
/ spaces or quotes, and an unquoted `rm -rf` would split it into other paths.
quoted:{[path] "'",ssr[path;"'";"'\"'\"'"],"'"}

/ Exit 1 with a message when a check fails, so the worked-examples test fails too.
check:{[ok;what] $[ok; -1 "  ok    ",what; [-1 "  FAIL  ",what; exit 1]]}

\d .

-1 "\n== 1. hdb_crypto_book's fixture: ",string[count .qcbookex.fixture]," row(s)";
-1 "  run over [",string[.qcbookex.range_from],";",string[.qcbookex.range_to],")";
system "rm -rf ",.qcbookex.quoted .qcbookex.dir;
system "mkdir -p ",.qcbookex.quoted .qcbookex.dir,"/status";
setenv[`UQF_STATUS_DIR;.qcbookex.dir,"/status"];
/ No credential: the worker reads the fixture, the demo path it warns about,
/ which a run must ask for before it may write it (#1082).
setenv[`UQF_SOURCE_CRED_HDB_CRYPTO_BOOK;""];
setenv[`UQF_FIXTURE_WRITES;enlist "1"];

-1 "\n== 2. run hdb_crypto_book_backfill into ",1_string .qcbookex.dst;
/ Where rows go is the runner's choice: here, an HDB partitioned on `time`.
.qetl.io.default:.qetl.io.hdb[.qcbookex.dst;`time];
r:.qcbookex.run[];
-1 "  ",string[r`state],", ",string[r`rows_published]," row(s) published";
.qcbookex.check[`completed~r`state;"the run completed"];

-1 "\n== 3. read crypto_book_1s back from disk";
out:.qcbookex.published[];
show out;
.qcbookex.check[0<count out;"at least one row was written"];
.qcbookex.check[(count out)=r`rows_published;"every published row is on disk"];

$[`keep in key .Q.opt .z.x;
    -1 "\nkept: ",.qcbookex.dir;
    [system "rm -rf ",.qcbookex.quoted .qcbookex.dir; -1 "\nremoved ",.qcbookex.dir," - pass -keep to look at it"]];
exit 0
