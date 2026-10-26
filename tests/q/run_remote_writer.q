/ run_remote_writer.q - a backfill extracted in THIS process and written by
/ ANOTHER, which owns the HDB (#1650).
/ .
/ The destination (tests/q/remote_writer_instance.q) holds the HDB and its
/ scoped writer; this process runs demo_deals_backfill on its fixture with
/ .qetl.io.remote.manager as its io manager, so every window's batch crosses a
/ real handle and is committed by the destination's receiver. This process
/ never writes the HDB's files.
/ .
/ WHAT THIS PROVES that the unit suite cannot: the receiver's gate admits the
/ worker's real messages over IPC, coverage is recorded only for windows the
/ destination committed, a window whose acknowledgement was lost is resent on
/ the next run and acknowledged as a duplicate rather than appended twice, and
/ finish over the wire finishes the destination's partitions.
/ .
/ Its own lane (`scripts/test.py q-remote-writer`), as run_two_instances is:
/ it needs a second process and a port.

\l src/init.q
\l src/etl/init.q
\l tests/lib/qunit.q
\l tests/lib/support.q

statusdir:getenv `UQF_STATUS_DIR;
if[0=count statusdir; '"run_remote_writer: set UQF_STATUS_DIR to a fresh directory"];
system"mkdir -p ",statusdir;
Q:.qt.support.q_interpreter[];

failures:0;
check:{[label;ok]
    -1 $[ok;"pass  ";"FAIL  "],label;
    if[not ok; `failures set failures+1];
    ok}

/ --- start the destination ------------------------------------------------

root:statusdir,"/hdb";
port:5510+`int$rand 400;
log_path:statusdir,"/remote_writer.log";
child:"QHOME=",getenv[`QHOME]," ",Q," tests/q/remote_writer_instance.q -p ",string[port],
      " -root ",root," -journal ",statusdir,"/journal < /dev/null > ",log_path," 2>&1 & echo $!";
pid:"J"$first system child;
ready:0b; tries:0;
while[(not ready) and tries<100;
    h:@[{hopen (`$":localhost:",string x;200)};port;{0Ni}];
    if[not null h; ready:1b];
    if[not ready; [system"sleep 0.2"; tries+:1]]];
stop:{[pid] @[{system"kill ",string x};pid;{[e] (::)}];}
if[not check["the destination is listening";ready]; [-1 each read0 hsym `$log_path; exit 1]];

/ --- extract here, commit there ---------------------------------------------

setenv[`UQF_FIXTURE_WRITES;enlist "1"];
setenv[`UQF_ON_CONFLICT;"append"];
.qetl.cfg.reset[];
spec:{[a;b] `source_version`range_from`range_to!(`v1;a;b)};
d:{[n] 2026.09.10D00:00:00.000000000+n*1D};
fixture_rows:{[a;b] count select from .qpipe.source.demo_deals.fixture[] where deal_time>=a, deal_time<b};

/ The first window's answer is lost after the destination committed it.
lost:0b;
lossy:{[h;m] r:h m; if[(`.qetl.io.remote.submit~first m) and not lost; [`lost set 1b; '"connection lost"]]; r}[h];
.qetl.io.default:.qetl.io.remote.manager[lossy;`uat;`demo_deals];
.qpipe.job.demo_deals_backfill.init[spec[d 1;d 4]];
r1:.qpipe.job.demo_deals_backfill.run[];
check["the window whose acknowledgement was lost failed here";1=r1`windows_failed];
check["the others were committed";2=r1`windows_completed];
check["and the destination holds all three days, the lost one included";
    (fixture_rows[d 1;d 4])=sum h(`.rw.rows;::)];

.qetl.io.default:.qetl.io.remote.manager[{[h;m] h m}[h];`uat;`demo_deals];
.qpipe.job.demo_deals_backfill.init[spec[d 1;d 4]];
r2:.qpipe.job.demo_deals_backfill.run[];
check["the re-run resends only the failed window";1=r2`windows_completed];
check["which the destination acknowledges without appending it again";
    (fixture_rows[d 1;d 4])=sum h(`.rw.rows;::)];
check["coverage now holds the whole range";
    .qetl.coverage.is_covered[`demo_deals;`;`$"v1~fixture";.z.p;d 1;d 4]];
check["nothing was written to an HDB from this process";0=count .qetl.io.touched];

st:@[h;(`.qetl.io.remote.status;::);{x}];
check["the gate refuses the receiver's status - it is not an entry point";st~"remote_writer: not an entry point"];
st:@[h;(`.qetl.io.remote.forget;`x);{x}];
check["and the operator's forget";st~"remote_writer: not an entry point"];

stop pid;
-1 "";
-1 $[failures=0; "remote writer: all checks passed"; "remote writer: ",string[failures]," check(s) FAILED"];
exit failures>0
