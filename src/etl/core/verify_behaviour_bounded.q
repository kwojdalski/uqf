/ verify_behaviour_bounded.q - a bounded worker's behaviours, exercised in a sandbox
/ (.qetl.verify, #1851). The streaming half and the sandbox are in verify_behaviour.q.
/ .
/ Window boundaries are judged from a capture; retries by replacing the source
/ read for one check; conflict handling by writing one captured batch twice through
/ the worker's own io manager and strategy into a sandbox table; coverage by a real
/ run, twice, in a sandbox status directory.

\d .

/ Do a capture's windows tile its range exactly, with every row inside it once?
/ @param worker the bounded worker
/ @param from_ts the range's first instant
/ @param to_ts the instant after its last
/ @param rows what the capture returned for the whole range, or (::)
/ @return a dict status, reason, evidence
/ @eg .qetl.verify.check_windows[`demo_deals_backfill;2026.09.11D00:00:00.000000000;2026.09.14D00:00:00.000000000;.qpipe.source.demo_deals.fixture[]]`status  ->  `UNVERIFIED
.qetl.verify.check_windows:{[worker;from_ts;to_ts;rows]
    cfg:.qetl.job.bounded.def worker;
    w:.qetl.job.bounded.runtime.windows[from_ts;to_ts;cfg`width];
    tc:(.qetl.source.def cfg`source)`time_column;
    tiled:((first w`range_from)=from_ts) and ((last w`range_to)=to_ts) and (all (w`range_from)<w`range_to)
        and all (1_ w`range_from)=-1_ w`range_to;
    ev:string[count w]," window(s) of ",string[cfg`width];
    if[not tiled; :`status`reason`evidence!(`FAIL;"the windows leave a gap or overlap inside the range";ev)];
    if[not 98h=type rows; :`status`reason`evidence!(`UNVERIFIED;"empty sample - no rows were captured to place in the windows";ev)];
    if[0=count rows; :`status`reason`evidence!(`UNVERIFIED;"empty sample - no rows were captured to place in the windows";ev)];
    t:rows tc;
    outside:sum (t<from_ts) or t>=to_ts;
    ks:(),cfg`target_key;
    twice:(count rows)-count distinct ks#rows;
    ev:ev,"; ",string[count rows]," row(s), ",string[outside]," outside the range, ",string[twice]," repeated across windows";
    $[outside>0; `status`reason`evidence!(`FAIL;"a window returned rows outside the range";ev);
      twice>0; `status`reason`evidence!(`FAIL;"a row came back from more than one window";ev);
      `status`reason`evidence!(`PASS;"";ev)]}

/ The source read as it was, kept while a check replaces it.
.qetl.verify.real_fetch:(::)

/ How many times the replaced source read was called.
.qetl.verify.faults:0

/ Private: the three fetches of check_retries - clean, after one injected transport
/ failure, and against a permanent data failure.
/ @private
.qetl.verify.retry_trials:{[w;f;t]
    clean:.qetl.job.bounded.fetch[w;f;t];
    `.qetl.verify.faults set 0;
    `.qetl.source.fetch_window set {[src;h;a;b]
        `.qetl.verify.faults set 1+.qetl.verify.faults;
        if[.qetl.verify.faults=1; '"connection refused (injected)"];
        .qetl.verify.real_fetch[src;h;a;b]};
    flaky:.qetl.job.bounded.fetch[w;f;t];
    `.qetl.source.fetch_window set {[src;h;a;b] '"schema mismatch (injected)"};
    broken:.qetl.job.bounded.fetch[w;f;t];
    (clean;flaky;broken;1b)}

/ Retries: a transport failure is retried and the window's rows arrive once; a data
/ failure is terminal on the first attempt. The source read is replaced for the check
/ and put back.
/ @param worker the bounded worker
/ @param from_ts the window's start
/ @param to_ts its end
/ @return a dict status, reason, evidence
/ @throws error when this is not a throwaway q
/ @eg .qetl.verify.check_retries[`demo_deals_backfill;2026.09.11D00:00:00.000000000;2026.09.12D00:00:00.000000000]`status  ->  `PASS
.qetl.verify.check_retries:{[worker;from_ts;to_ts]
    .qetl.verify.require_isolated[];
    why:.qetl.verify.not_exercisable[worker;`bounded];
    if[count why; :`status`reason`evidence!(`UNVERIFIED;why;"")];
    `.qetl.verify.real_fetch set .qetl.source.fetch_window;
    r:.[.qetl.verify.retry_trials;(worker;from_ts;to_ts);{[e] (`failed;e)}];
    `.qetl.source.fetch_window set .qetl.verify.real_fetch;
    if[(0h=type r) and `failed~first r; :.qetl.verify.did_not_complete last r];
    c:r 0; f:r 1; b:r 2;
    ev:"transient: ",string[f`state]," after ",string[f`attempts]," attempt(s); data error: ",string[b`state]," (",string[b`kind],") after ",string[b`attempts];
    $[not `ok~c`state; `status`reason`evidence!(`UNVERIFIED;"the window did not fetch cleanly, so retries cannot be judged";ev);
      not (`ok~f`state) and 2=f`attempts; `status`reason`evidence!(`FAIL;"a transport failure was not retried to success";ev);
      not (f`result)~c`result; `status`reason`evidence!(`FAIL;"the retried fetch returned different rows";ev);
      not (`failed~b`state) and 1=b`attempts; `status`reason`evidence!(`FAIL;"a data failure was retried or swallowed";ev);
      `status`reason`evidence!(`PASS;"";ev)]}

/ Conflict handling: one captured batch written twice through the worker's own io
/ manager and declared strategy, into a sandbox table, never the dataset's.
/ @param worker the bounded worker
/ @param batch the rows a capture returned, or (::)
/ @param from_ts the window's start
/ @param to_ts its end
/ @return a dict status, reason, evidence
/ @throws error when this is not a throwaway q
/ @eg .qetl.verify.check_conflict[`demo_deals_backfill;.qpipe.source.demo_deals.fixture[];2026.09.11D00:00:00.000000000;2026.09.16D00:00:00.000000000]`status  ->  `PASS
.qetl.verify.check_conflict:{[worker;batch;from_ts;to_ts]
    .qetl.verify.require_isolated[];
    why:.qetl.verify.not_exercisable[worker;`bounded];
    if[count why; :`status`reason`evidence!(`UNVERIFIED;why;"")];
    cfg:.qetl.job.bounded.def worker;
    if[not (::)~cfg`io; :`status`reason`evidence!(`UNVERIFIED;"the worker declares its own io manager, which the sandbox cannot redirect";"")];
    if[not 98h=type batch; :`status`reason`evidence!(`UNVERIFIED;"empty sample - no captured rows to write twice";"")];
    if[0=count batch; :`status`reason`evidence!(`UNVERIFIED;"empty sample - no captured rows to write twice";"")];
    strategy:.qetl.job.bounded.on_conflict worker;
    w:`range_from`range_to!(from_ts;to_ts);
    opts:`on_conflict`row_key`time_column`range_from`range_to`partition_column`partition`source_version!
        (strategy;cfg`target_key;cfg`window_column;from_ts;to_ts;cfg`partition_column;cfg`partition;`verify);
    target:`verify_conflict_sandbox;
    write:{[target;batch;opts] @[{[a] .qetl.io.write_keyed[.qetl.io.memory;a 0;a 1;a 2]};(target;batch;opts);{[e] (`refused;e)}]};
    one:write[target;batch;opts];
    two:write[target;batch;opts];
    held:$[target in tables `.; value target; ()];
    ks:(),cfg`target_key;
    n:count held;
    dup:n-count distinct ks#held;
    ev:string[strategy]," on ",(", " sv string ks),": ",string[n]," row(s) held after writing ",string[count batch]," twice, ",string[dup]," repeated";
    if[target in tables `.; ![`.;();0b;enlist target]];
    $[strategy=`fail; $[(0h=type two) and `refused~first two; `status`reason`evidence!(`PASS;"";ev,"; the second write was refused");
                        `status`reason`evidence!(`FAIL;"on_conflict fail accepted the same rows twice";ev)];
      strategy=`append; `status`reason`evidence!(`UNVERIFIED;"append keeps every write, so re-publication repeats rows by design; no conflict handling to judge";ev);
      dup>0; `status`reason`evidence!(`FAIL;"the same rows written twice left repeated keys";ev);
      n<>count batch; `status`reason`evidence!(`FAIL;"re-writing the same rows changed how many the table holds";ev);
      `status`reason`evidence!(`PASS;"";ev)]}

/ Private: the worker run twice over one range in the sandbox, as (first; second; ledger rows).
/ @private
.qetl.verify.coverage_trials:{[worker;from_ts;to_ts]
    ns:(.qetl.job.bounded.def worker)`ns;
    spec:`source_version`range_from`range_to!(`verify;from_ts;to_ts);
    saved:.qetl.io.default;
    fx:getenv`UQF_FIXTURE_WRITES;
    / the sandbox is the one place a fixture's rows may be written
    setenv[`UQF_FIXTURE_WRITES;"1"];
    `.qetl.io.default set .qetl.io.memory;
    pass:{[ns;spec] (get ` sv ns,`init) spec; r:(get ` sv ns,`run)[]; (get ` sv ns,`cleanup)[]; r};
    r1:.[pass;(ns;spec);{[e] (`failed;e)}];
    r2:.[pass;(ns;spec);{[e] (`failed;e)}];
    `.qetl.io.default set saved;
    setenv[`UQF_FIXTURE_WRITES;fx];
    (r1;r2;count select from .qetl.coverage.ledger[] where superseded_at=.qetl.coverage.still_current;1b)}

/ Coverage recording: a real run over the range, in a sandbox status directory, covers
/ every window it planned, publishes what a capture returned, and a second run over the
/ same range finds nothing left to do (range-and-version skipping).
/ @param worker the bounded worker
/ @param from_ts the range's first instant
/ @param to_ts the instant after its last
/ @param captured how many rows a capture returned for the range
/ @return a dict status, reason, evidence
/ @throws error when this is not a throwaway q
/ @eg .qetl.verify.check_coverage[`demo_deals_backfill;2026.09.11D00:00:00.000000000;2026.09.14D00:00:00.000000000;3]`status  ->  `PASS
.qetl.verify.check_coverage:{[worker;from_ts;to_ts;captured]
    .qetl.verify.require_isolated[];
    why:.qetl.verify.not_exercisable[worker;`bounded];
    if[count why; :`status`reason`evidence!(`UNVERIFIED;why;"")];
    cfg:.qetl.job.bounded.def worker;
    if[not (::)~cfg`io; :`status`reason`evidence!(`UNVERIFIED;"the worker declares its own io manager, which the sandbox cannot redirect";"")];
    n:count .qetl.job.bounded.runtime.windows[from_ts;to_ts;cfg`width];
    t:.qetl.verify.sandboxed[{[a] .qetl.verify.coverage_trials[a 0;a 1;a 2]};(worker;from_ts;to_ts)];
    if[99h=type t; :t];
    r1:t 0; r2:t 1;
    if[(0h=type r1) or 0h=type r2; :.qetl.verify.did_not_complete "a run threw: ",$[0h=type r1; last r1; last r2]];
    ev:"run 1: ",string[r1`state]," ",string[r1`windows_completed]," of ",string[n]," window(s), ",string[r1`rows_published]," row(s); ledger holds ",string[t 2],
        "; run 2: ",string[r2`state]," ",string[r2`rows_published]," row(s)";
    $[not `completed~r1`state; `status`reason`evidence!(`FAIL;"the run did not complete over a clean fixture range";ev);
      (t 2)<>n; `status`reason`evidence!(`FAIL;"the ledger does not hold one covered window per window planned";ev);
      (captured>0) and (r1`rows_published)<>captured; `status`reason`evidence!(`FAIL;"the run published a different number of rows than the capture returned";ev);
      not ((`idle~r2`state) and 0=r2`rows_published); `status`reason`evidence!(`FAIL;"a second run over the same range and version fetched again";ev);
      `status`reason`evidence!(`PASS;"";ev)]}

\d .
