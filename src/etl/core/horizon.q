/ horizon.q - the job kind that evaluates each event once its horizon has
/ passed (.qetl.job.stream.horizons, #945).
/ .
/ WHY A KIND. A markout scores a fill against the market some time AFTER it:
/ the fill arrives now, and the price it is judged by arrives later. So the
/ job queues the fill, keeps the reference rows that will price it, and on a
/ timer scores whatever has waited long enough. The first two markout
/ jobs each built that by hand - a pending queue, a reference
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
/   (or 0Wn)         as-of join (fx_markout). 0Wn, how a spec says
/                    "however old", is the same declaration (#1480). Rows
/                    older than the oldest pending event are dropped, EXCEPT
/                    the latest such row per `by` key: it is still the as-of
/                    answer for any horizon before that key's next row.
/ .
/ Under ready_on `reference (or `both) the latest pre-cutoff row per series is
/ kept with max_age too: readiness needs an anchor at or before event_time -
/ lookback, and a tick that ran before the reference caught up must not delete
/ it (#959). A SERIES is a `by` key, or with retain_by (#1395) the `by` key and
/ whatever else makes rows independent - a source: a reference quoted by
/ several sources has an as-of answer per source, and keeping only the latest
/ row per key would evict a quiet source's book when another one ticks.
/ .
/ With max_age the anchor kept is one per `by` key, not one per retain_by series:
/ a source's pre-cutoff book is older than max_age for every pending event, so it
/ can answer nothing, and keeping one per source would hold a source that stopped
/ quoting for ever (#1410). Readiness needs only the key's anchor.
/ .
/ Either way the history is bounded by the events still waiting, and the
/ answer for every pending event is the one the full history would give.
/ .
/ LATE EVENTS (#958). The bound above reads "no future event is older than the
/ oldest pending one", which holds only when event_time is receipt time. With
/ event_time:`source_time an event can arrive after later reference rows, so
/ eviction also subtracts lateness: how much older than its arrival an
/ event's event_time can be. Default 0D when event_time is `time; REQUIRED
/ when it is a source stamp (the one rule of every event-time kind, see
/ .qetl.job.stream.with_lateness, #2116). A bound, not a guess about the
/ data: an event later than it may find its reference evicted.
/ .
/ READINESS IS PER SOURCE, AND BOUNDED (#1523, #1524). With retain_by, `hi` alone
/ (the best of the sources) would call an event ready once ONE source passed the
/ horizon, scoring it without a slower source's book that is still in flight.
/ So every series held must have advanced to it - however far behind it runs, as
/ its books stamped inside max_age of the horizon may still be in flight (#2455);
/ the lookback reach is waived likewise. Both wait only until
/ event_time + horizon + lateness on the plant clock, after which a book
/ stamped earlier cannot still arrive: a source that never delivers, or books
/ that start inside the lookback, then leave the affected horizons unavailable
/ as the backfill does, rather than hold the event until it expires.
/ .
/ EVICT AFTER PUBLISHING. The runner's safe timer swallows an error, so a
/ publish that throws must leave the events queued for the next tick rather
/ than lose them.
/ .
/ REGISTERED AS A STREAMING JOB: define performs .qetl.job.stream.define
/ itself, subscribing to the two tables and publishing the transform's
/ output, so an instance cannot declare its edges apart from its contract.

\d .

/ name -> its declaration, ENLISTED, for the reason .qetl.job.stream.jobs enlists.
.qetl.job.stream.horizons.registry:(`symbol$())!();

.qetl.job.stream.registries:distinct .qetl.job.stream.registries,`.qetl.job.stream.horizons.registry;

/ What every horizon job declares.
.qetl.job.stream.horizons.required_keys:`events`reference`transform`outputs`horizon`lateness`period

/ What it may declare besides. The first five are the kind as #945 shipped
/ it; the rest (#952) are each OFF unless declared, so a declaration that
/ names none of them behaves exactly as before.
.qetl.job.stream.horizons.optional_keys:`procname`max_age`by`keep`ephemeral,.qetl.job.stream.passthrough_keys,
    `event_time`reference_time`lookback`ready_on`legs`identity`remember`expire_after`retain_by`shared_reference

/ What define installs in .qpipe.job.<name>. They are the shell's, documented
/ here and in define, as a bounded worker's inherited methods are
/ .qetl.job.bounded's: pending and history (the buffers), publish (the seam),
/ now (the clock, .z.p), on_batch and score_ready[now], on_timer, and the
/ ledgers completed (identities scored, while remembered) and expired
/ (events given up on, each with the reason).
.qetl.job.stream.horizons.installed:`pending`history`publish`now`on_batch`score_ready`on_timer`completed`expired

/ The readiness rules: `wall`, an event is ready once horizon has elapsed
/ since its event_time; `reference`, once every reference key it needs has an
/ as-of anchor at or before event_time - lookback and has advanced to
/ event_time + horizon; `both` (#1395), only when both hold - the horizon has
/ elapsed on the plant clock AND the reference covers it. `reference` alone can
/ be satisfied by a reference row stamped far ahead of the plant's clock, and
/ `wall` alone by an hour of silence; a job that must not score before either
/ has happened declares both.
.qetl.job.stream.horizons.ready_rules:`wall`reference`both

/ Private: does this readiness rule read the reference's coverage (anchor and
/ advance), and so need an as-of anchor kept and a legs/by it can resolve?
/ @private
.qetl.job.stream.horizons.reads_reference:{[rule] rule in `reference`both}

/ Private: the declaration with every default filled in, `by` and identity
/ normalised to lists - or a refusal naming what is wrong. Apart from define
/ for size alone: one function holding every check exceeds what q, and the
/ coverage instrumenter, allow a lambda.
/ @private
.qetl.job.stream.horizons.normalise:{[who;decl;ins]
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
    if[not $[-16h=type d`lateness; 0D<=d`lateness; 0b];
        'who,"'s lateness must be a timespan, how much older than its arrival an event's event_time can be"];
    if[not (d`ready_on) in .qetl.job.stream.horizons.ready_rules;
        'who,"'s ready_on must be one of ",", " sv string .qetl.job.stream.horizons.ready_rules];
    if[`legs in key d; .qetl.declared.require_callable[who;`legs;d`legs;"a function of the pending events"]];
    if[(`legs in key d) and not .qetl.job.stream.horizons.reads_reference[d`ready_on];
        'who," declares legs, which only ready_on `reference reads (or `both)"];
    if[.qetl.job.stream.horizons.reads_reference[d`ready_on];
        if[1<>count key_cols; 'who,"'s ready_on `",string[d`ready_on]," needs a one-column by - the key its legs name"];
        if[(not `legs in key d) and not (first key_cols) in ev_cols;
            'who,"'s events carry no ",string[first key_cols],", so each event's reference key must come from legs"]];
    / retain_by (#1395): the columns that identify one as-of series in the
    / reference. `by` is the key an event WAITS on (a leg); a reference with
    / several independent sources per key needs the latest pre-cutoff row of
    / EACH source kept, so retention groups by `by` plus the source.
    rb:$[`retain_by in key decl; (),decl`retain_by; key_cols];
    if[not 11h=type rb; 'who,"'s retain_by must be reference column names, symbols"];
    if[count rb except ref_cols;
        'who,"'s retain_by names ",(", " sv string rb except ref_cols),", which the reference input does not carry"];
    if[count key_cols except rb;
        'who,"'s retain_by must include by (",(", " sv string key_cols),") - a series is a source's rows for one key"];
    d[`retain_by]:rb;
    if[`identity in key d;
        idc:(),d`identity;
        if[not 11h=type idc; 'who,"'s identity must be event column names, symbols"];
        if[count idc except ev_cols;
            'who,"'s identity names ",(", " sv string idc except ev_cols),", which the events input does not carry"];
        d[`identity]:idc];
    if[(`remember in key decl) and not `identity in key d; 'who," declares remember without identity - nothing would be remembered"];
    if[not $[-16h=type d`remember; 0D<d`remember; 0b]; 'who,"'s remember must be a positive timespan"];
    if[(`shared_reference in key d) and not -1h=type d`shared_reference;
        'who,"'s shared_reference must be a boolean: 1b to keep one reference buffer with the other jobs of the process that read the same table"];
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
/   outputs (a one-table symbol list: the table it publishes), horizon (how long an event waits, a
/   timespan), period (the timer); lateness (a timespan, see the header; optional
/   only when event_time is `time); optionally max_age (a timespan, see the
/   header), procname (default `<name>1), by (the reference's key columns, default `sym), keep (a function
/   of a batch returning which rows to buffer), the passthrough options of .qetl.job.stream.define
/   (start_with_all, note, on_start, check with on_fail),
/   event_time and reference_time (the columns each table's clock is read
/   from, default `time - e.g. `source_time), lookback (how far BEFORE an
/   event its window reaches, default 0D), ready_on (`wall, the default;
/   `reference; or `both: the horizon has elapsed AND the reference covers it),
/   retain_by (reference columns naming one as-of series, default `by`; must
/   include it), legs (a function of the pending events giving each one's
/   reference keys, for ready_on `reference or `both; default its own `by` value),
/   identity (event columns naming one event: a redelivery replaces the
/   pending one, and one already scored is dropped), remember (how long a
/   scored identity is remembered, default 0D01) and expire_after (how long
/   after its event_time an event that is still not ready is given up on)
/ @return the name
/ @throws error naming every problem it finds first
.qetl.job.stream.horizons.define:{[name;decl]
    who:"define: horizon job ",string[name];
    if[not 99h=type decl; 'who,"'s declaration must be a dictionary"];
    .qetl.job.stream.refuse_publishes[who;decl];
    decl:.qetl.job.stream.with_lateness[who;decl];
    .qetl.declared.refuse_unknown[who;"a horizon job";decl;.qetl.job.stream.horizons.required_keys,.qetl.job.stream.horizons.optional_keys];
    missing:.qetl.job.stream.horizons.required_keys where not .qetl.job.stream.horizons.required_keys in key decl;
    if[count missing; 'who," is missing ",", " sv string missing];
    if[not all -11h=type each decl`events`reference`transform;
        'who,"'s events, reference, transform must each be one table or transform name, a symbol"];
    if[not $[11h=type decl`outputs; 1=count decl`outputs; 0b];
        'who,"'s outputs must be a symbol list of exactly one table - the table it publishes, e.g. enlist `my_table"];
    if[decl[`events]=decl`reference;
        'who," needs distinct events and reference tables"];
    / $[], not `and`: q's `and` evaluates both sides, so a mistyped value
    / would throw a bare 'type before the message naming it
    if[not $[-16h=type decl`horizon; 0D<decl`horizon; 0b];
        'who,"'s horizon must be a positive timespan - how long an event waits before it is scored"];
    if[$[`max_age in key decl; not $[-16h=type decl`max_age; 0D<=decl`max_age; 0b]; 0b];
        'who,"'s max_age must be a timespan, the oldest a reference row may be at a horizon and still count"];
    ins:.qetl.transform.def[who;decl`transform]`inputs;
    if[2<>count ins;
        'who,"'s transform ",string[decl`transform]," takes ",string[count ins]," inputs - it takes two: the events, then the reference"];
    d:.qetl.job.stream.horizons.normalise[who;decl;ins];
    if[`keep in key decl; .qetl.declared.require_callable[who;`keep;decl`keep;"a function of a batch"]];
    ns:.qetl.job.stream.namespace name;
    / the state and the surface every job has, in the one place a test, a
    / reader or the runner looks for them
    ev0:0#first value ins;
    (` sv ns,`pending) set ev0;
    (` sv ns,`history) set 0#last value ins;
    / only the shape is checked here: the job joins the buffer once nothing can refuse it
    / (#2425), or a refused one would stay a member every reference batch then looked up
    if[.qetl.job.stream.horizons.is_shared[d]; .qetl.job.stream.horizons.shared.check[d`reference;name;0#last value ins]];
    / Plain typed columns, built by update: a symbol id and a symbol reason
    / rather than joined tables and general columns, which PeachQ types
    / differently from KDB-X.
    (` sv ns,`completed) set ([] id:`symbol$(); at:`timestamp$());
    (` sv ns,`expired) set update expired_at:`timestamp$(), reason:`symbol$() from ev0;
    (` sv ns,`publish) set .qetl.job.stream.unwired name;
    / .z.p, not .proc.cp[]: .u.upd stamps every row with the plant's .z.p, so
    / the times compared against are UTC, and .proc.cp[] is local time under
    / TorQ's -localtime. A function, so a test can stand a fixed time in.
    (` sv ns,`now) set {[] .z.p};
    (` sv ns,`on_batch) set .qetl.job.stream.horizons.on_batch[name;;];
    (` sv ns,`score_ready) set .qetl.job.stream.horizons.score_ready[name;];
    / a projection with its tick argument still open: applied in full
    / here, `{[ns] ...}[ns]` would run once, now, rather than per tick
    (` sv ns,`on_timer) set {[ns;tick] (get ` sv ns,`score_ready) (get ` sv ns,`now)[]}[ns]; / noqa: QF016 on_timer callback signature
    / the buffers and ledgers, so `reset` empties exactly what define made (#967)
    extra:((.qetl.job.stream.passthrough decl),(enlist[`ephemeral] inter key decl)#decl),enlist[`state]!enlist `pending`history`completed`expired;
    .qetl.job.stream.define[name;(`procname`subscribe_to`outputs`on_batch`period`on_timer`transform!(
        .qetl.job.procname_of[name;decl];
        decl`events`reference;
        decl`outputs;
        get ` sv ns,`on_batch;
        decl`period;
        get ` sv ns,`on_timer;
        decl`transform)),extra];
    / registered only once the streaming job is: a refusal there (a procname
    / another job runs, say) must not leave a horizon job behind, nor a member
    / of a shared buffer
    if[.qetl.job.stream.horizons.is_shared[d]; .qetl.job.stream.horizons.shared.join[d`reference;name;0#last value ins]];
    .qetl.job.stream.horizons.registry[name]:enlist d;
    name}

/ Does this job share its reference buffer with the other jobs of the process that read the same table?
/ Declared with `shared_reference` (horizon_shared.q).
/ @param d the job's declaration
/ @return a boolean
/ @eg .qetl.job.stream.horizons.is_shared enlist[`shared_reference]!enlist 1b  ->  1b
.qetl.job.stream.horizons.is_shared:{[d] $[`shared_reference in key d; d`shared_reference; 0b]}

/ One horizon job's declaration, or a refusal naming it.
/ @param name the job
/ @return the declaration dict, with every default filled in
/ @throws error when nothing was defined under that name
/ @eg .qetl.job.stream.horizons.def[`crypto_markout]`horizon  ->  0D00:00:10
.qetl.job.stream.horizons.def:{[name]
    if[not name in key .qetl.job.stream.horizons.registry;
        '"def: ",string[name]," is not a defined horizon job - defined: ",", " sv string key .qetl.job.stream.horizons.registry];
    first .qetl.job.stream.horizons.registry name}

/ Every defined horizon job.
/ @return a symbol vector
/ @eg `crypto_markout in .qetl.job.stream.horizons.defined[]  ->  1b
.qetl.job.stream.horizons.defined:{[] key .qetl.job.stream.horizons.registry}

/ Private: buffer one batch - events into `pending`, reference rows into
/ `history` - projected onto the transform's input columns, after `keep`.
/ With identity, an event already scored (and still remembered) is dropped,
/ and a redelivery REPLACES the pending one - the last delivery wins, within
/ a batch too. A batch on any other table is dropped: the plant sends only
/ what was subscribed to, so that is a hand call or a test. An event with no
/ event_time has no horizon, so it is never buffered: it goes to `expired`
/ with reason `no_event_time, and is logged, as rolling drops one (#2424).
/ @private
.qetl.job.stream.horizons.on_batch:{[name;t;x]
    d:.qetl.job.stream.horizons.def[name];
    which:$[t=d`events; `pending; t=d`reference; `history; `];
    if[null which; :()];
    / one buffer, appended once for every member that reads it (horizon_shared.q)
    if[(which=`history) and .qetl.job.stream.horizons.is_shared d; .qetl.job.stream.horizons.shared.add[name;x]; :()];
    if[`keep in key d; x:x where (d`keep) x];
    if[0=count x; :()];
    ns:.qetl.job.stream.namespace name;
    buffer:` sv ns,which;
    x:(cols get buffer)#x;
    if[which=`pending; x:.qetl.job.stream.horizons.refuse_untimed[name;d;ns;x]];
    if[0=count x; :()];
    if[(which=`pending) and `identity in key d;
        ids:.qetl.job.stream.horizons.ids_of[d;x];
        keep:(not ids in (get ` sv ns,`completed)`id) & .qetl.io.last_seen ids;
        x:x where keep;
        if[0=count x; :()];
        held:get buffer;
        buffer set held where not .qetl.job.stream.horizons.ids_of[d;held] in ids where keep];
    buffer insert x;
    }

/ Private: the events of a batch that carry an event_time; the rest go to `expired`
/ with reason `no_event_time, logged once for the batch (#2424).
/ @private
.qetl.job.stream.horizons.refuse_untimed:{[name;d;ns;x]
    untimed:null x d`event_time;
    if[not any untimed; :x];
    gone:x where untimed;
    (` sv ns,`expired) insert update expired_at:(get ` sv ns,`now)[], reason:`no_event_time from gone;
    .qetl.log.ch.warn[`transform;"event dropped - it has no event time, so no horizon";(enlist[`transform]!enlist name),enlist[`rows]!enlist count gone];
    x where not untimed}

/ Private: one symbol per event, from its identity columns - so membership
/ and last-delivery-wins are vector operations on symbols.
/ Encode the typed tuple losslessly: display strings can round numbers, and
/ delimiter-joining fields merges (`a|b;`c) with (`a;`b|c).
/ @private
.qetl.job.stream.horizons.ids_of:{[d;t]
    idc:d`identity;
    `${raze string -8!value x} each idc#t}

/ Private: score every event that is ready by `now`, publish the rows, then
/ evict them; give up on events past expire_after, with the reason; and,
/ every tick, drop the reference rows and remembered identities nothing
/ pending can still need. Scoring and publishing come BEFORE anything is
/ evicted or remembered, so a transform or a publish that throws leaves
/ every event queued for the next tick.
/ @private
.qetl.job.stream.horizons.score_ready:{[name;now]
    d:.qetl.job.stream.horizons.def[name];
    ns:.qetl.job.stream.namespace name;
    pq:` sv ns,`pending; hq:` sv ns,`history; cq:` sv ns,`completed; xq:` sv ns,`expired;
    pending:get pq;
    et:pending d`event_time;
    why:.qetl.job.stream.horizons.why_not_ready[d;get hq;pending;now];
    mask:0=count each why;
    if[any mask;
        ins:key .qetl.transform.def["horizon job ",string name;d`transform]`inputs;
        ready:pending where mask;
        out:.qetl.transform.apply[d`transform;ins!(ready;get hq)];
        (get ` sv ns,`publish)[first d`outputs;out];
        if[`identity in key d; cq insert ([] id:.qetl.job.stream.horizons.ids_of[d;ready]; at:(count ready)#now)];
        .qetl.job.stream.evict[pq;mask]];
    if[`expire_after in key d;
        pending:get pq;
        et:pending d`event_time;
        lapsed:et<=now-d`expire_after;
        if[any lapsed;
            gone:pending where lapsed;
            reasons:$[.qetl.job.stream.horizons.reads_reference[d`ready_on];
                .qetl.job.stream.horizons.why_not_ready[d;get hq;gone;now];
                (count gone)#enlist "horizon has not elapsed"];
            xq insert update expired_at:now, reason:`$reasons from gone;
            {[name;r] .qetl.log.ch.warn[`transform;"event expired unresolved";(enlist[`transform]!enlist name),enlist[`reason]!enlist r]}[name] each distinct reasons;
            .qetl.job.stream.evict[pq;lapsed]]];
    / every tick, scoring or not: a job whose events stop arriving must not
    / keep every reference row it is sent
    left:(get pq) d`event_time;
    earliest:.qetl.job.stream.horizons.earliest_need[d;left;now];
    / a shared buffer is trimmed to what every member can still need (horizon_shared.q)
    $[.qetl.job.stream.horizons.is_shared d;
        .qetl.job.stream.horizons.shared.trim[name;now];
        hq set .qetl.job.stream.horizons.retain[d;get hq;earliest-d`lookback]];
    if[`identity in key d; cq set (get cq) where (get cq)[`at]>=now-d`remember];
    }

\d .

/ Declare a streaming job that scores each event once its horizon has passed.
/ @param name the job name
/ @param decl events, reference, transform, outputs, horizon, period and the process declaration
/ @return the registered streaming job name
/ @throws error when the transform is not a two-input contract for the declared tables
.qetl.job.stream.at_horizons:{[name;decl] .qetl.job.stream.horizons.define[name;decl]}

\d .
