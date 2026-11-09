/ horizon_reference.q - how a horizon job reads and keeps its reference rows (.qetl.job.stream.horizons, #945).
/ .
/ Split out of horizon.q, in the same namespace: what a reference key has reached, which events are
/ ready under the job's rule, and which reference rows are still of use. The shell (horizon.q) defines the
/ job and runs its batches and ticks; this file is the reference's side of them. Loaded straight after it.

\d .

/ Private: per reference key, the earliest and latest reference time held -
/ the anchor and how far the reference has advanced - and `ser`, the latest
/ reference time of EACH series of the key (a series is the retain_by columns:
/ a source's rows for the key, #1523). `hi` is the best of the series, so it
/ says the key has advanced when ONE source has; `ser` says whether every
/ source has.
/ @private
.qetl.job.stream.horizons.reach:{[d;hist]
    k:hist first d`by; rt:hist d`reference_time;
    g:group k;
    sg:group d[`retain_by]#hist;
    sk:(key sg) first d`by;
    sh:max each rt value sg;
    `lo`hi`ser!(min each rt g;max each rt g;(distinct sk)!{[sk;sh;k] sh where sk=k}[sk;sh] each distinct sk)}

/ Private: each pending event's reference keys - its legs, or its own key.
/ @private
.qetl.job.stream.horizons.legs_of:{[d;pending] $[`legs in key d; (d`legs) pending; enlist each pending first d`by]}

/ Private: why each event is not ready under `reference - "" when it is.
/ An event is ready when every key it needs has an anchor at or before
/ event_time and has advanced to event_time + horizon. Two requirements wait
/ only until the wall-clock grace event_time + horizon + lateness, after
/ which nothing stamped earlier can still arrive, so a source that never
/ delivers cannot hold an event for ever:
/   - the reach back to event_time - lookback (#1524): books that start inside
/     the lookback leave the earlier, negative horizons unavailable, as the
/     backfill does, instead of dropping the whole event; an anchor at or
/     before event_time itself is still required
/   - every series of a key must have advanced to the horizon (#1523), not just
/     the best of them: a fast source passing the horizon says nothing of a
/     slower one still in flight. Every series held, not only those whose newest
/     book is within max_age of the horizon (#2455): a feed running further
/     behind than max_age still has books stamped inside it in flight. A source
/     that has stopped is held only until the grace, and only while retain keeps
/     a book of it
/ @private
.qetl.job.stream.horizons.not_ready:{[d;r;et;legs;now]
    lo:r`lo; hi:r`hi; ser:r`ser;
    {[d;lo;hi;ser;now;t;ks]
        ks:(),ks;
        absent:ks where not ks in key lo;
        if[count absent; :"no reference for ",", " sv string absent];
        grace:now>=t+d[`horizon]+d`lateness;
        need:$[grace; t; t-d`lookback];
        early:ks where lo[ks]>need;
        if[count early; :"no ",string[d`reference_time]," at or before ",string[need]," for ",", " sv string early];
        short:ks where hi[ks]<t+d`horizon;
        if[count short; :"reference has not reached ",string[t+d`horizon]," for ",", " sv string short];
        behind:$[grace; `symbol$(); ks where {[h;s] any s<h}[t+d`horizon] each ser ks];
        if[count behind; :"a source has not reached ",string[t+d`horizon]," for ",", " sv string behind];
        ""}[d;lo;hi;ser;now]'[et;legs]}

/ Private: why each pending event is not ready under the job's rule - "" when
/ it is. `wall` reads the plant clock, `reference` the reference's coverage,
/ `both` the wall first (it is the cheaper to explain: nothing can have
/ arrived yet) and then the coverage.
/ @private
.qetl.job.stream.horizons.why_not_ready:{[d;hist;pending;now]
    et:pending d`event_time;
    wall:{$[x;"";"horizon has not elapsed"]} each et<=now-d`horizon;
    if[`wall=d`ready_on; :wall];
    cover:.qetl.job.stream.horizons.not_ready[d;.qetl.job.stream.horizons.reach[d;hist];et;.qetl.job.stream.horizons.legs_of[d;pending];now];
    $[`reference=d`ready_on; cover; {$[count x;x;y]}'[wall;cover]]}

/ Private: the as-of-safe reference rows to keep, given the earliest instant
/ any pending event can still ask about (see the header).
/ @private
.qetl.job.stream.horizons.retain:{[d;hist;earliest]
    rt:hist d`reference_time;
    / 0Wn is "as of however old" (fx_markout's spec), the same as no max_age:
    / earliest-0Wn is the 1734 floor, which would evict nothing for ever (#1480)
    aged:$[`max_age in key d; d[`max_age]<0Wn; 0b];
    cutoff:$[aged; earliest-d`max_age; earliest];
    old:where rt<cutoff;
    if[0=count old; :hist];
    / with max_age a row below the cutoff is never an answer - except as the
    / anchor ready_on `reference needs (#959)
    if[aged and not .qetl.job.stream.horizons.reads_reference[d`ready_on]; :hist where rt>=cutoff];
    / the latest pre-cutoff row per key is still an as-of answer
    / by reference time, not arrival: out-of-order rows must not evict the true as-of row (#1283)
    o:old iasc rt old;
    / a series' anchor is only an ANSWER while it is within max_age; with max_age the
    / only anchor still needed is readiness's, one per `by` key, so a source that
    / stopped quoting does not keep its last book for ever (#1410)
    series:$[aged; (),d`by; d`retain_by];
    latest:o last each value group series#hist o;
    hist asc (til[count hist] except old),latest}

/ The earliest instant any of a job's pending events can still ask about, before its lookback: the oldest
/ pending event time, or, with nothing pending, one horizon before now; moved back by the job's lateness, since
/ a late event may be older than everything pending but not than now less the lateness. What a trim keeps
/ reference rows from.
/ @param d the job's declaration
/ @param left the event times of the events still pending
/ @param now the time it is judged at
/ @return a timestamp
/ @eg .qetl.job.stream.horizons.earliest_need[`horizon`lateness!(0D00:00:10;0D);2026.09.17D10:00:00 2026.09.17D10:00:05;2026.09.17D10:01:00]  ->  2026.09.17D10:00:00.000000000
.qetl.job.stream.horizons.earliest_need:{[d;left;now]
    earliest:$[count left; min left; now-d`horizon];
    if[d[`lateness]>0D; earliest:(earliest&now)-d`lateness];
    earliest}

\d .
