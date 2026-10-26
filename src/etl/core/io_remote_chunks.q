/ io_remote_chunks.q - a window too large for one message, sent in chunks (#1650).
/ .
/ io_remote.q sends each window's batch as ONE synchronous message, and the
/ receiver refuses a batch over its grant's max_rows - so a window larger than
/ that could not be written remotely at all. Here a client splits such a batch
/ into chunks of at most .qetl.io.remote.chunk_rows rows, and the receiver
/ stages them and commits the window only when its last chunk arrives:
/ .
/   ALL OR NOTHING. A staged batch is not written until every chunk is in, and
/   then through .qetl.io.remote.accept - the same admission, journal and
/   id check (the reassembled rows must hash to the id the client claimed) as
/   a batch sent whole. A window whose chunks stop half-way writes nothing, and
/   its coverage does not move: the client's publish threw.
/ .
/   BACKPRESSURE. Every chunk is a synchronous call, so a client sends the next
/   only once the receiver has taken the last; and the receiver holds at most
/   .qetl.io.remote.max_staged_rows rows staged across every batch in flight,
/   refusing a chunk past it as busy rather than growing without bound. A batch
/   staged longer than .qetl.io.remote.stage_ttl is dropped - its client gave up
/   - and the window is sent again whole by the next run.
/ .
/   RESENDING IS SAFE. A chunk sent twice replaces itself; a batch already
/   committed answers `duplicate at its first chunk, before anything is staged.
/ .
/ Each chunk is admitted as it arrives - the grant, the user, the schema, the
/ window and the per-message max_rows - so a bad batch is refused at its first
/ bad chunk, not after the rest has been staged.

\d .

/ The most rows a client puts in one message; a larger batch is chunked. At or
/ under the receiver's max_rows (remote_receiver.q's -max_rows, 100000 by default).
.qetl.io.remote.chunk_rows:50000

/ The most rows a receiver holds staged across every batch in flight.
.qetl.io.remote.max_staged_rows:5000000

/ How long a partly-sent batch is kept before it is dropped.
.qetl.io.remote.stage_ttl:0D00:10

/ The batches being staged: batch id -> dict of request (less its rows), of
/ (how many chunks), chunks (one slot per chunk) and first (when the first came).
.qetl.io.remote.staged:(enlist `)!enlist (::)

/ The keys a chunk carries, exactly.
.qetl.io.remote.chunk_keys:`attachment`dataset`target`batch_id`opts`seq`of`rows

/ Private: rows staged now, across every batch.
/ @private
.qetl.io.remote.staged_rows:{[]
    v:1_value .qetl.io.remote.staged;
    $[count v; sum {[e] sum count each e`chunks} each v; 0]}

/ Private: drop batches staged longer than the time-to-live.
/ @private
.qetl.io.remote.expire_staged:{[now]
    k:1_key .qetl.io.remote.staged;
    old:k where {[now;e] now>(e`first)+.qetl.io.remote.stage_ttl}[now] each .qetl.io.remote.staged k;
    if[count old; .qetl.io.remote.staged:old _ .qetl.io.remote.staged];
    count old}

/ Private: refuse a malformed chunk, as require_request refuses a batch.
/ @private
.qetl.io.remote.require_chunk:{[req]
    if[not 99h=type req; '"remote: a chunk is a dictionary"];
    if[not (asc key req)~asc .qetl.io.remote.chunk_keys;
        '"remote: a chunk carries exactly ",", " sv string .qetl.io.remote.chunk_keys];
    if[not all -7h=type each req`seq`of; '"remote: seq and of are longs"];
    if[not (req[`of]>0) and (req[`seq]>=0) and req[`seq]<req`of;
        '"remote: chunk ",string[req`seq]," of ",string[req`of]," is not a chunk of that batch"];
    if[null req`batch_id; '"remote: a chunk names the batch it belongs to"];
    / the rest as a whole batch's: its rows stand in for the batch
    .qetl.io.remote.require_request (`seq`of`rows _ req),enlist[`batch]!enlist req`rows;
    }

/ Take one chunk: admit it, stage it, and commit the batch when it is whole.
/ @param req the chunk (chunk_keys)
/ @param user the remote user, or ` for an in-process call
/ @return dict status (`staged, or as accept once whole), batch_id and rows
/ @throws error, staging nothing, for a chunk not admitted, or when busy
.qetl.io.remote.accept_chunk:{[req;user]
    .qetl.io.remote.require_chunk req;
    whole:(`seq`of`rows _ req),enlist[`batch]!enlist req`rows;
    k:.qetl.io.remote.grant_key[req`attachment;req`dataset];
    if[not k in key .qetl.io.remote.grants;
        '"remote: nothing is granted for ",string[req`dataset]," of attachment ",string req`attachment];
    .qetl.io.remote.require_admitted[.qetl.io.remote.grants k;whole;user];
    id:req`batch_id;
    held:exec rows from .qetl.io.remote.journal where batch_id=id, state=`committed;
    if[count held; :`status`batch_id`rows!(`duplicate;id;first held)];
    .qetl.io.remote.expire_staged[.z.p];
    e:$[id in key .qetl.io.remote.staged; .qetl.io.remote.staged id;
        `req`of`chunks`first!(`seq`of`rows _ req;req`of;(req`of)#enlist ();.z.p)];
    if[not (e`of)=req`of;
        '"remote: batch ",string[id]," was being sent in ",string[e`of]," chunks, not ",string req`of];
    / its own earlier copy does not count against the room it takes
    room:.qetl.io.remote.max_staged_rows-.qetl.io.remote.staged_rows[]-count e[`chunks] req`seq;
    if[count[req`rows]>room;
        '"remote: busy - ",string[.qetl.io.remote.staged_rows[]]," rows are staged, the limit is ",
         string[.qetl.io.remote.max_staged_rows],"; send again when earlier batches are in"];
    e[`chunks]:@[e`chunks;req`seq;:;req`rows];
    if[any 0=count each e`chunks;
        .qetl.io.remote.staged[id]:e;
        :`status`batch_id`rows!(`staged;id;count req`rows)];
    .qetl.io.remote.staged:(enlist id) _ .qetl.io.remote.staged;
    .qetl.io.remote.accept[(e`req),enlist[`batch]!enlist raze e`chunks;user]}

/ The entry point a client's chunk names: accept_chunk, as this connection's user.
/ @param req the chunk
/ @return as accept_chunk
.qetl.io.remote.submit_chunk:{[req] .qetl.io.remote.accept_chunk[req;$[0=.z.w; `; .z.u]]}

.qetl.io.remote.entry_points[`.qetl.io.remote.submit_chunk]:1

/ Private: send a batch in chunks; the last answer is the batch's.
/ @private
.qetl.io.remote.send_chunks:{[call;attachment;dataset;target;id;o;batch]
    n:.qetl.io.remote.chunk_rows;
    parts:(n*til ceiling count[batch]%n) cut batch;
    k:count parts;
    r:();
    i:0;
    while[i<k;
        r:call (`.qetl.io.remote.submit_chunk;.qetl.io.remote.chunk_keys!(attachment;dataset;target;id;o;i;k;parts i));
        / committed already: the rest need not be sent
        if[`duplicate=r`status; :r];
        i+:1];
    r}

\d .
