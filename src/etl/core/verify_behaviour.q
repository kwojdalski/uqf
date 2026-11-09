/ verify_behaviour.q - exercising a dataset's producers in an isolated sandbox
/ (.qetl.verify, #1851).
/ .
/ Each check runs the producer's OWN machinery - .qetl.job.stream.tick, the
/ worker's init and run, .qetl.job.bounded.fetch - against its fixture, with the
/ status directory pointed at a throwaway directory and the publish seam on a
/ recorder, so cursors, checkpoints, locks and coverage are the sandbox's. Fault
/ injection replaces a function for the length of one check and puts it back.
/ .
/ NEVER A PRODUCTION PROCESS. `require_isolated` refuses to run anywhere that is
/ not a fresh q: inside a TorQ-named process, or one that already holds a coverage
/ ledger. A producer whose source is live is not exercised: faults are injected on
/ fixtures only.
/ .
/ WHAT IS EXERCISED, and what is not, is the catalogue `behaviour_catalogue`
/ below: a behaviour with no harness here is listed UNVERIFIED with the reason,
/ and the suites that cover it generically are named.

\d .

/ Refuse to run fault injection anywhere that could be a live process.
/ @return 1b
/ @throws error naming what makes this process unsafe
/ @eg .qetl.verify.require_isolated[]
.qetl.verify.require_isolated:{[]
    p:.qetl.run.proc_name[];
    if[not null p; '"verify: refusing to inject faults inside process ",string[p]," - behaviours run only in a throwaway q, never in a TorQ process"];
    if[`etl_coverage in tables `.; '"verify: refusing to run - this process already holds a coverage ledger, so it is not a throwaway q"];
    1b}

/ Private: one behaviour's result row.
/ @private
.qetl.verify.behaviour_row:{[name;mode;producer;status;reason;evidence]
    ([] behaviour:enlist name; mode:enlist mode; producer:enlist producer; status:enlist status; reason:enlist reason; evidence:enlist evidence)}

/ Run a function with UQF_STATUS_DIR pointing at a fresh directory, and remove it after.
/ @param f the function
/ @param args its arguments, a list
/ @return what f returned
/ @throws error when f does, after the environment is restored and the directory removed
/ @eg .qetl.verify.in_sandbox[{[x] x+1};enlist 1]
.qetl.verify.in_sandbox:{[f;args]
    .qetl.verify.require_isolated[];
    was_dir:getenv`UQF_STATUS_DIR;
    dir:first system"mktemp -d";
    setenv[`UQF_STATUS_DIR;dir];
    r:.[f;args;{[e] (`sandbox_failed;e)}];
    setenv[`UQF_STATUS_DIR;was_dir];
    system"rm -rf ",dir;
    if[(0h=type r) and `sandbox_failed~first r; 'last r];
    r}

/ What the sandboxed recorder was handed, as (table; rows).
.qetl.verify.sent:([] tbl:`symbol$(); n:`long$())

/ Private: run `f` with the job's publish on a recorder (`failing` throwing on its
/ first call), and the publish put back after.
/ @private
.qetl.verify.with_recorder:{[job;failing;f]
    / f is monadic over the job, applied here - a projection built by the caller would run before the wiring
    ns:` sv .qetl.job.stream.namespace[job],`publish;
    saved:get ns;
    `.qetl.verify.sent set 0#.qetl.verify.sent;
    `.qetl.verify.armed set failing;
    .qetl.job.stream.wire[job;{[t;rows]
        if[.qetl.verify.armed; `.qetl.verify.armed set 0b; '"publish refused (injected)"];
        `.qetl.verify.sent upsert (t;count rows); count rows}];
    r:@[f;job;{[e] (`failed;e)}];
    ns set saved;
    r}

.qetl.verify.armed:0b

/ Private: why a producer may not be exercised, or "".
/ @private
.qetl.verify.not_exercisable:{[producer;mode]
    $[mode=`stream;
        $[`live~.qetl.job.stream.liveness producer; "its source is live - faults are injected on fixtures only";
          `unknown~.qetl.job.stream.liveness producer; "its poll names no source, so it cannot be told from a live one";
          ""];
      .qetl.source.has_credentials (.qetl.job.bounded.def producer)`source; "its source is live - faults are injected on fixtures only";
      ""]}

/ Private: a check body that threw, as an UNVERIFIED result.
/ @private
.qetl.verify.did_not_complete:{[e] `status`reason`evidence!(`UNVERIFIED;"the check did not complete: ",e;"")}

/ Private: run `body` on one argument in a sandbox, refusing outright where that is unsafe
/ and reporting a body that threw as UNVERIFIED rather than a verdict.
/ @private
.qetl.verify.sandboxed:{[body;arg]
    .qetl.verify.require_isolated[];
    .[.qetl.verify.in_sandbox;(body;enlist arg);.qetl.verify.did_not_complete]}

/ Private: is there no saved cursor? A timestamp cursor is null when unset; a
/ compound one (a feed's own load, e.g. load_cursor_value) is (::) - a dictionary
/ is never "null" as a whole, which `null` would answer field by field.
/ @private
.qetl.verify.no_cursor:{[c] $[(::)~c; 1b; 99h=type c; 0b; null c]}

/ A streaming producer's cursor progression: one tick publishes the page and moves the
/ cursor past it, and the next tick finds nothing new and publishes nothing again.
/ @param job the polling job
/ @return a dict status, reason, evidence
/ @eg .qetl.verify.check_cursor[`demo_deals_feed]`status
.qetl.verify.check_cursor:{[job]
    body:{[job]
        ops:.qetl.job.stream.cursor_ops job;
        name:.qetl.job.stream.cursor_name job;
        r:.qetl.verify.with_recorder[job;0b;{[j] a:.qetl.job.stream.tick j; b:.qetl.job.stream.tick j; (a;b;1b)}];
        if[(0h=type r) and `failed~first r; :.qetl.verify.did_not_complete last r];
        a:r 0; b:r 1;
        c:ops[`load] name;
        ev:"tick 1: ",string[a`state]," ",string[a`rows]," row(s); cursor ",(-3!c),"; tick 2: ",string[b`state]," ",string[b`rows]," row(s); recorder saw ",string sum .qetl.verify.sent`n;
        $[0=a`rows; `status`reason`evidence!(`UNVERIFIED;"empty sample - the first tick found nothing to publish";ev);
          not (a`state)~`published; `status`reason`evidence!(`FAIL;"the first tick did not publish";ev);
          .qetl.verify.no_cursor[c]; `status`reason`evidence!(`FAIL;"the cursor did not move after a publish - the page would be fetched again";ev);
          not (b`state)~`idle; `status`reason`evidence!(`FAIL;"the second tick did not find the page acknowledged";ev);
          (sum .qetl.verify.sent`n)<>a`rows; `status`reason`evidence!(`FAIL;"rows published differ from the page";ev);
          `status`reason`evidence!(`PASS;"";ev)]};
    why:.qetl.verify.not_exercisable[job;`stream];
    if[count why; :`status`reason`evidence!(`UNVERIFIED;why;"")];
    .qetl.verify.sandboxed[body;job]}


/ A streaming producer's recovery from a publish that fails: the cursor stays where it
/ was (never an over-claim), and the next tick publishes the page it had not delivered.
/ @param job the polling job
/ @return a dict status, reason, evidence
/ @eg .qetl.verify.check_publish_recovery[`demo_deals_feed]`status
.qetl.verify.check_publish_recovery:{[job]
    body:{[job]
        ops:.qetl.job.stream.cursor_ops job;
        name:.qetl.job.stream.cursor_name job;
        want:sum (.qetl.job.stream.preview[job;1])`rows;
        if[0=want; :`status`reason`evidence!(`UNVERIFIED;"empty sample - the feed has no page to publish";"")];
        r:.qetl.verify.with_recorder[job;1b;{[j] .[.qetl.job.stream.tick;enlist j;{[e] (`refused;e)}]}];
        refused:(0h=type r) and `refused~first r;
        held:ops[`load] name;
        r2:.qetl.verify.with_recorder[job;0b;{[j] .qetl.job.stream.tick j}];
        c2:ops[`load] name;
        ev:"publish refused: ",string[refused],"; cursor after the failure ",(-3!held),"; retry published ",string[sum .qetl.verify.sent`n]," of ",string[want]," row(s); cursor ",-3!c2;
        $[not refused; `status`reason`evidence!(`UNVERIFIED;"the injected publish failure did not surface from the tick";ev);
          not .qetl.verify.no_cursor[held]; `status`reason`evidence!(`FAIL;"the cursor moved although the publish failed - a re-run would skip the page";ev);
          (sum .qetl.verify.sent`n)<>want; `status`reason`evidence!(`FAIL;"the page was not delivered in full after the retry";ev);
          .qetl.verify.no_cursor[c2]; `status`reason`evidence!(`FAIL;"the cursor did not move after the page was delivered";ev);
          `status`reason`evidence!(`PASS;"";ev)]};
    why:.qetl.verify.not_exercisable[job;`stream];
    if[count why; :`status`reason`evidence!(`UNVERIFIED;why;"")];
    .qetl.verify.sandboxed[body;job]}

\d .
