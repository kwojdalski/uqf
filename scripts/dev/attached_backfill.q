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
// Or, when the HDB is on another machine (#1650), in place of -hdb, -max_date, -deadline, -readers
// and -user:
//
//   -writer      host:port of the destination's receiver (scripts/dev/remote_receiver.q), which
//                admits each batch, commits it through its own fenced HDB writer and reloads the
//                readers; this process extracts, files coverage here and writes no HDB
//   -writer_user the user the receiver is reached as; its password is $UQF_ATTACH_WRITER_PASSWORD
//
// With -force as well (`uqs job backfill --writer HOST:PORT --force`), -writer is ANY q process of the
// stack - its wdb - running none of uqf's code: each date's rows are sent as a lambda that process
// evaluates, appending them into its own HDB (`.wdb.hdbdir`, or -force_hdb when it is not _own_).
// NOTHING is checked there: no receiver, contract, schema, date fence or batch journal, so a retry or
// a re-run appends twice, and no reader is told to reload. Append only. Coverage is recorded here as
// each window is acknowledged.
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
//   written_forced  0  -force: appended into the writer's HDB unchecked; no reader was told to reload
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

/ The flags every run takes; then the ones that name a destination this process writes itself,
/ or, with -writer, the receiver on the destination's machine that writes it (#1650).
.qdev.attached.local_flags:`hdb`destid`attachment`max_date`deadline`owner`readers`user
.qdev.attached.remote_flags:`destid`attachment`owner`writer`writer_user

/ Refuse a missing destination flag, and the flags this launcher does not take.
/ @param opts the parsed command line
/ @throws error naming every missing flag, or the forbidden ones
.qdev.attached.require_flags:{[opts]
    missing:$[`writer in key opts; .qdev.attached.remote_flags; .qdev.attached.local_flags] except key opts;
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
/ Naming the readers, asking each to reload, and the reload state: scripts/dev/attached_readers.q,
/ which the destination's receiver (scripts/dev/remote_receiver.q, #1650) loads too.
system"l scripts/dev/attached_readers.q";

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

