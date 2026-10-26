// remote_receiver.q - the destination side of a backfill extracted on another machine (#1650).
//
//   q scripts/dev/remote_receiver.q -p PORT -worker W -attachment NAME -destid ID \
//       -hdb /path/to/native/hdb -max_date 2026.10.15 -deadline 2026.10.16D23:00 \
//       -readers name@host:port,... -user U -journal /path/to/journal -writers alice,bob [-max_rows N]
//
// Started on the stack's machine by `uqs job receive W --attach NAME` (uqs.attach.backfill_receive),
// which has resolved the destination from the saved attachment and admitted it against the live stack
// exactly as an attached backfill does (uqs.attach.backfill_plan) - so the bounds, the readers and the
// HDB are the verified ones, and nothing is read from $KDBHDB.
//
// It grants ONE (attachment; dataset) to remote writers (.qetl.io.remote.serve): an extractor running
// `uqs job backfill W --attach NAME --writer host:PORT` sends each window's batch here, and this
// process admits it (the grant, the user, the worker's output schema, a row limit, the window) and
// commits it through .qetl.io.hdb_scoped - the same lock, date bounds, end-of-day fence, staging and
// recovery as a backfill writing the HDB itself. The journal (.qetl.io.remote) makes a resent batch a
// duplicate rather than a second append, across a restart of this process too.
//
//   -writers     the users an extractor may connect as (.z.u); everyone else is refused per batch
//   -journal     where the batch journal persists - in the destination's state directory
//   -max_rows    the most rows one batch may carry (default 100000)
//   the rest     as scripts/dev/attached_backfill.q; the readers' password is $UQF_ATTACH_REACH_PASSWORD
//
// THE GATE. .z.pg admits the receiver's entry points with plain-data arguments and nothing else
// (.qetl.io.remote.permitted); .z.ps refuses everything. Authentication is q's own: start it with
// -u or -U, as uqs does, so a connection carries a user to check.
//
// It stops itself at the deadline: past the end-of-day fence the writer refuses every batch anyway.

if[not all `worker`attachment in key opts:.Q.opt .z.x;
    -1 "remote_receiver: -worker and -attachment are required";
    exit 2];

\l src/init.q
.qetl.load.only:`$first opts`worker
\l src/etl/init.q
\l scripts/dev/attached_readers.q

\d .

.qdev.receiver.required:`destid`hdb`max_date`deadline`readers`user`journal`writers

/ The receiver's on_ready: on a run's final call, ask every reader whatever is owed - an earlier
/ run's reload may have failed before this process restarted - and mid-run as .qetl.io.due says.
/ Throws on a final call that leaves a reader without the rows, so the extractor is told the rows
/ are written and NOT queryable (.qetl.io.remote.reload records it apart from the write).
/ @param status `finished`pending`final
/ @return 1b when it asked
.qdev.receiver.on_ready:{[status]
    s:.qdev.attached.reload_state;
    act:$[status`final; 1b; (.qetl.io.due[s;status;.z.p;.qdev.attached.reload_interval[]])`act];
    if[not act; `.qdev.attached.reload_state set @[s;`dirty;|;0<status`finished]; :0b];
    r:.qdev.attached.reload_all[];
    `.qdev.attached.reload_state set .qetl.io.acknowledged[`dirty`last!(0b;.z.p);r];
    `.qdev.attached.last_failed set r`failed;
    if[.qdev.attached.reload_state`dirty;
        '"readers did not reload: ",", " sv r`failed];
    1b}

\d .

o:opts;
if[count missing:.qdev.receiver.required except key o;
    -1 "remote_receiver: missing ",", " sv "-",/:string missing; exit 2];
if[not system"p"; -1 "remote_receiver: start it with -p PORT - the extractor connects to it"; exit 2];
worker:`$first o`worker;
decl:.qetl.job.bounded.def worker;
if[not (::)~decl`io;
    -1 "remote_receiver: ",string[worker]," declares its own IO manager, which would write where it likes";
    exit 2];
root:hsym `$first o`hdb;
col:(.qetl.source.def decl`source)`time_column;
bounds:`max_date`deadline`destination!("D"$first o`max_date;"P"$first o`deadline;`$first o`destid);
if[any null bounds`max_date`deadline; -1 "remote_receiver: bad -max_date or -deadline"; exit 2];
mgr:.[.qetl.io.hdb_scoped;(root;col;bounds);{[e] -1 "remote_receiver: ",e; exit 2}];
.qdev.attached.readers_named:.qdev.attached.readers o;
.qdev.attached.dataset:decl`dataset;
.qdev.attached.reach_user:first o`user;
/ the batch a worker publishes: its transform's output (.qetl.job.bounded.publish)
schema:0#(.qetl.transform.def decl`transform)`output;
max_rows:$[`max_rows in key o; "J"$first o`max_rows; 100000];
.qetl.io.remote.open hsym `$first o`journal;
.qetl.io.remote.serve[`$first o`attachment;decl`dataset;`mgr`target`schema`max_rows`users!(
    mgr,enlist[`on_ready]!enlist .qdev.receiver.on_ready;decl`dataset;schema;max_rows;`$"," vs first o`writers)];

.z.pg:{[m]
    if[not .qetl.io.remote.permitted m; '"remote_receiver: not an entry point"];
    value m}
.z.ps:{[m] '"remote_receiver: asynchronous messages are refused"}
/ past the end-of-day fence nothing is admitted: stop rather than refuse every batch
.z.ts:{[ignored] if[.z.p>bounds`deadline; -1 "remote_receiver: the end-of-day fence began - stopping"; exit 0]}
\t 60000

-1 "remote_receiver: serving ",string[decl`dataset]," of attachment ",(first o`attachment),
    " into ",(1_string root)," on port ",string[system"p"],
    "; writers ",(first o`writers),"; until ",string bounds`deadline;
