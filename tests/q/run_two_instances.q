/ run_two_instances.q - two kdb+ processes, and data moved from one to the
/ other through the data-engineering framework.
/ .
/ The UPSTREAM is a plain q process on the starter pack's HDB
/ (tests/q/upstream_instance.q) - the smallest kdb+ instance with real data,
/ standing in for a system someone else runs. THIS process is the
/ orchestrator's side: it loads the framework, points the upstream_trades
/ worker at the other process through the credential the framework already
/ reads, and runs a bounded backfill of trades across the wire.
/ .
/ WHAT THIS PROVES that nothing else does. Every other lane runs a worker on
/ its FIXTURE - a credential is never set, so .qetl.job.bounded.connect, a source's live
/ `query` and .qetl.source.validate_live have never executed anywhere, and the
/ coverage tool reports them as never entered. They run here, against a
/ genuine second process: the connection is opened, the schema is validated
/ against the real `meta`, the query is evaluated remotely, the rows come
/ back, the transform reshapes them, coverage is recorded, a second run is
/ idle, and a restatement re-fetches only what it withdrew.
/ .
/ Its own lane (`scripts/test.py q-two-instances`) rather than a q-unit test,
/ for the reason run_backfill_process is: it needs a second process and a
/ port, which the hermetic suite must not.

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q
\l scripts/processes/torq_pipeline.q
\l src/etl/init.q

statusdir:getenv `UQFSTATUSDIR;
if[0=count statusdir; '"run_two_instances: set UQFSTATUSDIR to a fresh directory"];
system"mkdir -p ",statusdir;
Q:.testutil.q_interpreter[];

failures:0;
check:{[label;ok]
    -1 $[ok;"pass  ";"FAIL  "],label;
    if[not ok; `failures set failures+1];
    ok}

/ --- start the upstream, and wait until it answers -----------------------

port:5010+`int$rand 500;
log_path:statusdir,"/upstream.log";
child:"QHOME=",getenv[`QHOME]," ",Q," tests/q/upstream_instance.q -p ",string[port],
      " < /dev/null > ",log_path," 2>&1 & echo $!";
pid:"J"$first system child;
-1 "upstream: pid ",string[pid]," port ",string port;

/ Poll rather than sleep a fixed time: the HDB load takes as long as it
/ takes, and a fixed wait is either too short on a slow disk or wasted on a
/ fast one. Ten seconds is far more than it needs.
ready:0b; tries:0;
while[(not ready) and tries<50;
    h:@[{hopen (`$":localhost:",string x;200)};port;{0Ni}];
    if[not null h; [ready:1b; hclose h]];
    if[not ready; [system"sleep 0.2"; tries+:1]]];
check["the upstream process is listening";ready];

stop_upstream:{[pid] @[{system"kill ",string x};pid;{[e] (::)}];}

if[not ready; [stop_upstream pid; -1 "upstream never came up; log:"; -1 each read0 hsym `$log_path; exit 1]];

/ --- point the worker at it through the framework's own credential --------

/ The credential is the address, and it is set HERE, in the environment,
/ because that is the only place the framework reads it from.
/ With it set, .qetl.job.bounded.init opens a live handle instead of using the fixture.
setenv[`UQF_SOURCE_CRED_UPSTREAM_TRADES;"localhost:",string port];
check["the framework sees a credential for the source";.qetl.source.has_credentials `upstream_trades];

/ --- the live schema check, against the real meta ----------------

h:hopen `$":localhost:",string port;
check["validate_live accepts the declaration against the upstream's real meta";
    1b~@[{.qetl.source.validate_live[`upstream_trades;x]};h;{[e] -1 "  ",e; 0b}]];
