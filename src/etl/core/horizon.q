/ horizon.q - the job kind that evaluates each event once its horizon has
/ passed (.qetl.job.stream.horizons, #945).
/ .
/ WHY A KIND. A markout scores a fill against the market some time AFTER it:
/ the fill arrives now, and the price it is judged by arrives later. So the
/ job queues the fill, keeps the reference rows that will price it, and on a
/ timer scores whatever has waited long enough. demo_markout and
/ crypto_markout each built that by hand - a pending queue, a reference
/ history, score_ready, evict - line for line the same, and the copies had
/ already drifted: one bounded its history, the other let it grow for as
/ long as the process ran. A third would have been a third copy.
/ .
/ What an instance declares is what differs between them: which table
/ carries the events, which the reference, the transform that scores them,
/ how long an event must wait, and how far back a reference row is still of
/ use. The shell owns the rest.
/ .
/ THE TRANSFORM IS THE CONTRACT. It is a declared .qetl.transform with two
/ inputs: the FIRST is the events, the SECOND the reference. The buffers are
/ those inputs' own schemas, and each batch is projected onto them, so the
/ queue cannot drift from what the transform reads - and the same transform
/ serves the job's backfill twin.
/ .
/ WHAT A REFERENCE ROW IS STILL GOOD FOR decides eviction, and it is one of
/ two things, declared rather than guessed:
/ .
/   max_age given    a reference row older than max_age at the horizon is
/                    never used (crypto_markout: a venue that went quiet must
/                    not set the price). Every row older than the oldest
/                    pending event minus max_age is dropped.
/   max_age absent   the latest reference row is used however old - an
/                    as-of join (demo_markout). Rows older than the oldest
/                    pending event are dropped, EXCEPT the latest such row
/                    per `by` key: it is still the as-of answer for any
/                    horizon before that key's next row.
/ .
/ Either way the history is bounded by the events still waiting, and the
/ answer for every pending event is the one the full history would give.
/ .
/ EVICT AFTER PUBLISHING. The runner's safe timer swallows an error, so a
/ publish that throws must leave the events queued for the next tick rather
/ than lose them.
/ .
/ REGISTERED AS A STREAMING JOB: define performs .qetl.job.stream.define
/ itself, subscribing to the two tables and publishing the transform's
/ output, so an instance cannot declare its edges apart from its contract.

\d .qetl.job.stream.horizons

/ name -> its declaration, ENLISTED, for the reason .qetl.job.stream.jobs enlists.
registry:(`symbol$())!();

/ What every horizon job declares.
required_keys:`procname`events`reference`transform`publishes`horizon`period

/ What it may declare besides.
optional_keys:`max_age`by`keep`start_with_all`note

/ What define installs in .qpipe.job.<name>. They are the shell's, documented
/ here and in define, as a bounded worker's inherited methods are
/ .qetl.job.bounded's: pending and history (the buffers), publish (the seam),
/ now (the clock, .z.p), on_batch and score_ready[now], and on_timer.
installed:`pending`history`publish`now`on_batch`score_ready`on_timer

/ Declare a job that evaluates each event once its horizon has passed.
/ .
/ Installs, in .qpipe.job.<name>: the buffers `pending` (events) and
/ `history` (reference), `on_batch`, `score_ready[now]`, `on_timer`, `now`
/ and the unwired `publish`, then registers the streaming job.
/ @param name the job's name
/ @param decl dict of procname, events (the table carrying events), reference
/   (the table they are scored against), transform (two inputs, events first),
/   publishes (the table it publishes), horizon (how long an event waits, a
/   timespan), period (the timer); optionally max_age (a timespan, see the
/   header), by (the reference's key columns, default `sym), keep (a function
/   of a batch returning which rows to buffer), start_with_all and note
/ @return the name
/ @throws error naming every problem it finds first
define:{[name;decl]
    who:"define: horizon job ",string[name];
    if[not 99h=type decl; 'who,"'s declaration must be a dictionary"];
    missing:required_keys where not required_keys in key decl;
    if[count missing; 'who," is missing ",", " sv string missing];
    unknown:(key decl) except required_keys,optional_keys;
    if[count unknown;
        'who," declares ",(", " sv string unknown),", which a horizon job does not take"];
    if[not all -11h=type each decl`events`reference`transform`publishes;
        'who,"'s events, reference, transform and publishes must each be one table or transform name, a symbol"];
    / $[], not `and`: q's `and` evaluates both sides, so a mistyped value
    / would throw a bare 'type before the message naming it
    if[not $[-16h=type decl`horizon; 0D<decl`horizon; 0b];
        'who,"'s horizon must be a positive timespan - how long an event waits before it is scored"];
    if[$[`max_age in key decl; not $[-16h=type decl`max_age; 0D<=decl`max_age; 0b]; 0b];
        'who,"'s max_age must be a timespan, the oldest a reference row may be at a horizon and still count"];
    if[not (decl`transform) in .qetl.transform.defined[];
        'who,"'s transform ",string[decl`transform]," is not registered - it is the job's contract, so it is declared first"];
    ins:.qetl.transform.def[decl`transform]`inputs;
    if[2<>count ins;
        'who,"'s transform ",string[decl`transform]," takes ",string[count ins]," inputs - it takes two: the events, then the reference"];
    / key_cols, not `by`: by is a qSQL keyword, which q reserves
    key_cols:$[`by in key decl; (),decl`by; enlist `sym];
    absent:key_cols where not key_cols in cols last value ins;
    if[count absent;
        'who,"'s by names ",(", " sv string absent),", which the reference input does not carry"];
    if[not `time in cols first value ins; 'who,"'s events input carries no time - the horizon is measured from it"];
    if[not `time in cols last value ins; 'who,"'s reference input carries no time"];
    if[$[`keep in key decl; not 100h<=type decl`keep; 0b]; 'who,"'s keep must be a function of a batch"];
    ns:.qetl.job.stream.namespace name;
    / the state and the surface every job has, in the one place a test, a
    / reader or the runner looks for them
    (` sv ns,`pending) set 0#first value ins;
    (` sv ns,`history) set 0#last value ins;
    (` sv ns,`publish) set .qetl.job.stream.unwired name;
    / .z.p, not .proc.cp[]: .u.upd stamps every row with the plant's .z.p, so
    / the times compared against are UTC, and .proc.cp[] is local time under
    / TorQ's -localtime. A function, so a test can stand a fixed time in.
    (` sv ns,`now) set {[] .z.p};
    (` sv ns,`on_batch) set on_batch[name;;];
    (` sv ns,`score_ready) set score_ready[name;];
    / a projection with its tick argument still open: applied in full
    / here, `{[ns] ...}[ns]` would run once, now, rather than per tick
    (` sv ns,`on_timer) set {[ns;tick] (get ` sv ns,`score_ready) (get ` sv ns,`now)[]}[ns];
    extra:(`start_with_all`note inter key decl)#decl;
    .qetl.job.stream.define[name;(`procname`subscribe_to`publishes`on_batch`period`on_timer`transform!(
        decl`procname;
        decl`events`reference;
        enlist decl`publishes;
        get ` sv ns,`on_batch;
        decl`period;
        get ` sv ns,`on_timer;
        decl`transform)),extra];
    / registered only once the streaming job is: a refusal there (a procname
    / another job runs, say) must not leave a horizon job behind
    registry[name]:enlist decl,enlist[`by]!enlist key_cols;
    name}

/ One horizon job's declaration, or a refusal naming it.
/ @param name the job
/ @return the declaration dict, `by` filled in
/ @throws error when nothing was defined under that name
/ @eg .qetl.job.stream.horizons.def[`crypto_markout]`horizon  ->  0D00:00:10
def:{[name]
    if[not name in key registry;
        '"def: ",string[name]," is not a defined horizon job - defined: ",", " sv string key registry];
    first registry name}

/ Every defined horizon job.
/ @return a symbol vector
/ @eg `demo_markout in .qetl.job.stream.horizons.defined[]  ->  1b
defined:{[] key registry}

/ Private: buffer one batch - events into `pending`, reference rows into
/ `history` - projected onto the transform's input columns, after `keep`.
/ A batch on any other table is dropped: the plant sends only what was
/ subscribed to, so that is a hand call or a test.
/ @private
on_batch:{[name;t;x]
    d:def[name];
    which:$[t=d`events; `pending; t=d`reference; `history; `];
    if[null which; :()];
    if[`keep in key d; x:x where (d`keep) x];
    if[0=count x; :()];
    ns:.qetl.job.stream.namespace name;
    buffer:` sv ns,which;
    buffer insert (cols get buffer)#x;
    }

/ Private: the as-of-safe reference rows to keep, given the oldest instant
/ any pending event can still ask about (see the header).
/ @private
retain:{[d;hist;oldest]
    if[`max_age in key d; :hist where hist[`time]>=oldest-d`max_age];
    old:where hist[`time]<oldest;
    if[0=count old; :hist];
    / the latest pre-cutoff row per key is still an as-of answer
    latest:old last each value group (d`by)#hist old;
    hist asc (til[count hist] except old),latest}

/ Private: score every event whose horizon has passed by `now`, publish the
/ rows, then evict the events - and, every tick, the reference rows nothing
/ pending can still use.
/ @private
score_ready:{[name;now]
    d:def[name];
    ns:.qetl.job.stream.namespace name;
    pq:` sv ns,`pending; hq:` sv ns,`history;
    pending:get pq;
    mask:pending[`time]<=now-d`horizon;
    if[any mask;
        ins:key .qetl.transform.def[d`transform]`inputs;
        out:.qetl.transform.apply[d`transform;ins!(pending where mask;get hq)];
        (get ` sv ns,`publish)[d`publishes;out];
        .qetl.job.stream.evict[pq;mask]];
    / every tick, scoring or not: a job whose events stop arriving must not
    / keep every reference row it is sent
    left:(get pq)`time;
    oldest:$[count left; min left; now-d`horizon];
    hq set retain[d;get hq;oldest];
    }

\d .qetl.job.stream

/ Declare a streaming job that scores each event once its horizon has passed.
/ @param name the job name
/ @param decl events, reference, transform, publishes, horizon, period and the process declaration
/ @return the registered streaming job name
/ @throws error when the transform is not a two-input contract for the declared tables
at_horizons:{[name;decl] .qetl.job.stream.horizons.define[name;decl]}

\d .
