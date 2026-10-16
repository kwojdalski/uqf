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
//   -readers     name@host:port,... - the HDB readers that serve the destination (verified by the
//                launcher), the only processes told to reload; _none_ when there are none
//   -user        the user the readers are reached as; its password is $UQF_ATTACH_REACH_PASSWORD
//
// The rest are torq_backfill.q's flags, parsed by the same functions (.qetl.job.bounded.spec_from_flags,
// .qetl.job.bounded.runtime.use_on_conflict, use_mode). -mode is dry_run or run; validate and plan are
// the launcher's. There is no -fixture: a fixture in a native HDB is indistinguishable from data.
//
// It is the bounded-worker lifecycle and the HDB writer, unchanged - init, run, finish_window's
// publish-then-coverage-then-checkpoint order, the staged swap and recovery - with the destination
// handed to the writer explicitly (.qetl.io.hdb_scoped) and coverage deferred until every verified
// reader has acknowledged a reload (.qetl.coverage.defer_ready, release_ready). It does not register
// with discovery: the launcher names the readers, and ONLY those are asked to reload.
//
// WRITTEN IS NOT QUERYABLE, and the outcome says which it is. A window's rows are on disk and covered
// when the write commits; a reader serves them once it reloads. If a reader does not (down, refused,
// slow), the rows stay written and covered, the readiness watermark is not advanced, the run exits 3
// and its RESULT line says outcome=written_not_queryable with the readers that failed. Running the
// same command again rewrites nothing - every window is covered - and asks the readers again.
//
//   outcome   exit  meaning
//   queryable    0  written, covered, and every reader reloaded (or there is none to ask)
//   dry_run      0  nothing written
//   written_not_queryable  3  written and covered; some reader has not reloaded: re-run to retry
//   partial      1  some windows failed; those are neither written nor covered
//   failed       1  the run did not do its job
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
.qdev.attached.required:`hdb`destid`attachment`max_date`deadline`owner`readers`user

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

/ ------------------------------------------------------------ READERS
/ .
/ Reached with the credentials the launcher passes, never the runtime's own: an attached stack is
/ somebody else's, and its HDB readers are the verified ones for this destination.

/ The readers named on the command line.
/ @param opts the parsed command line
/ @return a list of name/host/port dictionaries, empty for none
.qdev.attached.readers:{[opts]
    v:first opts`readers;
    if[v~"_none_"; :()];
    {[s] p:"@" vs s; hp:":" vs p 1; `name`host`port!(`$p 0;`$hp 0;"J"$hp 1)} each "," vs v}