/ ------------------------------------------------------------ REMOTE WRITER (#1650)
/ .
/ With -writer host:port the run extracts here and the destination's receiver
/ (scripts/dev/remote_receiver.q) commits every batch through its own HDB writer and tells
/ the readers, so this machine needs neither the HDB nor the readers. Coverage is filed
/ here, deferred as above, and released only when the receiver says its readers reloaded.

/ The receiver, as one synchronous call per message; refused when it cannot be reached.
/ @param opts the parsed command line
/ @return a function of one message
.qdev.attached.writer_call:{[opts]
    addr:`$":",(first opts`writer),":",(first opts`writer_user),":",getenv `UQF_ATTACH_WRITER_PASSWORD;
    h:@[hopen;(addr;10000);{[e] e}];
    if[10h=type h; '"attached_backfill: cannot reach the receiver at ",(first opts`writer),": ",h];
    {[h;m] h m}[h]}

/ ------------------------------------------------------------ FORCED WRITER
/ .
/ -force: the process at -writer is the stack's own (a wdb) and runs nothing of ours, so what it
/ does is sent with every call. These two lambdas are what it evaluates; they name only q and,
/ for an _own_ root, the TorQ wdb's .wdb.hdbdir. They stay in the root context so they serialise
/ as plain lambdas.

/ Evaluated BY THE WRITER: append one date's rows to <root>/<date>/<target>/ and return how many.
/ `p# comes off sym first, as io_hdb.q's append does; forced_sort puts it back.
/ @param root the HDB root, or ` for the writer's own .wdb.hdbdir
/ @param d the partition date
/ @param target the table
/ @param rows the date's rows, with a time column and no date column
/ @return the rows appended
.qdev.attached.forced_append:{[root;d;target;rows]
    db:$[null root; hsym .wdb.hdbdir; root];
    part:` sv .Q.par[db;d;target],`;
    data:.Q.en[db;rows];
    have:key part;
    if[`sym in have; @[{@[x;`sym;`#]};part;::]];
    / an interpreter that cannot upsert onto a splayed table on disk rewrites it whole
    $[()~have; part set data; .[upsert;(part;data);{[p;d;e] p set (get p),d}[part;data]]];
    count data}

/ Evaluated BY THE WRITER at the end: sort each date's partition by sym then time and put `p# on
/ sym (where the interpreter can), as a finished partition is left. Returns how many it sorted.
/ @param root as forced_append's
/ @param target the table
/ @param dates the dates written
/ @return the partitions sorted
.qdev.attached.forced_sort:{[root;target;dates]
    db:$[null root; hsym .wdb.hdbdir; root];
    sum {[db;target;d]
        base:.Q.par[db;d;target];
        part:` sv base,`;
        if[()~key part; :0];
        (`sym`time inter get ` sv base,`.d) xasc part;
        if[`sym in key part; @[{@[x;`sym;`p#]};part;::]];
        1}[db;target] each dates}

/ The client's manager: each window's rows go out a date at a time, through forced_append.
/ Refuses every strategy but append - anything else would need to read what the writer holds.
/ @param call the writer, as a function of one message
/ @param root the HDB root, or ` for the writer's own
/ @param partition_col the column `time copies when the batch has none (the source's time column)
/ @return a manager with write, write_keyed and finish
.qdev.attached.forced_manager:{[call;root;partition_col]
    write:{[call;root;pc;target;batch;opts]
        if[not `append~opts`on_conflict;
            '"attached_backfill: -force only appends - pass --on-conflict append (this run: ",string[opts`on_conflict],")"];
        if[0=count batch; :0];
        data:$[`time in cols batch; batch; `time xcols ![batch;();0b;enlist[`time]!enlist pc]];
        data:(cols[data] except `date)#data;
        days:`date$data`time;
        `.qdev.attached.forced_dates set distinct .qdev.attached.forced_dates,distinct days;
        sum {[call;root;target;data;days;d] call (.qdev.attached.forced_append;root;d;target;data where days=d)}[call;root;target;data;days] each distinct days}[call;root;partition_col];
    `write`write_keyed`finish!(
        {[w;target;batch] w[target;batch;enlist[`on_conflict]!enlist `append]}[write];
        write;
        {[call;root;x] / noqa: QF016 niladic
            n:call (.qdev.attached.forced_sort;root;.qdev.attached.dataset;.qdev.attached.forced_dates);
            `.qdev.attached.forced_dates set `date$();
            n}[call;root])}

/ The dates this run has sent, for the end-of-run sort.
.qdev.attached.forced_dates:`date$()

/ The remote manager's on_ready: the receiver has told its readers already; release coverage
/ when it reports they reloaded, and keep it owed when they did not.
/ @param status `finished`pending`final, as .qetl.io.flush and finish pass it
/ @return 1b when coverage was released
.qdev.attached.remote_ready:{[status]
    r:.qetl.io.remote.last_reload;
    s:.qdev.attached.reload_state;
    s[`dirty]:s[`dirty] or 0<status`finished;
    done:(`reloaded=r`outcome) and 0=status`pending;
    if[done; s[`dirty]:0b; .qetl.coverage.release_ready r`asked];
    if[`failed=r`outcome; `.qdev.attached.last_failed set enlist r`error];
    `.qdev.attached.reload_state set s;
    done}

/ The outcome of a run, and the exit code that says it.
/ @param result the lifecycle's result dictionary
/ @param dry whether the run wrote nothing by mode
/ @param forced whether it wrote through -force, where no reader is told: written_forced, never queryable
/ @return dict: outcome, queryable, code
.qdev.attached.outcome:{[result;dry;forced]
    code:.qetl.job.bounded.exit_code result`state;
    owed:.qdev.attached.reload_state`dirty;
    o:$[not 0=code; $[`partial~result`state; `partial; `failed];
        dry; `dry_run;
        forced; `written_forced;
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

remote:`writer in key o;
forced:remote and `force in key o;
.qdev.attached.dataset:decl`dataset;
$[remote;
    [bounds:enlist[`destination]!enlist `$first o`destid;
     call:@[.qdev.attached.writer_call;o;{[e] -1 e; exit 2}];
     / the receiver tells the readers: none are asked from here
     .qdev.attached.readers_named:.qdev.attached.readers enlist[`readers]!enlist enlist "_none_";
     $[forced;
        [froot:$["_own_"~first o`force_hdb; `; hsym `$first o`force_hdb];
         .qetl.io.default:.qdev.attached.forced_manager[call;froot;(.qetl.source.def decl`source)`time_column];
         .qetl.log.warn[worker;"FORCED: appending straight into the writer's HDB - nothing is checked there, a re-run duplicates rows, and no reader is told to reload";
            `attachment`destination`writer`root`state!(`$first o`attachment;bounds`destination;`$first o`writer;$[null froot;`$".wdb.hdbdir";froot];state)]];
        [.qetl.io.default:.qetl.io.remote.manager[call;`$first o`attachment;decl`dataset],enlist[`on_ready]!enlist .qdev.attached.remote_ready;
         .qetl.log.info[worker;"extracting here; the receiver on the destination's machine writes the HDB";
            `attachment`destination`writer`state!(`$first o`attachment;bounds`destination;`$first o`writer;state)]]]];
    [root:hsym `$first o`hdb;
     col:(.qetl.source.def decl`source)`time_column;
     bounds:@[.qdev.attached.bounds;o;{[e] -1 "attached_backfill: bad bounds: ",e; exit 2}];
     mgr:.[.qetl.io.hdb_scoped;(root;col;bounds);{[e] -1 "attached_backfill: ",e; exit 2}];
     / written is not queryable: coverage is released to the readers only once each has reloaded (#1094)
     .qdev.attached.readers_named:.qdev.attached.readers o;
     .qdev.attached.reach_user:first o`user;
     .qetl.io.default:mgr,enlist[`on_ready]!enlist .qdev.attached.on_ready;
     .qetl.log.info[worker;"writing into an attached stack's HDB";
        `attachment`destination`root`partition_col`max_date`deadline`state`readers!(`$first o`attachment;bounds`destination;root;col;bounds`max_date;bounds`deadline;state;`$", " sv string .qdev.attached.readers_named`name)]]];
/ forced: no reader is told, so coverage cannot wait for one
.qetl.coverage.defer_ready:not forced;

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
/ Remotely, the receiver's finish asks its readers whatever it finished (remote_receiver.q).
if[count .qetl.coverage.unready;
    $[remote; [.qetl.io.finish .qetl.io.default]; .qdev.attached.on_ready `finished`pending`final!(0;0;1b)]];
.qdev.attached.release o;
fin:.qdev.attached.outcome[result;not .qetl.job.bounded.runtime.allows`finish_store;forced];
.qetl.log.info[worker;"attached backfill finished";result,fin,`destination`reload_owed!(bounds`destination;.qdev.attached.reload_state`dirty)];
/ the launcher reads this line: written and queryable are different things
-1 "RESULT ",.j.j `worker`state`outcome`queryable`error`destination`readers`failed_readers!(string worker;string result`state;string fin`outcome;fin`queryable;$[`error in key result;$[10h=type e:result`error;e;string e];""];string bounds`destination;string .qdev.attached.readers_named`name;.qdev.attached.last_failed);
exit fin`code;
