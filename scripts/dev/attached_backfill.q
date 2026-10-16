// attached_backfill.q - run one bounded worker into an ATTACHED native stack's HDB (#1454).
//
//   q scripts/dev/attached_backfill.q -worker W -version V -from F -to T \
//       -hdb /path/to/native/hdb -destid <identity> -attachment <name> \
//       -max_date 2026.10.15 -deadline 2026.10.16D23:00 [-mode dry_run] [-on_conflict replace]
//
// Started by `uqs job backfill --attach NAME --mode run|dry-run` (uqs.attach.backfill_run), which has
// already resolved the destination from the saved attachment, admitted it against the live stack
// (uqs.attach.backfill_plan) and worked out the two bounds below. Everything it needs about the
// destination arrives here as a flag. NOTHING is read from $KDBHDB or defaulted: a flag that is missing
// is a refusal, because a backfill that guessed its destination would write uqf's own HDB or none.
//
//   -hdb         the destination HDB root; it must exist, and is never created
//   -destid      the destination's identity (host, stack id, canonical HDB). $UQF_STATUS_DIR must be the
//                directory made for it, so one destination's coverage, checkpoint and lock can never be
//                read as another's
//   -attachment  the attachment's name, for the log and the owner record
//   -max_date    the last date this run may write (the native writers' active date, less one)
//   -deadline    no write after this instant: where the end-of-day fence begins
//   -owner       the run owner record to write while running and remove on the way out
//
// The rest are torq_backfill.q's flags, parsed by the same functions (.qetl.job.bounded.spec_from_flags,
// .qetl.job.bounded.runtime.use_on_conflict, use_mode). -mode is dry_run or run; validate and plan are
// the launcher's. There is no -fixture: a fixture in a native HDB is indistinguishable from data.
//
// It is the bounded-worker lifecycle and the HDB writer, unchanged - init, run, finish_window's
// publish-then-coverage-then-checkpoint order, the staged swap and recovery - with the destination
// handed to the writer explicitly (.qetl.io.hdb_scoped) and coverage deferred until a reload says the
// readers see it (.qetl.coverage.defer_ready). It does not register with discovery and does not reload
// anything itself: the launcher knows the readers.
//
// While it runs it holds the run owner record named by -owner, which `uqs job attach detach` reads: an
// attachment with a live owner cannot be detached. It removes it on the way out; a run that dies leaves
// a record whose pid is gone, which reads as no owner.

