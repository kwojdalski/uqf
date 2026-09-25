/ torq_backfill.q - run a bounded backfill worker as a discoverable TorQ
/ process.
/ .
/ WHAT THIS FIXES. Backfill workers were spawned ad hoc - `system "q ..."`
/ from a test harness - so a running backfill had no presence in
/ `.servers.SERVERS`. Nothing could find it. That included .qetl.job.bounded.runtime.connected,
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
/                    or Airflow triggers with a range.
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
/   -verbose  optional: switch the DBG level on for this process - the parsed
/             flags, the worker's declaration, the windows it will cut, every
/             window as it starts and publishes, and each stage's timing.
/             `uqs backfill --debug` passes it. Not TorQ's own -debug, which
/             also stops the log going to its file.
/ .
/ The first four are required and refused when absent. A backfill that defaulted a
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
/ missing one at once rather than over four restarts.
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

/ Milliseconds since `t0`, for the timing fields every stage logs.
/ @param t0 a timestamp, as .z.p returned it
/ @return elapsed milliseconds as a long
elapsed_ms:{[t0] `long$(.z.p-t0)%1000000}

/ Whether this process was asked for DBG output.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return 1b when -verbose was given
verbose:{[opts] `verbose in key opts}

/ How many windows [range_from;range_to) cuts into at the worker's width, for
/ the log only - so a reader can see progress against a total. Null when the
/ width is not a timespan rather than failing the run over a log line.
/ @param spec the run specification
/ @param width the worker's declared window width
/ @return the window count, or 0N
window_count:{[spec;width]
    .[{[spec;width] `long$ceiling (spec[`range_to]-spec`range_from)%width};
      (spec;width);
      {[e] 0N}]}

/ The HDB this backfill writes into: $KDBHDB, which uqs sets for every
/ process it starts (python/uqs/src/uqs/stack/env.py). Refused when unset
/ rather than falling back to memory: a backfill whose rows live only in
/ its own process loses them at exit while coverage records the range as
/ done, so the next run is idle and the rows never arrive anywhere.
/ @return the HDB root as a file symbol
/ @throws error when KDBHDB is unset
hdb_root:{[]
    d:getenv `KDBHDB;
    if[0=count d; '"torq_backfill: KDBHDB is unset - a backfill writes into the HDB, and cannot tell where it is"];
    hsym `$d}

/ Point every worker that declares no io of its own at the HDB, partitioned
/ by its source's time column. Here rather than in the worker, because where
/ rows belong is a fact about the stack, not about the worker - the same
/ split as a streaming job's publish, which torq_stream.q wires.
/ @param decl the worker's declaration
/ @return the manager now in .qetl.io.default
use_hdb:{[decl]
    root:hdb_root[];
    col:(.qetl.source.def decl`source)`time_column;
    .qetl.io.default:.qetl.io.hdb[root;col];
    .qetl.log.info[`backfill;"writing into the HDB";`root`partition_col!(root;col)];
    .qetl.io.default}

/ Run the worker named on the command line, and report what it did.
/ .
/ Errors are caught and logged rather than thrown, so the process exits with
/ a status a caller can read instead of a q error trace. The exit CODE is
/ what Airflow reads (Airflow owns retries), so it must distinguish a
/ failed run from a successful one.
/ @return the run's result dictionary
run:{[]
    t0:.z.p;
    .qetl.log.dbg[`backfill;"command line";enlist[`args]!enlist .z.x];
    s:spec_from_flags .Q.opt .z.x;
    worker:s`worker;
    spec:s`spec;
    .qetl.log.info[worker;"backfill process starting";spec];
    decl:.qetl.job.bounded.def worker;
    ns:decl`ns;
    .qetl.log.dbg[worker;"declaration";
        `ns`source`dataset`width`partition!
            (ns;decl`source;decl`dataset;decl`width;.qetl.job.bounded.partition_of worker)];
    .qetl.log.info[worker;"range";
        `range_from`range_to`span`width`windows!
            (spec`range_from;spec`range_to;spec[`range_to]-spec`range_from;
             decl`width;window_count[spec;decl`width])];
    use_hdb decl;
    t1:.z.p;
    (` sv ns,`init)[spec];
    .qetl.log.dbg[worker;"init done";enlist[`ms]!enlist elapsed_ms t1];
    t2:.z.p;
    r:(` sv ns,`run)[];
    .qetl.log.info[worker;"backfill process finished";
        r,`run_ms`total_ms!(elapsed_ms t2;elapsed_ms t0)];
    / The partitions are sorted and filled by the run's own finish step; a
    / running HDB still maps the old set until it is told to reload.
    if[0<r`rows_published; .qtorq.reload_hdb[]];
    r}

\d .

/ Load uqf's own tree. Same shape as the ETL pipelines: init.q's \l lines are
/ repo-root-relative and torq.sh does not launch us from the repo root, so cd
/ there and back. system"cd" is q's builtin chdir, not a subshell, so it
/ sticks across the two calls.
{[uqfroot]
  t0:.z.p;
  cwd:first system"pwd";
  if[0=count uqfroot; '"torq_backfill: UQFROOT is unset - cannot find the uqf tree to load"];
  -1 string[.z.p]," | torq_backfill: loading uqf tree from ",uqfroot;
  system"cd ",uqfroot;
  system"l src/init.q";
  system"l src/etl/init.q";
  system"cd ",cwd;
  -1 string[.z.p]," | torq_backfill: uqf tree loaded in ",string[`long$(.z.p-t0)%1000000],"ms";
 }[getenv[`UQFROOT]];

/ DBG before anything else logs, so -verbose covers discovery too. .qetl.log is
/ only defined once the tree above has loaded.
if[.qproc.backfill.verbose .Q.opt .z.x; .qetl.log.debug 1b];
.qetl.log.dbg[`backfill;"debug logging on";
    `procname`pid`port`cwd!(.proc.procname;.z.i;system"p";first system"pwd")];

/ Register with discovery before doing any work, so the fleet can see the
/ backfill WHILE it runs rather than only after it finishes. .servers.startup
/ opens and registers the handle using this process's own accesslist
/ credentials, exactly as cross1 does.
.qetl.log.dbg[`backfill;"registering with discovery";()!()];
{[t0]
  .servers.startup[];
  .qetl.log.dbg[`backfill;"registered with discovery";
      `ms`servers!(.qproc.backfill.elapsed_ms t0;count .servers.SERVERS)];
 }[.z.p];

/ Run, then leave. exit 0 on a completed run, 1 otherwise - Airflow reads the
/ code, and `partial` is not success: some windows failed and a retry should
/ pick them up, which it can because coverage never claimed them.
/ .Q.trp rather than @[], so a failure carries WHERE it happened: the
/ backtrace is logged with the error, which is the one thing a bare message
/ like 'type cannot tell you after the process has gone.
result:.Q.trp[{.qproc.backfill.run[]};::;{[e;bt]
    .qetl.log.err[`backfill;"backfill process failed";enlist[`error]!enlist e];
    .qetl.log.err[`backfill;"backtrace";enlist[`trace]!enlist .Q.sbt bt];
    `state`error!(`failed;e)}];
code:$[`completed~result`state; 0; 1];
.qetl.log.info[`backfill;"exiting";`state`code!(result`state;code)];
exit code;
