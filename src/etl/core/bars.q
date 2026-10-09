/ bars.q - the job kind that aggregates a stream into fixed-width time windows
/ (.qetl.job.stream.bars, #946): OHLC, VWAP and volume per interval.
/ .
/ WHY A KIND. Time bars are the commonest derived table on a desk, and a
/ hand-written one meets the same three problems every time: a row that
/ arrives after its window should have closed, the last window of the day
/ that nothing closes, and a restart that must neither lose the open windows
/ nor publish the closed ones again. The shell owns those; an instance
/ declares what differs - the input table, the grouping, the width, how late
/ a row may be, and the aggregation.
/ .
/ THE TRANSFORM IS THE AGGREGATION, and the only thing a backfill twin shares.
/ It is a declared .qetl.transform with ONE input: the input table's rows
/ with a `bar_start` column the shell adds (assign), one row per group per
/ window out, carrying the grouping columns and `bar_start`. Rows reach it in
/ arrival order and it must not depend on that: a late row amends a window
/ that is still open, so open/close are by event time.
/ .
/ WINDOWS ARE HALF-OPEN, [bar_start, bar_start+width), on the event time: a
/ row exactly at a boundary belongs to the window that STARTS there. A window
/ with no rows has no bar - nothing is published for an empty interval.
/ .
/ LATENESS. A window closes, and its bar is published once, when its end plus
/ the allowed lateness has passed on the process clock. A row that arrives
/ before that amends the still-open window; one that arrives after is dropped
/ and logged (the `dropped` ledger), because the bar it belongs to is out. A
/ window is also closed at end of day (on_endofday, #943), for every window of
/ the day that ended, whatever its lateness: the open window must not wait for
/ a tick that, at the day's roll, belongs to the next day.
/ .
/ REPLAY. The job subscribes to its own output as well and replays with it
/ (restore_from, #943): a replayed bar evicts its window's rows, so a restart
/ re-buffers the windows that were open and publishes none that were closed.
/ During a replay the process clock is not consulted - the log's rows are old
/ by then - only whether the log already holds the window's bar.
/ .
/ PUBLISH BEFORE EVICT. The runner's safe timer swallows an error, so a
/ publish that throws leaves the windows buffered for the next tick.
/ .
/ REGISTERED AS A STREAMING JOB: define performs .qetl.job.stream.define
/ itself, so an instance cannot declare its edges apart from its contract.

\d .qetl.job.stream.bars

/ name -> its declaration, ENLISTED, for the reason .qetl.job.stream.jobs enlists.
registry:(`symbol$())!();

/ What every bars job declares.
required_keys:`procname`events`transform`publishes`width`lateness`period

/ What it may declare besides: the grouping columns (default `sym) and the
/ column the window is read from (default `time).
optional_keys:`by`event_time`start_with_all`note

/ What define installs in .qpipe.job.<name>: pending (rows of windows still
/ open), closed (windows published, until their lateness has passed),
/ dropped (late rows, with the reason), publish (the seam), now (the clock),
/ on_batch, close_ready[now], on_timer and on_endofday[date].
installed:`pending`closed`dropped`publish`now`on_batch`close_ready`on_timer`on_endofday

/ The window start of each row: its event time floored to a multiple of width.
/ The twin's transform calls this too, so a window is cut one way.
/ @param width the bar width, a timespan
/ @param tc the event-time column
/ @param x a table carrying tc
/ @return x with a bar_start column
/ @eg (.qetl.job.stream.bars.assign[0D00:01;`t;([] t:enlist 2026.10.09D10:00:59)])`bar_start  ->  ,2026.10.09D10:00:00
assign:{[width;tc;x]
    / xbar, not `div`: PeachQ divides longs through floats, which at a
    / nanosecond timestamp's magnitude moves a row across a window boundary
    update bar_start:width xbar x tc from x}

/ Declare a job that aggregates a stream into time bars.
/ @param name the job's name
/ @param decl dict of procname, events (the table aggregated), transform (one
/   input, the events plus bar_start), publishes (the bar table), width and
/   lateness (timespans, lateness may be 0D), period (the timer, how often
/   closed windows are looked for); optionally by (grouping columns, default
/   `sym), event_time (default `time), start_with_all, note
/ @return the name
/ @throws error naming the first problem found
define:{[name;decl]
    who:"define: bars job ",string[name];
    if[not 99h=type decl; 'who,"'s declaration must be a dictionary"];
    missing:required_keys where not required_keys in key decl;
    if[count missing; 'who," is missing ",", " sv string missing];
    unknown:(key decl) except required_keys,optional_keys;
    if[count unknown; 'who," declares ",(", " sv string unknown),", which a bars job does not take"];
    if[not all -11h=type each decl`events`transform`publishes;
        'who,"'s events, transform and publishes must each be one table or transform name, a symbol"];
    if[not $[-16h=type decl`width; 0D<decl`width; 0b];
        'who,"'s width must be a positive timespan"];
    if[not $[-16h=type decl`lateness; 0D<=decl`lateness; 0b];
        'who,"'s lateness must be a timespan, 0D for none"];
    if[not (decl`transform) in .qetl.transform.defined[];
        'who,"'s transform ",string[decl`transform]," is not registered - it is the job's contract, so it is declared first"];
    t:.qetl.transform.def decl`transform;
    if[1<>count t`inputs; 'who,"'s transform takes ",string[count t`inputs]," inputs - it takes one: the events plus bar_start"];
    d:`by`event_time!(enlist `sym;`time);
    d:d,decl,enlist[`by]!enlist (),$[`by in key decl; decl`by; `sym];
    in_cols:cols first value t`inputs;
    needs:distinct d[`by],(d`event_time),`bar_start;
    if[count lacks:needs except in_cols;
        'who,"'s transform input carries no ",(", " sv string lacks)];
    if[count lacks:(d[`by],`bar_start) except cols t`output;
        'who,"'s transform output carries no ",(", " sv string lacks)," - they name the window the bar closes"];
    d[`input]:first key t`inputs;
    ns:.qetl.job.stream.namespace name;
    ev0:0#first value t`inputs;
    (` sv ns,`pending) set ev0;
    (` sv ns,`closed) set (d[`by],`bar_start)#ev0;
    (` sv ns,`dropped) set update reason:`symbol$() from ev0;
    (` sv ns,`publish) set .qetl.job.stream.unwired name;
    / .z.p, as horizon.q: .u.upd stamps rows in UTC. A function, so a test can stand a time in.
    (` sv ns,`now) set {[] .z.p};
    (` sv ns,`on_batch) set on_batch[name;;];
    (` sv ns,`close_ready) set close_ready[name;];
    (` sv ns,`on_endofday) set end_of_day[name;];
    (` sv ns,`on_timer) set {[ns;tick] (get ` sv ns,`close_ready) (get ` sv ns,`now)[]}[ns];
    extra:(`start_with_all`note inter key decl)#decl;
    .qetl.job.stream.define[name;(`procname`subscribe_to`publishes`on_batch`period`on_timer`transform`on_endofday`replay`restore_from!(
        decl`procname;
        enlist decl`events;
        enlist decl`publishes;
        get ` sv ns,`on_batch;
        decl`period;
        get ` sv ns,`on_timer;
        decl`transform;
        get ` sv ns,`on_endofday;
        1b;
        enlist decl`publishes)),extra];
    registry[name]:enlist d;
    name}

/ One bars job's declaration, or a refusal naming it.
/ @param name the job
/ @return the declaration dict, with its defaults filled in
/ @throws error when nothing was defined under that name
/ @eg .qetl.job.stream.bars.def[`exec_bars]`width  ->  0D00:01:00.000000000
def:{[name]
    if[not name in key registry;
        '"def: ",string[name]," is not a defined bars job - defined: ",", " sv string key registry];
    first registry name}

/ Every defined bars job.
/ @return a symbol vector
/ @eg `exec_bars in .qetl.job.stream.bars.defined[]  ->  1b
defined:{[] key registry}

/ Private: a table's key columns as a list of rows, so membership is one `in`.
/ @private
wkey:{[cs;t] flip value flip cs#t}

/ Private: buffer one batch of events - cut to the window each row belongs to,
/ and refused when that window is already out - or, for the bar table, record
/ the windows it holds as published. A batch on any other table is ignored.
/ @private
on_batch:{[name;t;x]
    d:def name;
    ns:.qetl.job.stream.namespace name;
    ck:d[`by],`bar_start;
    if[t=d`publishes; :mark_published[ns;ck;x]];
    if[(t<>d`events) or 0=count x; :()];
    pq:` sv ns,`pending;
    x:assign[d`width;d`event_time;(cols[get pq] except `bar_start)#x];
    x:(cols get pq)#x;
    now:(get ` sv ns,`now)[];
    / in a replay every row is old: only the log's own bars say a window is out
    stale:$[.qetl.job.stream.replaying; 0b; (x[`bar_start]+(d`width)+d`lateness)<=now];
    out:stale | wkey[ck;x] in wkey[ck;get ` sv ns,`closed];
    if[any out;
        gone:x where out;
        (` sv ns,`dropped) set -1000 sublist (get ` sv ns,`dropped),update reason:`late from gone;
        .qetl.log.warn[name;"late row dropped - its bar is already out";`rows`first_window!(count gone;min gone`bar_start)]];
    pq insert x where not out;
    }

/ Private: windows the log or the plant says are published: evict their
/ rows (a restart re-buffered them) and remember them as closed.
/ @private
mark_published:{[ns;ck;x]
    if[0=count x; :()];
    pq:` sv ns,`pending; cq:` sv ns,`closed;
    .qetl.job.stream.evict[pq;wkey[ck;get pq] in wkey[ck;x]];
    new:distinct (ck#x) where not wkey[ck;x] in wkey[ck;get cq];
    cq insert new;
    }

/ Private: aggregate, publish and then evict the pending rows `pick` selects -
/ whole windows, so a window is never published twice - and remember them as
/ closed.
/ @private
close_windows:{[name;pick]
    d:def name;
    ns:.qetl.job.stream.namespace name;
    pq:` sv ns,`pending; ck:d[`by],`bar_start;
    mask:pick get pq;
    if[not any mask; :0];
    ready:(get pq) where mask;
    out:.qetl.transform.apply[d`transform;enlist[d`input]!enlist ready];
    if[count out; (get ` sv ns,`publish)[d`publishes;out]];
    (` sv ns,`closed) insert distinct ck#ready;
    .qetl.job.stream.evict[pq;mask];
    count out}

/ Close and publish every window whose end plus the lateness has passed by
/ `now`; then forget closed windows whose lateness has passed, since a row for
/ one is refused by the clock alone.
/ @param name the job
/ @param now the time to close by
/ @return the number of bars published
/ @eg .qpipe.job.exec_bars.close_ready 2026.10.09D10:01:30
close_ready:{[name;now]
    d:def name;
    ns:.qetl.job.stream.namespace name; cq:` sv ns,`closed;
    n:close_windows[name;{[w;l;now;p] (p[`bar_start]+w+l)<=now}[d`width;d`lateness;now]];
    cq set (get cq) where (((get cq)`bar_start)+(d`width)+d`lateness)>now;
    n}

/ Close every window of the day that ended, whatever its lateness (#943).
/ @param name the job
/ @param dt the date that ended
/ @return the number of bars published
/ @eg .qpipe.job.exec_bars.on_endofday 2026.10.09
end_of_day:{[name;dt]
    close_windows[name;{[before;p] p[`bar_start]<before}[`timestamp$dt+1]]}

\d .qetl.job.stream

/ Declare a streaming job that aggregates its input into time bars.
/ @param name the job name
/ @param decl events, transform, publishes, width, lateness, period and the process declaration
/ @return the registered streaming job name
/ @throws error when the transform is not a one-input contract carrying the window
at_bars:{[name;decl] .qetl.job.stream.bars.define[name;decl]}

\d .
