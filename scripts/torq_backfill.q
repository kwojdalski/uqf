/ torq_backfill.q - run a bounded backfill worker as a discoverable TorQ
/ process.
/ .
/ WHAT THIS FIXES. Backfill workers were spawned ad hoc - `system "q ..."`
/ from a test harness - so a running backfill had no presence in
/ `.servers.SERVERS`. Nothing could find it. That included .qwrt.connected,
/ which reads exactly that table to decide whether a worker's declared
/ dependencies are up: a backfill could not be a dependency of anything,
/ and an operator had no way to ask the fleet what was running.
/ .
/ Started through torq.sh with proctype `backfill`, TorQ's own startup
/ registers the process with discovery before this script's last line runs.
/ The registration is not code here - it is a consequence of being a declared
/ process, which is why the fix is a Pipeline entry plus this runner rather
/ than a registration call someone has to remember.
/ .
/ BOUNDED, NOT LONG-RUNNING. A backfill runs a window range and exits, which
/ is the opposite of every other process in the registry. Two consequences
/ are deliberate:
/ .
/   startwithall=0   it must not start with the stack. `uqf-stack start` is
/                    for the streaming fleet; a backfill is a job an operator
/                    or Airflow triggers with a range (ETL-15).
/   exit at the end  the process leaves discovery when it finishes, so the
/                    fleet view shows running backfills and not a graveyard
/                    of completed ones.
/ .
/ WHICH WORKER, AND OVER WHAT RANGE, come from the environment rather than
/ from this file, so one script serves every worker:
/ .
/   UQF_BACKFILL_WORKER   the worker name, e.g. demo_deals_backfill
/   UQF_BACKFILL_VERSION  the source_version to record coverage under
/   UQF_BACKFILL_FROM     inclusive lower bound, a q timestamp
/   UQF_BACKFILL_TO       exclusive upper bound
/ .
/ All four are required and refused when absent. A backfill that defaulted a
/ range would publish the wrong window and record it as covered, which is the
/ failure coverage exists to make impossible.

\d .qproc.backfill

/ The environment names this process reads. Listed so the refusal below can
/ report every missing one at once rather than over four restarts (ETL-16).
required_env:`UQF_BACKFILL_WORKER`UQF_BACKFILL_VERSION`UQF_BACKFILL_FROM`UQF_BACKFILL_TO

/ Private: read one required variable, or record it as missing.
missing:()

/ Private: the value, or "" with the name recorded.
value_of:{[nm]
    v:getenv nm;
    if[0=count v; missing,:nm];
    v}

/ Refuse unless every required variable is set, naming all of them.
require_env:{[]
    missing::();
    vals:value_of each required_env;
    if[count missing;
        '"torq_backfill: missing ",(", " sv string missing),
         " - a backfill with no range would publish the wrong window and record it as covered"];
    required_env!vals}

/ The run specification, parsed and typed.
/ @throws error when a bound is not a q timestamp
spec_from_env:{[]
    e:require_env[];
    from_ts:"P"$e`UQF_BACKFILL_FROM;
    to_ts:"P"$e`UQF_BACKFILL_TO;
    if[null from_ts; '"torq_backfill: UQF_BACKFILL_FROM is not a timestamp: ",e`UQF_BACKFILL_FROM];
    if[null to_ts;   '"torq_backfill: UQF_BACKFILL_TO is not a timestamp: ",e`UQF_BACKFILL_TO];
    `worker`spec!(`$e`UQF_BACKFILL_WORKER;
        `source_version`range_from`range_to!(`$e`UQF_BACKFILL_VERSION;from_ts;to_ts))}

/ Run the worker named by the environment, and report what it did.
/ .
/ Errors are caught and logged rather than thrown, so the process exits with
/ a status a caller can read instead of a q error trace. The exit CODE is
/ what Airflow reads (ETL-15 gives it retries), so it must distinguish a
/ failed run from a successful one.
/ @return the run's result dictionary
run:{[]
    s:spec_from_env[];
    worker:s`worker;
    .qlog.info[worker;"backfill process starting";s`spec];
    ns:(.qbw.declaration worker)`ns;
    (` sv ns,`init)[s`spec];
    r:(` sv ns,`run)[];
    .qlog.info[worker;"backfill process finished";r];
    r}

\d .

/ Load uqf's own tree. Same shape as the ETL pipelines: init.q's \l lines are
/ repo-root-relative and torq.sh does not launch us from the repo root, so cd
/ there and back. system"cd" is q's builtin chdir, not a subshell, so it
/ sticks across the two calls.
{[uqfroot]
  cwd:first system"pwd";
  system"cd ",uqfroot;
  system"l src/init.q";
  system"l src/etl/init.q";
  system"cd ",cwd;
 }[getenv[`UQFROOT]];

/ Register with discovery before doing any work, so the fleet can see the
/ backfill WHILE it runs rather than only after it finishes. .servers.startup
/ opens and registers the handle using this process's own accesslist
/ credentials, exactly as cross1 does.
.servers.startup[];

/ Run, then leave. exit 0 on a completed run, 1 otherwise - Airflow reads the
/ code, and `partial` is not success: some windows failed and a retry should
/ pick them up, which it can because coverage never claimed them.
result:@[{.qproc.backfill.run[]};::;{[e] .lg.e[`backfill;"backfill process failed: ",e]; `state`error!(`failed;e)}];
exit $[`completed~result`state; 0; 1];