/ Ask one reader to reload the way a native wdb does at end of day: `(`reload; date)`, which a TorQ
/ HDB answers by re-mapping its database - and then ask it whether it SERVES the dataset. A reload
/ that succeeds is not queryable: a partitioned HDB takes its table list from its LATEST partition,
/ so a table written only into an older date is invisible to it until the latest partition holds the
/ table too (a native end of day writes it; `uqs data hdb-check --fix` fills it).
/ @param user the user to connect as
/ @param pass its password
/ @param dataset the table the run wrote
/ @param r the reader: name, host, port
/ @return (1b; "") when it reloaded and serves the table, else (0b; why)
.qdev.attached.reload_one:{[user;pass;dataset;r]
    addr:`$":",string[r`host],":",string[r`port],":",user,":",pass;
    h:@[hopen;(addr;10000);{[e] e}];
    if[10h=type h; :(0b;"cannot connect: ",h)];
    res:.[{[h;ds] h(`reload;.z.d);
            $[h({[t] t in tables[]};ds); (1b;"");
              (0b;"reloaded, but its HDB does not define ",string[ds]," yet: an HDB takes its tables from its latest partition, which does not hold it - it appears after the stack's next end of day writes the table, or once the latest partition is filled (uqs data hdb-check --fix)")]};
          (h;dataset);{[e] (0b;"reload failed: ",e)}];
    @[hclose;h;{[e] (::)}];
    res}

/ Ask every named reader, and say how it went.
/ @return dict: registered, opened (reached), reloaded, and failed as "name: why" strings
.qdev.attached.reload_all:{[]
    rs:.qdev.attached.readers_named;
    pass:getenv `UQF_ATTACH_REACH_PASSWORD;
    out:.qdev.attached.reload_one[.qdev.attached.reach_user;pass;.qdev.attached.dataset] each rs;
    ok:first each out;
    failed:{[r;o] (string r`name),": ",o 1}'[rs where not ok;out where not ok];
    reached:{not (x 1) like "cannot connect*"} each out;
    `registered`opened`reloaded`failed!(count rs;sum "j"$reached;sum "j"$ok;failed)}

/ What has been finished and not yet reloaded, and when the last reload was.
.qdev.attached.reload_state:`dirty`last!(0b;0Np)

/ The table the run writes: what a reader must serve for the rows to be queryable.
.qdev.attached.dataset:`

/ The readers that failed the last reload, for the closing line.
.qdev.attached.last_failed:()

/ Seconds between mid-run reloads: hdb_reload_seconds, else 30 (0 reloads at every finished day).
.qdev.attached.reload_interval:{[]
    v:.qetl.cfg.raw `hdb_reload_seconds;
    n:$[0=count v; 30; null j:"J"$v; 30; j<0; 30; j];
    n*0D00:00:01}

/ The writer's on_ready: reload the named readers when .qetl.io.due says so, and release coverage to the
/ readers only when every one acknowledged with nothing open. A reader that failed is asked again at the
/ next on_ready; the run's final call that still falls short says so - the rows are written and covered.
/ @param status `finished`pending`final from .qetl.io.flush or finish
/ @return 1b when it asked
.qdev.attached.on_ready:{[status]
    d:.qetl.io.due[.qdev.attached.reload_state;status;.z.p;.qdev.attached.reload_interval[]];
    s:d`state;
    if[d`act;
        / taken BEFORE the reload is asked: what it maps is what was on disk then
        asked:.z.p;
        r:.qdev.attached.reload_all[];
        s:.qetl.io.acknowledged[s;r];
        `.qdev.attached.last_failed set r`failed;
        if[not s`dirty; .qetl.coverage.release_ready asked];
        if[s[`dirty] and status`final;
            .qetl.log.err[`attached_backfill;"rows are written and covered but NOT queryable: a reader has not reloaded - re-run the same command to ask again, nothing will be rewritten";
                `readers`failed!(`$", " sv string .qdev.attached.readers_named`name;", " sv r`failed)]]];
    `.qdev.attached.reload_state set s;
    d`act}

/ A run on a key whose coverage its readers may not show yet - an earlier run ended before they
/ reloaded - owes them a reload even if it writes nothing, or an idle re-run would leave those windows
/ unqueryable for good (#1094).
/ @param worker the worker being run
/ @return 1b when this run took the debt on
.qdev.attached.catch_up:{[worker]
    if[not .qetl.job.bounded.runtime.allows`finish_store; :0b];
    cfg:.qetl.job.bounded.def worker;
    v:(.qetl.job.bounded.spec worker)`source_version;
    if[not .qetl.coverage.outstanding[cfg`dataset;cfg`partition;v]; :0b];
    `.qetl.coverage.unready set distinct .qetl.coverage.unready,([] dataset:enlist cfg`dataset; partition:enlist cfg`partition; source_version:enlist v);
    @[`.qdev.attached.reload_state;`dirty;:;1b];
    .qetl.log.info[worker;"coverage the readers have not loaded yet - this run asks them to reload";`dataset`source_version!(cfg`dataset;v)];
    1b}

/ The outcome of a run, and the exit code that says it.
/ @param result the lifecycle's result dictionary
/ @param dry whether the run wrote nothing by mode
/ @return dict: outcome, queryable, code
.qdev.attached.outcome:{[result;dry]
    code:.qetl.job.bounded.exit_code result`state;
    owed:.qdev.attached.reload_state`dirty;
    o:$[not 0=code; $[`partial~result`state; `partial; `failed];
        dry; `dry_run;
        owed; `written_not_queryable;
        `queryable];
    `outcome`queryable`code!(o;o~`queryable;$[o~`written_not_queryable; 3; code])}

\d .

o:.Q.opt .z.x;
/ A refusal is one line and exit 2 (the launchers' convention), not a q trace.
@[.qdev.attached.require_flags;o;{[e] -1 e; exit 2}];
state:@[.qdev.attached.state_dir;first o`destid;{[e] -1 e; exit 2}];
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
/ written is not queryable: coverage is released to the readers only once each has reloaded (#1094)
.qdev.attached.readers_named:.qdev.attached.readers o;
.qdev.attached.dataset:decl`dataset;
.qdev.attached.reach_user:first o`user;
.qetl.io.default:mgr,enlist[`on_ready]!enlist .qdev.attached.on_ready;
.qetl.coverage.defer_ready:1b;
.qetl.log.info[worker;"writing into an attached stack's HDB";
    `attachment`destination`root`partition_col`max_date`deadline`state`readers!(`$first o`attachment;bounds`destination;root;col;bounds`max_date;bounds`deadline;state;`$", " sv string .qdev.attached.readers_named`name)];

.qdev.attached.claim o;
if[.qetl.job.bounded.runtime.allows`record_run; .qetl.run.attach[]];
/ Three named parameters so {...}[ns;spec] is a PROJECTION .Q.trp can call with one argument. The
/ catch-up sits between init and run: it is what lets an idle re-run ask the readers again.
result:.Q.trp[{[ns;spec;w;ignored] (` sv ns,`init)[spec]; .qdev.attached.catch_up w; (` sv ns,`run)[]}[decl`ns;s`spec;worker];::;
    {[e;bt]
        .qetl.log.err[`attached_backfill;"run failed";enlist[`error]!enlist e];
        .qetl.log.err[`attached_backfill;"backtrace";enlist[`trace]!enlist .Q.sbt bt];
        `state`error!(`failed;e)}];
/ An idle run never finishes its store, so what catch_up took on is asked here; a run that wrote has
/ already asked and released, or failed to.
if[count .qetl.coverage.unready; .qdev.attached.on_ready `finished`pending`final!(0;0;1b)];
.qdev.attached.release o;
fin:.qdev.attached.outcome[result;not .qetl.job.bounded.runtime.allows`finish_store];
.qetl.log.info[worker;"attached backfill finished";result,fin,`destination`reload_owed!(bounds`destination;.qdev.attached.reload_state`dirty)];
/ the launcher reads this line: written and queryable are different things
-1 "RESULT ",.j.j `worker`state`outcome`queryable`error`destination`readers`failed_readers!(string worker;string result`state;string fin`outcome;fin`queryable;$[`error in key result;$[10h=type e:result`error;e;string e];""];string bounds`destination;string .qdev.attached.readers_named`name;.qdev.attached.last_failed);
exit fin`code;