/ The same half-open window the worker will ask for, counted the same way
/ the source's query selects it - so the expected count and the delivered
/ count come from one definition of the window.
counts:h({[from_ts;to_ts]
    w:select size from trade where date within `date$(from_ts;to_ts), time>=from_ts, time<to_ts;
    (count w; exec count i from w where size>0)
  };2015.01.07D09:00:00;2015.01.07D11:00:00);
hclose h;
upstream_rows:counts 0; expected_rows:counts 1;
-1 "  upstream holds ",string[upstream_rows]," trades in the two-hour window, ",
   string[upstream_rows-expected_rows]," of them zero-size";
check["the window has data to move";expected_rows>0];
check["and some zero-size rows for the transform to drop, or that rule is untested here";upstream_rows>expected_rows];

/ --- move two hours of trades across --------------------------------------

from_ts:2015.01.07D09:00:00; to_ts:2015.01.07D11:00:00;
.qpipe.job.upstream_trades_backfill.init[`source_version`range_from`range_to!(`v1;from_ts;to_ts)];
check["init opened a live handle rather than falling back to the fixture";not null .qpipe.job.upstream_trades_backfill.handle];
r:.qpipe.job.upstream_trades_backfill.run[];
check["the run completed";`completed~r`state];
check["two one-hour windows";2=r`windows_completed];
check["no window failed";0=r`windows_failed];
check["every non-zero-size upstream row arrived, and no zero-size one did";expected_rows=count imported_trades];
check["...and the run reports the same count";expected_rows=r`rows_published];
check["the transform reshaped them: long size, 1/-1 side, venue symbol";
    "sjj"~exec t from meta[imported_trades] where c in `venue`size`side];
check["no zero-size trade was imported";all (exec size from imported_trades)>0];
check["no side other than 1 or -1 came through";all (exec side from imported_trades) in 1 -1];
check["coverage records the range as complete";
    .qetl.coverage.is_covered[`imported_trades;`;`v1;.z.p;from_ts;to_ts]];
.qpipe.job.upstream_trades_backfill.cleanup[];

/ --- a second run is idle, and moves nothing twice -------------------------

.qpipe.job.upstream_trades_backfill.init[`source_version`range_from`range_to!(`v1;from_ts;to_ts)];
r2:.qpipe.job.upstream_trades_backfill.run[];
check["a second run over a covered range is idle";`idle~r2`state];
check["and publishes nothing further";expected_rows=count imported_trades];
.qpipe.job.upstream_trades_backfill.cleanup[];

/ --- a restatement withdraws one hour, and only that hour is re-fetched ----

n_withdrawn:.qetl.coverage.supersede[`imported_trades;`;`v1;2015.01.07D10:00:00;to_ts];
-1 "  supersede withdrew ",string[n_withdrawn]," claim(s); missing now: ",.Q.s1 .qetl.coverage.missing[`imported_trades;`;`v1;.z.p;from_ts;to_ts];
.qpipe.job.upstream_trades_backfill.init[`source_version`range_from`range_to!(`v1;from_ts;to_ts)];
r3:.qpipe.job.upstream_trades_backfill.run[];
-1 "  re-run: state ",string[r3`state],", windows ",string[r3`windows_completed],", cursor ",string r3`cursor;
check["after superseding one window, exactly one window is re-run";1=r3`windows_completed];
check["the range reads as covered again";.qetl.coverage.is_covered[`imported_trades;`;`v1;.z.p;from_ts;to_ts]];
.qpipe.job.upstream_trades_backfill.cleanup[];

/ --- an unreachable upstream is refused, not silently substituted ---------

stop_upstream pid;
system"sleep 0.3";
setenv[`UQF_SOURCE_CRED_UPSTREAM_TRADES;"localhost:",string port];
err:@[{[a;b] .qpipe.job.upstream_trades_backfill.init[`source_version`range_from`range_to!(`v1;a;b)]; ""}[from_ts];to_ts;{x}];
check["with the upstream gone, init refuses rather than using the fixture";err like "*cannot reach*"];
check["the refusal says what it is refusing to do";err like "*refusing to start*"];
/ init acquired the single-instance lock before it tried to connect, so it
/ is still held; release it the way a crashed process's successor would.
.qetl.job.bounded.state.release_lock `upstream_trades_backfill;

-1 "";
-1 $[failures=0; "two instances: all checks passed"; "two instances: ",string[failures]," check(s) FAILED"];
exit failures>0
