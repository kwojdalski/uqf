// hdb_transfer_example.q - one shot: build a kdb+ database, then move its
// trades into a second kdb+ database with a scaffolded ETL job.
//
//   q scripts/examples/hdb_transfer_example.q            (from the repository root)
//   q scripts/examples/hdb_transfer_example.q -keep      keep both databases afterwards
//
// No TorQ, no running stack, nothing outside one temporary directory:
//
//   1. builds a SOURCE HDB on disk - three days of trades, date-partitioned and
//      splayed, symbols enumerated against its own sym file, like any HDB;
//   2. points the scaffolded job at it: source hdb_transfer reads the files
//      directly (the `local` transport), worker hdb_transfer_backfill adds each
//      trade's notional, and an HDB IO manager writes trades_copy into a
//      second, empty DESTINATION HDB;
//   3. runs the first two days, then the same range again - every window is
//      already covered, so nothing is fetched - then all three days, which
//      fetches only the third;
//   4. reads the destination back from disk and checks it against the source,
//      row for row, exiting 1 on any difference.
//
// The job is the one `uqs job new hdb_transfer --kind backfill --transport local`
// scaffolded (src/etl/sources/hdb_transfer.q, src/etl/workers/hdb_transfer_backfill.q).
// docs/scaffolding/hdb-transfer.md walks through it, and how to point it at a
// real HDB or run it on the stack.

\l src/init.q
\l src/etl/init.q

\d .qhdbtx

/ Everything this run writes lives under one directory.
/ TMPDIR usually ends in "/"; trimmed so the paths print cleanly.
dir:({$["/"=last x;-1_x;x]} $[count getenv`TMPDIR;getenv`TMPDIR;"/tmp"]),"/uqf_hdb_transfer_",string .z.i
src:hsym `$dir,"/source_hdb"
dst:hsym `$dir,"/destination_hdb"
days:2026.01.05 2026.01.06 2026.01.07
syms:`EURUSD`USDJPY`GBPUSD`AUDUSD
mids:syms!1.0842 151.20 1.2655 0.6612

/ One day of trades: n of them, at deterministic times through the day.
/ trade_id is unique across the whole table - the source's row key.
day_trades:{[d;first_id;n]
    s:n?syms;
    ([] time:(`timestamp$d)+asc n?0D23:59:59; trade_id:first_id+til n; sym:s;
        price:mids[s]*1+0.001*-0.5+n?1f; size:1000*1+n?5000; side:n?`buy`sell)}

/ Write one day's partition of `trades`, enumerating sym against the source's
/ own sym file - what .Q.dpft does, without clobbering a global `trades`
/ (the ETL tree has a plant table of that name in this process).
write_day:{[d;ignored;t]
    p:` sv src,(`$string d),`trades,`;
    p set .Q.en[src;t];
    count t}

/ The run spec for [from;to).
spec:{[from_ts;to_ts] `source_version`range_from`range_to!(`v1;from_ts;to_ts)}

/ Run the worker over one range, as the backfill process does.
run:{[from_ts;to_ts]
    ns:(.qetl.job.bounded.def `hdb_transfer_backfill)`ns;
    (` sv ns,`init)[spec[from_ts;to_ts]];
    (` sv ns,`run)[]}

/ Exit 1 with a message when a check fails, so the q-scripts lane fails too.
check:{[ok;what] $[ok; -1 "  ok    ",what; [-1 "  FAIL  ",what; exit 1]]}

\d .

-1 "\n== 1. a source kdb+ database: ",1_string .qhdbtx.src;
system "rm -rf ",.qhdbtx.dir;
system "mkdir -p ",.qhdbtx.dir,"/status";
setenv[`UQF_STATUS_DIR;.qhdbtx.dir,"/status"];
sizes:150 160 170;
written:.qhdbtx.write_day'[.qhdbtx.days;1+0,sums -1_sizes;.qhdbtx.day_trades'[.qhdbtx.days;1+0,sums -1_sizes;sizes]];
-1 "  ",(", " sv {string[x],": ",string[y]," trades"}'[.qhdbtx.days;written]);
source:raze {.qetl.source.local_read[.qhdbtx.src;`trades;`timestamp$x;`timestamp$x+1]} each .qhdbtx.days;

-1 "\n== 2. the job points at it, and writes to ",1_string .qhdbtx.dst;
/ The credential IS the source HDB's directory, for a `local` source.
setenv[`UQF_SOURCE_CRED_HDB_TRANSFER;1_string .qhdbtx.src];
/ Where rows go is the runner's choice: here, a second HDB, partitioned by the
/ source's time column - what torq_backfill.q does with the stack's own HDB.
.qetl.io.default:.qetl.io.hdb[.qhdbtx.dst;`time];

-1 "\n== 3. run it";
r1:.qhdbtx.run[2026.01.05D00:00;2026.01.07D00:00];
-1 "  first two days:        ",string r1`state;
r2:.qhdbtx.run[2026.01.05D00:00;2026.01.07D00:00];
-1 "  same range again:      ",string[r2`state],"  (every window already covered - nothing fetched)";
r3:.qhdbtx.run[2026.01.05D00:00;2026.01.08D00:00];
-1 "  all three days:        ",string[r3`state],"  (only the third day was missing)";
.qhdbtx.check[(r1`state;r2`state;r3`state)~`completed`idle`completed;"completed, then idle, then completed"];
.qhdbtx.check[(r1`rows_published;r2`rows_published;r3`rows_published)~(sum 2#sizes;0;last sizes);
    "rows written: ",(" then " sv string (r1;r2;r3)@\:`rows_published)," - the re-run wrote nothing, the last only the new day"];

-1 "\n== 4. read the destination back from disk";
copied:raze {.qetl.source.local_read[.qhdbtx.dst;`trades_copy;`timestamp$x;`timestamp$x+1]} each .qhdbtx.days;
show select trades:count i, notional:sum notional by date:`date$time from copied;
.qhdbtx.check[(count copied)=count source;"every source trade arrived: ",string count copied];
.qhdbtx.check[(`trade_id xasc delete notional from copied)~`trade_id xasc source;"each row exactly as the source holds it"];
.qhdbtx.check[all 1e-9>abs (copied`notional)-(copied`price)*copied`size;"notional = price * size on every row"];
.qhdbtx.check[3=count .qetl.source.local_dates[.qhdbtx.dst;-0Wd;0Wd];"one partition per day in the destination"];

$[`keep in key .Q.opt .z.x;
    -1 "\nkept: ",.qhdbtx.dir,"  (q ",1_string[.qhdbtx.dst]," opens the destination as an HDB)";
    [system "rm -rf ",.qhdbtx.dir; -1 "\nremoved ",.qhdbtx.dir," - pass -keep to look at the two databases"]];
exit 0