if[not `worker in key opts:.Q.opt .z.x;
    -1 "attached_backfill: -worker is required";
    exit 2];

\l src/init.q
.qetl.load.only:`$first opts`worker
\l src/etl/init.q

\d .

/ The flags that name the destination, all required.
.qdev.attached.required:`hdb`destid`attachment`max_date`deadline`owner

/ Refuse a missing destination flag, and the flags this launcher does not take.
/ @param opts the parsed command line
/ @throws error naming every missing flag, or the forbidden ones
.qdev.attached.require_flags:{[opts]
    missing:.qdev.attached.required except key opts;
    if[count missing; '"attached_backfill: missing ",", " sv "-",/:string missing];
    if[`fixture in key opts; '"attached_backfill: -fixture is refused: a fixture in a native HDB is indistinguishable from data"];
    if[count bad:(`plan`validate) inter `$first opts`mode;
        '"attached_backfill: -mode ",string[first bad]," is the launcher's; this runs dry_run or run"];
    if[.qetl.source.fixture_writes_allowed[];
        '"attached_backfill: fixture writes are enabled (UQF_FIXTURE_WRITES): unset it - nothing here writes a fixture"]}

/ The state directory must be the one made for this destination.
/ @param destid the destination identity
/ @return the directory
/ @throws error when UQF_STATUS_DIR is unset or not this destination's
.qdev.attached.state_dir:{[destid]
    d:getenv `UQF_STATUS_DIR;
    if[0=count d; '"attached_backfill: UQF_STATUS_DIR is unset - coverage and checkpoints are filed per destination"];
    if[not (last "/" vs d) like "*",destid; '"attached_backfill: UQF_STATUS_DIR ",d," is not the directory for destination ",destid];
    d}

/ The fence handed to the writer.
/ @param opts the parsed command line
/ @return dict of max_date, deadline and destination
.qdev.attached.bounds:{[opts]
    `max_date`deadline`destination!("D"$first opts`max_date;"P"$first opts`deadline;`$first opts`destid)}

/ Record who is running into which destination.
/ @param opts the parsed command line
/ @return the owner record's path
.qdev.attached.claim:{[opts]
    path:first opts`owner;
    system "mkdir -p ",(neg 1+count last "/" vs path)_path;
    r:`pid`host`worker`attachment`destination`started!(.z.i;string .z.h;first opts`worker;first opts`attachment;first opts`destid;string .z.p);
    (hsym `$path) 0: enlist .j.j r;
    path}

/ Drop the owner record, whatever happened to the run.
/ @param opts the parsed command line
.qdev.attached.release:{[opts] @[hdel;hsym `$first opts`owner;{[e] (::)}]}

\d .

o:.Q.opt .z.x;
.qdev.attached.require_flags o;
state:.qdev.attached.state_dir first o`destid;
system "mkdir -p ",state;
worker:`$first o`worker;
s:@[.qetl.job.bounded.spec_from_flags;o;{[e] -1 "attached_backfill: ",e; exit 2}];
decl:.qetl.job.bounded.def worker;
@[{[o] .qetl.job.bounded.runtime.use_on_conflict["attached_backfill";o]; .qetl.job.bounded.runtime.use_mode["attached_backfill";o]};
    o;{[e] -1 e; exit 2}];
if[`verbose in key o; .qetl.log.debug 1b];
if[`trace in key o; .qetl.log.debug 1b; .qetl.log.trace 1b];

if[not .qetl.source.has_credentials decl`source;
    -1 "attached_backfill: ",string[decl`source]," has no credential configured: without one this would write its fixture into a native HDB";
    exit 2];
if[not (::)~decl`io;
    -1 "attached_backfill: ",string[worker]," declares its own IO manager, which would write where it likes";
    exit 2];

root:hsym `$first o`hdb;
col:(.qetl.source.def decl`source)`time_column;
bounds:@[.qdev.attached.bounds;o;{[e] -1 "attached_backfill: bad bounds: ",e; exit 2}];
mgr:.[.qetl.io.hdb_scoped;(root;col;bounds);{[e] -1 "attached_backfill: ",e; exit 2}];
.qetl.io.default:mgr;
/ written is not queryable: coverage waits for the launcher's reload (#1094)
.qetl.coverage.defer_ready:1b;
.qetl.log.info[worker;"writing into an attached stack's HDB";
    `attachment`destination`root`partition_col`max_date`deadline`state!(`$first o`attachment;bounds`destination;root;col;bounds`max_date;bounds`deadline;state)];

.qdev.attached.claim o;
if[.qetl.job.bounded.runtime.allows`record_run; .qetl.run.attach[]];
/ Three named parameters so {...}[ns;spec] is a PROJECTION .Q.trp can call with one argument.
result:.Q.trp[{[ns;spec;ignored] (` sv ns,`init)[spec]; (` sv ns,`run)[]}[decl`ns;s`spec];::;
    {[e;bt]
        .qetl.log.err[`attached_backfill;"run failed";enlist[`error]!enlist e];
        .qetl.log.err[`attached_backfill;"backtrace";enlist[`trace]!enlist .Q.sbt bt];
        `state`error!(`failed;e)}];
.qdev.attached.release o;
/ the launcher reads this line: written and queryable are different things
.qetl.log.info[worker;"attached backfill finished";result,`hdb_unreloaded`destination!(1b;bounds`destination)];
-1 "RESULT ",.j.j `worker`state`error`destination!(string worker;string result`state;$[`error in key result;$[10h=type e:result`error;e;string e];""];string bounds`destination);
exit .qetl.job.bounded.exit_code result`state;
