/ run_mapped_heap.q - the markout twin's heap against a synthetic HDB (#2288). Run as a CHILD process by
/ test_mapped_books.q, or by hand to measure at the scale a deployment runs at:
/ .
/   q tests/q/run_mapped_heap.q build   <dir> <rows per day> <executions>
/   q tests/q/run_mapped_heap.q measure <dir> <rows per day> <executions>
/ .
/ build writes a partitioned HDB: market_data for two dates (the day before the executions' and their own), each
/ <rows per day> rows sorted by sym, source, source_time with p# on sym, the flat top, rid and clock - and NO depth
/ columns at all, so any markout that selected a ladder would fail rather than merely be slow - and an executions
/ table of <executions> fills on the second date. Written a (sym, source) group at a time by appending to the column
/ files, so building ten terabytes' worth of rows needs only one group's memory.
/ .
/ Three more modes put the same questions about a book's DEPTH and give the measurement something to fail on:
/   build_deep <dir> <rows per day>  a day of market_data WITH three-level ladders (nested columns, written whole: keep it to a few million rows)
/   measure_depth <dir> <rows> <books>  the heap `depth_at` adds reading <books> matched books' ladders through their rid
/   measure_copy <dir> <rows> <executions>  the heap the OLD way adds: the same books selected with a where clause beyond the date. It
/                                       materialises every column it names, so it grows with the rows - the control that shows the other
/                                       measurements could have failed
/ .
/ And one for the reference variants (#2289):
/   measure_variants <dir> <rows> <executions> <variants> <selects>
/                                    the batch priced under <variants> declared variants (vwap at a quantity each, three combines) whose
/                                    SELECTs are <selects> distinct offsets: gathers the books once (.qetl.variant.read), evaluates them all
/                                    and prints  RESULT|rows|execs|heap after the gather|peak|mapped|slice rows|ladder rows read|variants|
/                                    SELECT groups|gather ms|evaluate ms|heap after the evaluate
/ .
/ measure loads that HDB (memory-mapping it), runs the hdb_fx_markouts source's query against it through a stand-in
/ handle and scores the result with the worker's transform, and prints one line:
/   RESULT|<rows per day>|<executions>|<used bytes the run added>|<peak bytes it added>|<bytes mapped>|<rows scored>
/ `used` is .Q.w[]'s heap after the run less before it, both after a garbage collection, with the run's inputs and
/ outputs still held - what the twin keeps while it holds a window.

args:.z.x;
mode:`$args 0;
dir:args 1;
rows:"J"$args 2;
execs:"J"$args 3;
day:2026.09.17;
before_day:day-1;

/ ---------------------------------------------------------------- the synthetic market
pairs:`EURUSD`GBPUSD`AUDUSD`NZDUSD`USDCAD`USDCHF`USDJPY`USDSEK`USDNOK`USDDKK;
venues:`a`b`c`d;
domain:`#asc distinct pairs,venues,`fx`venue`receipt`LPX;

/ One day of market_data written to its partition, a (sym; source) run at a time.
build_day:{[root;d;n]
    dir:` sv root,(`$string d),`market_data;
    system "mkdir -p ",1_string dir;
    groups:pairs cross venues;
    per:n div count groups;
    names:`time`sym`source`market`source_time`clock`bid`ask`rid;
    {[dir;d;per;done;g]
        sy:g 0; so:g 1;
        t:(`timestamp$d)+asc per?86400000000000;
        px:1+per?1f;
        chunk:`time`sym`source`market`source_time`clock`bid`ask`rid!(t;per#`sym$sy;per#`sym$so;per#`sym$`fx;t;per#`sym$`venue;px;px+0.0002;done+til per);
        {[dir;c;v] f:` sv dir,c; $[()~key f; f set v; f upsert v]}[dir]'[key chunk;value chunk];
        done+per}[dir;d;per]/[0;groups];
    (` sv dir,`.d) set names;
    / p# on sym: the groups were written in sym order, so the column is grouped
    sf:` sv dir,`sym;
    sf set `p#get sf;
    count get ` sv dir,`rid};

/ The executions on the second day: fills of the pairs at random times between 10:00 and 12:00.
build_executions:{[root;d;n]
    dir:` sv root,(`$string d),`executions;
    system "mkdir -p ",1_string dir;
    t:(`timestamp$d)+36000000000000+asc n?7200000000000;
    e:([] time:t; sym:n?pairs; source_time:t; venue:n#`LPX; side:n?1 -1; size:n#1e6; price:1+n?1f;
        fee:n#0f; fee_ccy:n#`; fill_id:`$"f",/:string til n; book:n#`; product:n#`; clock:n#`venue);
    (` sv dir,`) set .Q.ens[root;e;`sym];
    n};

/ ---------------------------------------------------------------- build
if[mode=`build;
    root:hsym `$dir;
    system "rm -rf ",dir;
    system "mkdir -p ",dir;
    (` sv root,`sym) set domain;
    `sym set domain;
    build_day[root;before_day;rows div 10];
    build_day[root;day;rows];
    / every partition holds every table, or a query across dates fails
    build_executions[root;before_day;0];
    build_executions[root;day;execs];
    -1 "BUILT|",string rows;
    exit 0];

/ A day with depth: three levels each side, the flat top the first level. Whole-table, so for sizes that fit in memory.
build_deep:{[root;n]
    system "rm -rf ",1_string root;
    system "mkdir -p ",1_string root;
    (` sv root,`sym) set domain;
    `sym set domain;
    t:(`timestamp$day)+asc n?86400000000000;
    px:1+n?1f;
    rows:([] time:t; sym:n?pairs; source:n?venues; market:n#`fx; source_time:t; bid_prices:enlist each px; bid_sizes:n#enlist 1e6 2e6 3e6;
        ask_prices:enlist each px+0.0002; ask_sizes:n#enlist 1e6 2e6 3e6; clock:n#`venue);
    rows:update bid_prices:{x,x-0.0001 0.0002}each px, ask_prices:{x,x+0.0001 0.0002}each px+0.0002 from rows;
    rows:`sym`source`source_time xasc rows;
    rows:update bid:first each bid_prices, ask:first each ask_prices, rid:til count i from rows;
    market_data::rows;
    .Q.dpfts[root;day;`sym;`market_data;`sym];
    / the previous day too, so every query can look back across midnight
    market_data::0#rows;
    .Q.dpfts[root;before_day;`sym;`market_data;`sym];
    n};

if[mode=`build_deep;
    build_deep[hsym `$dir;rows];
    -1 "BUILT|",string rows;
    exit 0];

/ ---------------------------------------------------------------- measure
/ The tree first (its paths are relative), then the HDB, which changes the working directory.
system "l src/init.q";
system "l src/etl/init.q";
system "l ",dir;

used:{`long$(.Q.w[])`used};
peak:{`long$(.Q.w[])`peak};
mapped:{`long$(.Q.w[])`mmap};
.Q.gc[];
u0:used[];
p0:peak[];

/ The heap `depth_at` adds reading some matched books' ladders through their rid.
if[mode=`measure_depth;
    probes:([] sym:execs?pairs; time:(`timestamp$day)+43200000000000+execs?3600000000000);
    books:.qetl.mapped.tob_at`probes`read_from`skew`venues!(probes;(`timestamp$day)-1D;1;`all);
    .Q.gc[];
    u0:used[];
    deep:.qetl.mapped.depth_at`books`cols!(books;`bid_prices`ask_prices);
    .Q.gc[];
    -1 "RESULT|",("|" sv string (rows;execs;used[]-u0;0;0;count deep));
    exit 0];

/ The heap the OLD way adds: the day's books for the syms selected with a where beyond the date, every column named.
if[mode=`measure_copy;
    got:select source_time, sym, source, bid, ask, clock from market_data where date=day, sym in pairs, source_time>=(`timestamp$day)+43200000000000;
    .Q.gc[];
    -1 "RESULT|",("|" sv string (rows;execs;used[]-u0;0;0;count got));
    exit 0];
/ the stand-in handle runs each query here, as the HDB would
h:{value x};

/ <variants> variants over the batch: gather once, evaluate all.
if[mode=`measure_variants;
    nv:"J"$args 4;
    ns:"J"$args 5;
    probes:([] sym:execs?pairs; time:(`timestamp$day)+43200000000000+execs?3600000000000);
    reg:{[i;ns]
        n:`$"v",string i;
        qty:1e4*1+i div 3;
        off:`timespan$1000000000*i mod ns;
        .qetl.reference.register[n;`venues`stale`offset`transform`combine`params!(`all;`spec;off;`vwap;`best`median`wmean i mod 3;enlist[`qty]!enlist qty)];
        n};
    names:reg[;ns] each til nv;
    .Q.gc[];
    u0:used[];
    t0:.z.p;
    r:.qetl.variant.read[h;probes;(`timestamp$day)-1D;1;names;0D01:00:00];
    gms:(`long$.z.p-t0) div 1000000;
    .Q.gc[];
    ug:used[]-u0;
    t1:.z.p;
    res:.qetl.variant.evaluate[names;r`slice;0D01:00:00;probes`sym;probes`time;(count probes)#`venue];
    ems:(`long$.z.p-t1) div 1000000;
    .Q.gc[];
    -1 "RESULT|",("|" sv string (rows;execs;ug;peak[]-p0;mapped[];count r`slice;r`reads;nv;r`groups;gms;ems;used[]-u0));
    exit 0];
got:.qpipe.source.hdb_fx_markouts.query[h;(`timestamp$day);(`timestamp$day)+1D];
out:.qetl.transform.apply[`hdb_fx_markouts_long;got];
m1:mapped[];
.Q.gc[];
u1:used[];
p1:peak[];
-1 "RESULT|",("|" sv string (rows;execs;u1-u0;p1-p0;m1;count out));
exit 0
