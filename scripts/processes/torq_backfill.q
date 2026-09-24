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
/   startwithall=0   it must not start with the stack. `uqs start` is
/                    for the streaming fleet; a backfill is a job an operator
/                    or Airflow triggers with a range (ETL-15).
/   exit at the end  the process leaves discovery when it finishes, so the
/                    fleet view shows running backfills and not a graveyard
/                    of completed ones.
/ .
/ WHICH WORKER, AND OVER WHAT RANGE, come from this process's command line,
/ so one script serves every worker. `uqs backfill` appends them to TorQ's
/ start line through torq.sh's own `-extras`:
/ .
/   -worker   the worker name, e.g. demo_deals_backfill
/   -version  the source_version to record coverage under
/   -from     inclusive lower bound, a q timestamp
/   -to       exclusive upper bound
/ .
/ All four are required and refused when absent. A backfill that defaulted a
/ range would publish the wrong window and record it as covered, which is the
/ failure coverage exists to make impossible.
/ .
/ FLAGS, NOT ENVIRONMENT VARIABLES. They used to be UQF_BACKFILL_WORKER and
/ friends, because torq.sh builds the start line from process.csv and the
/ environment was the one channel that reached the process. torq.sh's
/ `-extras` is the channel meant for this. An exported variable outlives the
/ run it was set for, so the next backfill in the same shell silently reused
/ the last range - the very default this script refuses to have.

\d .qproc.backfill

/ The flags this process reads. Listed so the refusal below can report every
/ missing one at once rather than over four restarts (ETL-16).
required_flags:`worker`version`from`to

/ Refuse unless every required flag has a value, naming all that do not.
/ .Q.opt keeps each flag's words as a list of strings, so the value is the
/ first of them. Missing is either not given at all, or given with nothing
/ after it - which .Q.opt maps to an empty list - so `-from` with no value is
/ refused here by name rather than failing later as a type error. Presence is
/ tested with `in key` rather than by indexing, because what a dictionary
/ returns for an absent key depends on its value list's prototype.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return flag -> its value, as a string
require_flags:{[opts]
    missing:required_flags where not (required_flags in key opts) and 0<count each opts required_flags;
    if[count missing;
        '"torq_backfill: missing ",(", " sv "-",/:string missing),
         " - a backfill with no range would publish the wrong window and record it as covered"];
    required_flags!first each opts required_flags}

/ The run specification, parsed and typed.
/ @param opts the parsed command line, as .Q.opt returns it
/ @throws error when a bound is not a q timestamp
spec_from_flags:{[opts]
    f:require_flags opts;
    from_ts:"P"$f`from;
    to_ts:"P"$f`to;
    if[null from_ts; '"torq_backfill: -from is not a timestamp: ",f`from];
    if[null to_ts;   '"torq_backfill: -to is not a timestamp: ",f`to];
    `worker`spec!(`$f`worker;
        `source_version`range_from`range_to!(`$f`version;from_ts;to_ts))}

/ Run the worker named on the command line, and report what it did.
/ .
/ Errors are caught and logged rather than thrown, so the process exits with
/ a status a caller can read instead of a q error trace. The exit CODE is
/ what Airflow reads (ETL-15 gives it retries), so it must distinguish a
/ failed run from a successful one.
/ @return the run's result dictionary
run:{[]
    s:spec_from_flags .Q.opt .z.x;
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
result:@[{.qproc.backfill.run[]};::;{[e] .qlog.err[`backfill;"backfill process failed";enlist[`error]!enlist e]; `state`error!(`failed;e)}];
exit $[`completed~result`state; 0; 1];
