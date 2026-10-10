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
/   -version  the source_version to record coverage under; optional when the
/             worker declares a default source_version
/   -from     inclusive lower bound, a q timestamp
/   -to       exclusive upper bound
/ .
/   -verbose  optional: switch the DEBUG level on for this process - the parsed
/             flags, the worker's declaration, the windows it will cut, every
/             window as it starts and publishes, and each stage's timing.
/             `uqs backfill --debug` passes it. Not TorQ's own -debug, which
/             also stops the log going to its file.
/   -trace    optional: switch the TRACE level on - every query the source is
/             sent, the SQL statement or the q lambda and its bounds, before
/             it goes and again with the rows and milliseconds it took - and
/             DEBUG with it, as -verbose does: a query is read beside the window
/             it was sent for. `uqs backfill --trace` passes it.
/   -on_conflict  optional: upsert, replace, ignore, append or fail - what a
/             write does with a row whose row_key is already there, for this
/             run only, over the worker's declared strategy.
/             `uqs backfill --on-conflict` passes it.
/   -mode     optional: validate, plan, dry_run or run (the default) - see
/             .qetl.job.bounded.runtime.modes. validate and plan stop before
/             any source is opened or anything written; dry_run fetches and
/             writes nothing. `uqs backfill --mode` passes it.
/   -fixture  optional: let a run with no credential write its source's
/             fixture (#1082) - refused otherwise, outside a dry run.
/             Its coverage is recorded under the release tagged ~fixture.
/             `uqs backfill --fixture` passes it.
/ .
/ -worker, -from and -to are required and refused when absent, and -version
/ unless the worker declares a default. A backfill that defaulted a
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

/ The flags this process reads are parsed by .qetl.job.bounded.spec_from_flags
/ in src/etl/core/bounded_worker.q - the one parser scripts/dev/run_backfill.q
/ uses too, so a command line means the same thing to both.

/ Whether this process was asked for DEBUG output.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return 1b when -verbose was given
verbose:{[opts] `verbose in key opts}

/ Whether this process was asked for TRACE output.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return 1b when -trace was given
trace:{[opts] `trace in key opts}

/ Apply -on_conflict, when given, as this run's strategy - set as the
/ on_conflict config override, which .qetl.job.bounded.on_conflict reads
/ ahead of the worker's declaration. Checked here, so a typo fails before
/ the first fetch rather than at the first write.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return the strategy, or ` when none was given
use_on_conflict:{[opts]
    if[not `on_conflict in key opts; :`];
    v:first opts`on_conflict;
    .qetl.io.require_strategy `$v;
    .qetl.cfg.set_override[`on_conflict;v];
    `$v}

/ Apply -mode, when given, as this run's mode - set as the mode config
/ override, which .qetl.job.bounded.runtime.mode reads. Resolved here, so a
/ typo or a contradiction with dry_run fails before anything is opened.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return the mode this run is in
use_mode:{[opts]
    if[`mode in key opts; .qetl.cfg.set_override[`mode;first opts`mode]];
    .qetl.job.bounded.runtime.mode[]}

/ Apply -fixture, when given, as the fixture_writes config override, which
/ .qetl.source.fixture_writes_allowed reads.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return 1b when this run may write a fixture
use_fixture:{[opts]
    if[`fixture in key opts; .qetl.cfg.set_override[`fixture_writes;"1"]];
    .qetl.source.fixture_writes_allowed[]}

