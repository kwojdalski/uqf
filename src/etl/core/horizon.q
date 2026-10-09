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

/ What it may declare besides. The first five are the kind as #945 shipped
/ it; the rest (#952) are each OFF unless declared, so a declaration that
/ names none of them behaves exactly as before.
optional_keys:`max_age`by`keep`start_with_all`note,
    `event_time`reference_time`lookback`ready_on`legs`identity`remember`expire_after

/ What define installs in .qpipe.job.<name>. They are the shell's, documented
/ here and in define, as a bounded worker's inherited methods are
/ .qetl.job.bounded's: pending and history (the buffers), publish (the seam),
/ now (the clock, .z.p), on_batch and score_ready[now], on_timer, and the
/ ledgers completed (identities scored, while remembered) and expired
/ (events given up on, each with the reason).
installed:`pending`history`publish`now`on_batch`score_ready`on_timer`completed`expired

/ The readiness rules: `wall`, an event is ready once horizon has elapsed
/ since its event_time; `reference`, once every reference key it needs has an
/ as-of anchor at or before event_time - lookback and has advanced to
/ event_time + horizon.
ready_rules:`wall`reference

/ Private: the declaration with every default filled in, `by` and identity
/ normalised to lists - or a refusal naming what is wrong. Apart from define
/ for size alone: one function holding every check exceeds what q, and the
/ coverage instrumenter, allow a lambda.
/ @private
normalise:{[who;decl;ins]
    ev_cols:cols first value ins; ref_cols:cols last value ins;
    / key_cols, not `by`: by is a qSQL keyword, which q reserves
    key_cols:$[`by in key decl; (),decl`by; enlist `sym];
    absent:key_cols where not key_cols in ref_cols;
    if[count absent;
        'who,"'s by names ",(", " sv string absent),", which the reference input does not carry"];
    / the defaults, then what was declared, then `by` normalised to a list
    defaults:`event_time`reference_time`lookback`ready_on`remember!(`time;`time;0D;`wall;0D01);
    d:defaults,decl,enlist[`by]!enlist key_cols;
    if[not -11h=type d`event_time; 'who,"'s event_time must be one column name, a symbol"];
    if[not -11h=type d`reference_time; 'who,"'s reference_time must be one column name, a symbol"];
    if[not (d`event_time) in ev_cols;
        'who,"'s events input carries no ",string[d`event_time]," - the horizon is measured from it"];
    if[not (d`reference_time) in ref_cols;
        'who,"'s reference input carries no ",string[d`reference_time]];
    if[not $[-16h=type d`lookback; 0D<=d`lookback; 0b];
        'who,"'s lookback must be a timespan, how far before an event its window reaches"];
    if[not (d`ready_on) in ready_rules;
        'who,"'s ready_on must be one of ",", " sv string ready_rules];
    if[$[`legs in key d; not 100h<=type d`legs; 0b]; 'who,"'s legs must be a function of the pending events"];
    if[(`legs in key d) and not `reference=d`ready_on;
        'who," declares legs, which only ready_on `reference reads"];
    if[`reference=d`ready_on;
        if[1<>count key_cols; 'who,"'s ready_on `reference needs a one-column by - the key its legs name"];
        if[(not `legs in key d) and not (first key_cols) in ev_cols;
            'who,"'s events carry no ",string[first key_cols],", so each event's reference key must come from legs"]];
    if[`identity in key d;
        idc:(),d`identity;
        if[not 11h=type idc; 'who,"'s identity must be event column names, symbols"];
        if[count idc except ev_cols;
            'who,"'s identity names ",(", " sv string idc except ev_cols),", which the events input does not carry"];
        d[`identity]:idc];
    if[(`remember in key decl) and not `identity in key d; 'who," declares remember without identity - nothing would be remembered"];
    if[not $[-16h=type d`remember; 0D<d`remember; 0b]; 'who,"'s remember must be a positive timespan"];
    if[`expire_after in key d;
        if[not $[-16h=type d`expire_after; d[`expire_after]>d`horizon; 0b];
            'who,"'s expire_after must be a timespan longer than the horizon - an event cannot be late before it is due"]];
    d}

/ Declare a job that evaluates each event once its horizon has passed.
/ .
/ Installs, in .qpipe.job.<name>: the buffers `pending` (events) and
/ `history` (reference), `on_batch`, `score_ready[now]`, `on_timer`, `now`,
/ the ledgers `completed` and `expired`, and the unwired `publish`, then
/ registers the streaming job.
/ @param name the job's name
/ @param decl dict of procname, events (the table carrying events), reference
/   (the table they are scored against), transform (two inputs, events first),
/   publishes (the table it publishes), horizon (how long an event waits, a
/   timespan), period (the timer); optionally max_age (a timespan, see the
/   header), by (the reference's key columns, default `sym), keep (a function
/   of a batch returning which rows to buffer), start_with_all, note,
/   event_time and reference_time (the columns each table's clock is read
/   from, default `time - e.g. `source_time), lookback (how far BEFORE an
/   event its window reaches, default 0D), ready_on (`wall, the default, or
/   `reference), legs (a function of the pending events giving each one's
/   reference keys, for ready_on `reference; default its own `by` value),
/   identity (event columns naming one event: a redelivery replaces the
/   pending one, and one already scored is dropped), remember (how long a
/   scored identity is remembered, default 0D01) and expire_after (how long
/   after its event_time an event that is still not ready is given up on)
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
    d:normalise[who;decl;ins];
    if[$[`keep in key decl; not 100h<=type decl`keep; 0b]; 'who,"'s keep must be a function of a batch"];
    ns:.qetl.job.stream.namespace name;
    / the state and the surface every job has, in the one place a test, a
    / reader or the runner looks for them
    ev0:0#first value ins;
    (` sv ns,`pending) set ev0;
    (` sv ns,`history) set 0#last value ins;
    (` sv ns,`completed) set $[`identity in key d; (d[`identity]#ev0),'([] at:`timestamp$()); ([] at:`timestamp$())];
    (` sv ns,`expired) set ev0,'([] expired_at:`timestamp$(); reason:());
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
    registry[name]:enlist d;
    name}

/ One horizon job's declaration, or a refusal naming it.
/ @param name the job
/ @return the declaration dict, with every default filled in
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
/ With identity, an event already scored (and still remembered) is dropped,
/ and a redelivery REPLACES the pending one - the last delivery wins, within
/ a batch too. A batch on any other table is dropped: the plant sends only
/ what was subscribed to, so that is a hand call or a test.
/ @private
on_batch:{[name;t;x]
    d:def[name];
    which:$[t=d`events; `pending; t=d`reference; `history; `];
    if[null which; :()];
    if[`keep in key d; x:x where (d`keep) x];
    if[0=count x; :()];
    ns:.qetl.job.stream.namespace name;
    buffer:` sv ns,which;
    x:(cols get buffer)#x;
    if[(which=`pending) and `identity in key d;
        idc:d`identity;
        x:x where not (idc#x) in idc#get ` sv ns,`completed;
        if[0=count x; :()];
        x:x asc last each value group idc#x;
        buffer set (get buffer) where not (idc#get buffer) in idc#x];
    buffer insert x;
    }

/ Private: per reference key, the earliest and latest reference time held -
/ the anchor and how far the reference has advanced.
/ @private
reach:{[d;hist]
    k:hist first d`by; rt:hist d`reference_time;
    g:group k;
    `lo`hi!(min each rt g;max each rt g)}

/ Private: each pending event's reference keys - its legs, or its own key.
/ @private
legs_of:{[d;pending] $[`legs in key d; (d`legs) pending; enlist each pending first d`by]}

/ Private: why each event is not ready under `reference - "" when it is.
/ An event is ready when every key it needs has an anchor at or before
/ event_time - lookback and has advanced to event_time + horizon.
/ @private
not_ready:{[d;r;et;legs]
    lo:r`lo; hi:r`hi;
    {[d;lo;hi;t;ks]
        ks:(),ks;
        absent:ks where not ks in key lo;
        if[count absent; :"no reference for ",", " sv string absent];
        early:ks where lo[ks]>t-d`lookback;
        if[count early; :"no ",string[d`reference_time]," at or before ",string[t-d`lookback]," for ",", " sv string early];
        short:ks where hi[ks]<t+d`horizon;
        if[count short; :"reference has not reached ",string[t+d`horizon]," for ",", " sv string short];
        ""}[d;lo;hi]'[et;legs]}

/ Private: the as-of-safe reference rows to keep, given the earliest instant
/ any pending event can still ask about (see the header).
/ @private
retain:{[d;hist;earliest]
    rt:hist d`reference_time;
    if[`max_age in key d; :hist where rt>=earliest-d`max_age];
    old:where rt<earliest;
    if[0=count old; :hist];
    / the latest pre-cutoff row per key is still an as-of answer
    latest:old last each value group (d`by)#hist old;
    hist asc (til[count hist] except old),latest}

/ Private: score every event that is ready by `now`, publish the rows, then
/ evict them; give up on events past expire_after, with the reason; and,
/ every tick, drop the reference rows and remembered identities nothing
/ pending can still need. Scoring and publishing come BEFORE anything is
/ evicted or remembered, so a transform or a publish that throws leaves
/ every event queued for the next tick.
/ @private
score_ready:{[name;now]
    d:def[name];
    ns:.qetl.job.stream.namespace name;
    pq:` sv ns,`pending; hq:` sv ns,`history; cq:` sv ns,`completed; xq:` sv ns,`expired;
    pending:get pq;
    et:pending d`event_time;
    why:$[`reference=d`ready_on;
        not_ready[d;reach[d;get hq];et;legs_of[d;pending]];
        {$[x;"";"horizon has not elapsed"]} each et<=now-d`horizon];
    mask:0=count each why;
    if[any mask;
        ins:key .qetl.transform.def[d`transform]`inputs;
        ready:pending where mask;
        out:.qetl.transform.apply[d`transform;ins!(ready;get hq)];
        (get ` sv ns,`publish)[d`publishes;out];
        if[`identity in key d; cq insert (d[`identity]#ready),'([] at:(count ready)#now)];
        .qetl.job.stream.evict[pq;mask]];
    if[`expire_after in key d;
        pending:get pq;
        et:pending d`event_time;
        lapsed:et<=now-d`expire_after;
        if[any lapsed;
            gone:pending where lapsed;
            reasons:$[`reference=d`ready_on;
                not_ready[d;reach[d;get hq];gone d`event_time;legs_of[d;gone]];
                (count gone)#enlist "horizon has not elapsed"];
            xq insert gone,'([] expired_at:(count gone)#now; reason:reasons);
            {[name;r] .qetl.log.warn[name;"event expired unresolved";enlist[`reason]!enlist r]}[name] each distinct reasons;
            .qetl.job.stream.evict[pq;lapsed]]];
    / every tick, scoring or not: a job whose events stop arriving must not
    / keep every reference row it is sent
    left:(get pq) d`event_time;
    earliest:$[count left; min left; now-d`horizon];
    hq set retain[d;get hq;earliest-d`lookback];
    if[`identity in key d; cq set (get cq) where (get cq)[`at]>=now-d`remember];
    }

\d .qetl.job.stream

/ Declare a streaming job that scores each event once its horizon has passed.
/ @param name the job name
/ @param decl events, reference, transform, publishes, horizon, period and the process declaration
/ @return the registered streaming job name
/ @throws error when the transform is not a two-input contract for the declared tables
at_horizons:{[name;decl] .qetl.job.stream.horizons.define[name;decl]}

\d .