/ Log a validate or plan report: one summary line, then the planned windows,
/ capped so a year of hourly windows does not bury the summary.
/ @param worker the worker's name
/ @param r what .qetl.job.bounded.validate or plan_only returned
/ @return the report
report:{[worker;r]
    .qetl.log.info[worker;string[r`state]," - nothing opened, nothing written";`plan _ r];
    if[not `plan in key r; :r];
    ws:r`plan;
    shown:50&count ws;
    {[worker;w] .qetl.log.info[worker;"would fetch";w]}[worker] each shown#ws;
    if[shown<count ws; .qetl.log.info[worker;"and more windows";enlist[`count]!enlist count[ws]-shown]];
    r}

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

/ ------------------------------------------------------------ HDB RELOAD
/ .
/ WHEN THE HDB IS TOLD TO RELOAD. It used to be once, after the run: a
/ thirty-day backfill showed nothing in the HDB until its last day was
/ written. Now the HDB writer finishes each day as the run moves past it
/ (.qetl.io.flush), and on_hdb_ready asks .qetl.io.due whether to reload now:
/ not while a partition is still being appended to, and mid-run not within
/ hdb_reload_seconds of the last reload. The run's final call always reloads
/ if anything finished since.

/ Seconds between mid-run reloads: hdb_reload_seconds (UQF_HDB_RELOAD_SECONDS),
/ else 30. 0 reloads at every day the run finishes. Unreadable is the
/ default, not an error - a typo here should not fail a backfill that has
/ rows to write.
/ @return the interval, as a timespan
reload_interval:{[]
    v:.qetl.cfg.raw `hdb_reload_seconds;
    n:$[0=count v; 30; null j:"J"$v; 30; j<0; 30; j];
    n*0D00:00:01}

/ What has been finished and not yet reloaded, and when the last reload was.
reload_state:`dirty`last!(0b;0Np)

/ The HDB manager's on_ready: reload when .qetl.io.due says so. What is
/ written stays owed until every registered HDB has reloaded it (#1084):
/ one that refused or failed is asked again at the next on_ready, and the
/ run's final call that still falls short says so - its rows are on disk
/ and covered, but a running HDB is serving the map from before them.
/ @param status `finished`pending`final from .qetl.io.flush or .qetl.io.finish
/ @return 1b when it asked the HDB to reload
on_hdb_ready:{[status]
    d:.qetl.io.due[.qproc.backfill.reload_state;status;.z.p;reload_interval[]];
    s:d`state;
    if[d`act;
        / taken BEFORE the reload is asked: what it maps is what was on disk then
        asked:.z.p;
        r:.qtorq.reload_hdb[];
        s:.qetl.io.acknowledged[s;r];
        / every running HDB has it, with nothing open: queryable now (#1094)
        if[not s`dirty; .qetl.coverage.release_ready asked];
        if[s[`dirty] and status`final;
            .qetl.log.err[`backfill;"backfill finished with rows a running hdb has not reloaded - they are on disk and covered, and appear there when it reloads or restarts";r]]];
    `.qproc.backfill.reload_state set s;
    d`act}

/ Point every worker that declares no io of its own at the HDB, partitioned
/ by its source's time column. Here rather than in the worker, because where
/ rows belong is a fact about the stack, not about the worker - the same
/ split as a streaming job's publish, which torq_stream.q wires.
/ @param decl the worker's declaration
/ @return the manager now in .qetl.io.default
use_hdb:{[decl]
    root:hdb_root[];
    col:(.qetl.source.def decl`source)`time_column;
    .qetl.io.default:.qetl.io.hdb[root;col],enlist[`on_ready]!enlist on_hdb_ready;
    / coverage here means written; queryable once the HDBs reload (#1094)
    .qetl.coverage.defer_ready:1b;
    .qetl.log.info[`backfill;"writing into the HDB";`root`partition_col!(root;col)];
    .qetl.io.default}

/ A run on a key whose coverage its HDBs may not show yet - an earlier run
/ ended before they reloaded - owes them a reload even if it writes nothing,
/ or an idle re-run would leave those windows unqueryable for good (#1094).
/ @param worker the worker being run
/ @return 1b when this run took the debt on
catch_up:{[worker]
    if[not .qetl.job.bounded.runtime.allows`finish_store; :0b];
    cfg:.qetl.job.bounded.def worker;
    v:(.qetl.job.bounded.spec worker)`source_version;
    if[not .qetl.coverage.outstanding[cfg`dataset;cfg`partition;v]; :0b];
    `.qetl.coverage.unready set distinct .qetl.coverage.unready,([] dataset:enlist cfg`dataset; partition:enlist cfg`partition; source_version:enlist v);
    @[`.qproc.backfill.reload_state;`dirty;:;1b];
    .qetl.log.info[worker;"coverage the hdb has not loaded yet - this run reloads it";`dataset`source_version!(cfg`dataset;v)];
    1b}

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
    s:.qetl.job.bounded.spec_from_flags .Q.opt .z.x;
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
    oc:use_on_conflict .Q.opt .z.x;
    if[not null oc; .qetl.log.info[worker;"on_conflict for this run";enlist[`on_conflict]!enlist oc]];
    md:use_mode .Q.opt .z.x;
    .qetl.log.info[worker;"mode";enlist[`mode]!enlist md];
    if[use_fixture .Q.opt .z.x; .qetl.log.info[worker;"fixture writes allowed for this run";enlist[`fixture_writes]!enlist 1b]];
    / validate and plan stop here: no HDB, no ledger, no lock, no source.
    if[md~`validate; :report[worker;.qetl.job.bounded.validate[worker;spec]]];
    if[md~`plan; :report[worker;.qetl.job.bounded.plan_only[worker;spec]]];
    use_hdb decl;
    / The run ledger, attached HERE and unprotected: the worker's own
    / begin_run tolerates any failure, so a ledger it cannot write - one from
    / before etl_runs gained its range and counts columns - would leave this
    / run untracked without a word. Failing the backfill names the fix
    / (`uqs run migrate`) instead.
    / Not on a dry run, which records no run - attaching can create the ledger.
    if[.qetl.job.bounded.runtime.allows`record_run; .qetl.run.attach[]];
    t1:.z.p;
    (` sv ns,`init)[spec];
    catch_up worker;
    .qetl.log.dbg[worker;"init done";enlist[`ms]!enlist .qtorq.elapsed_ms t1];
    t2:.z.p;
    r:(` sv ns,`run)[];
    / No reload here: the run's finish step called on_hdb_ready with final
    / set, which reloads if anything finished since the last one - and what
    / it could not get a running HDB to reload is reported beside the run,
    / apart from whether the rows were written (#1084).
    / An idle run never finishes its store, so what it took on in catch_up
    / is reloaded here; a run that wrote has already released or failed to.
    if[count .qetl.coverage.unready; on_hdb_ready `finished`pending`final!(0;0;1b)];
    r:r,enlist[`hdb_unreloaded]!enlist .qproc.backfill.reload_state`dirty;
    .qetl.log.info[worker;"backfill process finished";
        r,`run_ms`total_ms!(.qtorq.elapsed_ms t2;.qtorq.elapsed_ms t0)];
    r}

\d .

/ Load uqf's own tree. Same shape as the ETL pipelines: init.q's \l lines are
/ repo-root-relative and torq.sh does not launch us from the repo root, so cd
/ there and back. system"cd" is q's builtin chdir, not a subshell, so it
/ sticks across the two calls.
{[uqfroot]
  t0:.z.p;
  cwd:first system"pwd";
  if[0=count uqfroot; '"torq_backfill: UQF_ROOT is unset - cannot find the uqf tree to load"];
  -1 string[.z.p]," | torq_backfill: loading uqf tree from ",uqfroot;
  system"cd ",uqfroot;
  system"l src/init.q";
  / Only -worker's declarations and what they reach (#902): a worker never
  / loads, let alone backfills, another.
  if[`worker in key o:.Q.opt .z.x; .qetl.load.only:`$o`worker];
  system"l src/etl/init.q";
  system"cd ",cwd;
  -1 string[.z.p]," | torq_backfill: uqf tree loaded in ",string[`long$(.z.p-t0)%1000000],"ms";
 }[getenv[`UQF_ROOT]];

/ What each external source connects to, from TorQ's config layers (#718).
/ Before any worker runs, so a malformed sources.csv stops the process here.
.qtorq.load_source_settings[];

/ DEBUG before anything else logs, so -verbose covers discovery too. .qetl.log is
/ only defined once the tree above has loaded.
if[.qproc.backfill.verbose .Q.opt .z.x; .qetl.log.debug 1b];
/ -trace is the most detail there is, so it includes DEBUG: a traced query is
/ read beside the window it was sent for, which only DEBUG logs. The two stay
/ separate switches in .qetl.log - only this flag ties them.
if[.qproc.backfill.trace .Q.opt .z.x; .qetl.log.debug 1b; .qetl.log.trace 1b];
.qetl.log.dbg[`backfill;"debug logging on";
    `procname`pid`port`cwd!(.proc.procname;.z.i;system"p";first system"pwd")];

/ Register with discovery before doing any work, so the fleet can see the
/ backfill WHILE it runs rather than only after it finishes. .servers.startup
/ opens and registers the handle using this process's own accesslist
/ credentials, exactly as cross1 does.
/ The identity this process connects to the fleet WITH. Its proctype,
/ backfill, has no password file in TorQ or the starter pack, so TorQ fell
/ back to default.txt - whose user is on no access list - and every
/ connection to an HDB was refused: the rows landed on disk and no HDB
/ reloaded them. The ETL processes' file (metrics.txt) carries the identity
/ the access list already accepts for a process reading the fleet. A
/ deployment that gives backfill, or this procname, a file of its own keeps
/ it. Set before discovery, which is the first connection.
{[]
    specific:(raze {.proc.getconfig["passwords/",(string x),".txt";2]} each .proc.proctype,.proc.procname) except `;
    fallback:hsym `$getenv[`KDBAPPCONFIG],"/passwords/metrics.txt";
    c:.qtorq.credential_from[specific;fallback];
    if[`adopted~c`source; .servers.USERPASS:c`userpass];
    $[`missing~c`source;
        .qetl.log.warn[`backfill;"no outbound credential for this process type, and none to adopt - connections to the fleet will use TorQ's default and be refused";
            enlist[`looked_for]!enlist 1_string fallback];
        .qetl.log.info[`backfill;"outbound credential";
            `source`file!(c`source;$[`adopted~c`source; 1_string fallback; "this process type's own"])]];
 }[];

.qetl.log.dbg[`backfill;"registering with discovery";()!()];
{[t0]
  .servers.startup[];
  .qetl.log.dbg[`backfill;"registered with discovery";
      `ms`servers!(.qtorq.elapsed_ms t0;count .servers.SERVERS)];
 }[.z.p];

/ Run, then leave. Which terminal states count as success is
/ .qetl.job.bounded.exit_code's to say, not this file's - the states are the
/ framework's vocabulary, and holding the mapping here is how `idle` came to
/ exit 1 while run's own comment called it a success.
/ .Q.trp rather than @[], so a failure carries WHERE it happened: the
/ backtrace is logged with the error, which is the one thing a bare message
/ like 'type cannot tell you after the process has gone.
result:.Q.trp[{.qproc.backfill.run[]};::;{[e;bt]
    .qetl.log.err[`backfill;"backfill process failed";enlist[`error]!enlist e];
    .qetl.log.err[`backfill;"backtrace";enlist[`trace]!enlist .Q.sbt bt];
    `state`error!(`failed;e)}];
code:.qetl.job.bounded.exit_code result`state;
/ A non-zero exit is a run that did not do its job, so it is an ERROR line - at
/ INFO, `state=failed` read as routine in a log filtered for problems.
$[0=code; .qetl.log.info; .qetl.log.err][`backfill;"exiting";`state`code!(result`state;code)];
exit code;
