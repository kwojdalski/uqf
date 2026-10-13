/ rolling.q - the job kind that keeps a statistic over the last N minutes up
/ to date (.qetl.job.stream.rolling, #1224): per-key rates, volumes and
/ ratios "right now", at several window lengths at once.
/ .
/ WHY A KIND. Bars (bars.q) cut time into fixed, non-overlapping windows, each
/ published once. A desk also asks "over the last five minutes, as of now":
/ a venue's reject rate, a client's volume, a short-horizon vol. That is a
/ different lifecycle - the window slides, every publication is a fresh
/ snapshot, a row is in many of them - and each hand-written one would carry
/ the same problems: what is in the window exactly, how old a row may be and
/ still count, what a quiet key publishes, and what a restart rebuilds. The
/ shell owns those; an instance declares what differs - the input table, the
/ grouping, the window lengths, the lateness, the period and the statistic.
/ .
/ THE TRANSFORM IS THE STATISTIC, and the only thing a backfill twin shares.
/ It is a declared .qetl.transform with ONE input: the input table's rows with
/ three columns the shell adds - `window` (a timespan, the window length the
/ row is offered to), `as_of` (the instant the windows end at) and `inside`
/ (whether the row is in that window). The input holds every row of the
/ longest window once per window length, so a key that traded in the last hour
/ appears for the 1m window too, with `inside` false on its older rows. It
/ returns one row per key per window length, carrying the grouping columns,
/ `window` and `as_of`. It must read only the rows with `inside`; the others
/ are there so a key with nothing in a short window is still a row, and it
/ publishes the statistic's identity (0 for a count or a sum, null for a mean
/ or a ratio, as .qexec.fill_ratio does) - documented per statistic, in the
/ transform's example.
/ .
/ WINDOWS ARE (as_of-N, as_of] ON THE EVENT TIME: a row exactly N old is OUT, a
/ row exactly at as_of is IN. Half-open on the left because the windows of two
/ consecutive publications then tile - a row is in the window ending at as_of
/ or in the next, never both of two windows that abut. (Bars are the other
/ way, [start, end), because a bar is named by its start.)
/ .
/ PUBLICATION. Every `period`, at as_of = the clock floored to a multiple of
/ the period - a grid, so a restart and a backfill twin cut the same instants.
/ A tick whose grid point was already published (a second tick in the same
/ period, or a restart that finds it in the log) publishes nothing. A key is
/ published only if it had a row within the LONGEST window, so the key set is
/ bounded by the buffer and never grows with the days the process has run.
/ .
/ LATENESS. A row whose receipt is more than `lateness` after its event time
/ is dropped and logged (the `dropped` ledger). A row within it is buffered
/ and is part of the next publication it falls in the window of: it AMENDS
/ the series, since nothing already published is republished. The receipt is
/ the log clock (.qetl.job.stream.clock_of): the process clock live, the
/ plant's `time` stamp on replay, so a row is late exactly when it was live.
/ When the event time IS the plant's `time` stamp there is no distinct
/ receipt and no row can be late: it is stamped as it arrives. A row with a
/ null event time is dropped too (reason `no_event_time`), since it is in no
/ window.
/ .
/ EVICTION. A row is kept while it is newer than the longest window plus the
/ lateness behind the clock; older, it can be in no future window, and goes.
/ The buffer is therefore bounded by that span of input, however long the
/ process runs, and so is a replay (its clock is the log's).
/ .
/ COST. A publication re-aggregates the buffered rows once per window length:
/ O(W x R) with W window lengths and R rows in the longest window, every
/ period. That is nothing at desk volumes (thousands of rows an hour). Running
/ sums per key would make it O(rows arrived) but give up the one statistic
/ for another: a stdev, a median or a drawdown cannot be held as a sum, and a
/ transform that can be any of them is the contract here.
/ .
/ END OF DAY. A rolling window is not day-bucketed: at the plant's roll the
/ buffer is kept and the series carries on, so a 1h window at 00:30 holds the
/ previous day's last half hour. Nothing is published at the roll; there is
/ no day to close. The cost is on RESTART: the plant's log is per day, so a
/ replay after midnight holds only today's rows. The job records `covered_from`,
/ the plant's local midnight before the first clock it saw (the log begins at the roll),
/ and publishes a window length only when its whole span is covered - a 1h
/ window restarted at 00:30 is withheld until 01:00, logged when the set of
/ withheld windows changes, rather than publishing a rate over half the hour.
/ An uninterrupted job crossing midnight has no such gap. Under-claiming, not
/ over-claiming, is the rule the coverage ledger follows too.
/ .
/ RESTART. The job replays its input and its own output (restore_from). A
/ replayed row is buffered under the same rules as a live one; a replayed
/ output row sets `last_as_of`, so the grid points already published are not
/ published again. Nothing is published during a replay. The first publication
/ after a restart is the one an uninterrupted job would make.
/ .
/ PUBLISH BEFORE ADVANCE. The runner's safe timer swallows an error, so a
/ publish that throws leaves `last_as_of` and the buffer untouched and the next
/ tick publishes the same grid point (or a later one).
/ .
/ REGISTERED AS A STREAMING JOB: define performs .qetl.job.stream.define
/ itself, so an instance cannot declare its edges apart from its contract.

\d .qetl.job.stream.rolling

/ name -> its declaration, ENLISTED, for the reason .qetl.job.stream.jobs enlists.
registry:(`symbol$())!();

/ What every rolling job declares.
required_keys:`procname`events`transform`publishes`windows`lateness`period

/ What it may declare besides: the grouping columns (default `sym) and the
/ column the window is read from (default `time).
optional_keys:`by`event_time,.qetl.job.stream.passthrough_keys

/ The columns the shell adds to the transform's input, with their types.
shell_columns:`window`as_of`inside!"npb"

/ What define installs in .qpipe.job.<name>: pending (the buffered rows),
/ dropped (late rows, with the reason), last_as_of (the newest grid point
/ published), covered_from (the instant the buffer is complete from),
/ withheld (the window lengths not yet covered), publish (the seam), now (the
/ clock), on_batch, evaluate[now], on_timer and on_endofday[date].
installed:`pending`dropped`last_as_of`covered_from`withheld`publish`now`on_batch`evaluate`on_timer`on_endofday

/ Declare a job that keeps a statistic over rolling windows.
/ @param name the job's name
/ @param decl dict of procname, events (the table read), transform (one input:
/   the events plus window, as_of and inside), publishes (the table written),
/   windows (the window lengths, timespans), lateness (a timespan, 0D for
/   none) and period (the timer, and the grid the windows end on); optionally
/   by (grouping columns, default `sym), event_time (default `time),
/   the passthrough options of .qetl.job.stream.define (start_with_all, note,
/   on_start, check with on_fail)
/ @return the name
/ @throws error naming the first problem found
define:{[name;decl]
    who:"define: rolling job ",string[name];
    if[not 99h=type decl; 'who,"'s declaration must be a dictionary"];
    missing:required_keys where not required_keys in key decl;
    if[count missing; 'who," is missing ",", " sv string missing];
    unknown:(key decl) except required_keys,optional_keys;
    if[count unknown; 'who," declares ",(", " sv string unknown),", which a rolling job does not take"];
    if[not all -11h=type each decl`events`transform`publishes;
        'who,"'s events, transform and publishes must each be one table or transform name, a symbol"];
    ws:(),decl`windows;
    if[not $[16h=type ws; $[count ws; all ws>0D; 0b]; 0b];
        'who,"'s windows must be timespans, each positive"];
    if[not $[-16h=type decl`lateness; 0D<=decl`lateness; 0b];
        'who,"'s lateness must be a timespan, 0D for none"];
    if[not $[-16h=type decl`period; 0D<decl`period; 0b];
        'who,"'s period must be a positive timespan"];
    if[not (decl`transform) in .qetl.transform.defined[];
        'who,"'s transform ",string[decl`transform]," is not registered - it is the job's contract, so it is declared first"];
    t:.qetl.transform.def decl`transform;
    if[1<>count t`inputs; 'who,"'s transform takes ",string[count t`inputs]," inputs - it takes one: the events plus window, as_of and inside"];
    d:`by`event_time!(enlist `sym;`time);
    d:d,decl,`by`windows!(((),$[`by in key decl; decl`by; `sym]);asc distinct ws);
    ev0:first value t`inputs;
    types:exec c!t from 0!meta ev0;
    if[count lacks:(key shell_columns) where not (key shell_columns) in key types;
        'who,"'s transform input carries no ",(", " sv string lacks)," - the shell adds them"];
    if[count bad:(key shell_columns) where not (value shell_columns)=types key shell_columns;
        'who,"'s transform input types ",(", " sv string bad)," wrongly - window is a timespan, as_of a timestamp, inside a boolean"];
    if[count clash:d[`by] inter key shell_columns; 'who,"'s by names ",(", " sv string clash),", which the shell adds"];
    if[count lacks:(distinct d[`by],d`event_time) except key types;
        'who,"'s transform input carries no ",(", " sv string lacks)];
    if[count lacks:(d[`by],`window`as_of) except cols t`output;
        'who,"'s transform output carries no ",(", " sv string lacks)," - they name the window the row describes"];
    d[`input]:first key t`inputs;
    d[`kept]:(cols ev0) except key shell_columns;
    ns:.qetl.job.stream.namespace name;
    (` sv ns,`pending) set (d`kept)#ev0;
    (` sv ns,`dropped) set update reason:`symbol$() from (d`kept)#ev0;
    (` sv ns,`last_as_of) set 0Np;
    (` sv ns,`covered_from) set 0Np;
    (` sv ns,`withheld) set `timespan$();
    (` sv ns,`publish) set .qetl.job.stream.unwired name;
    / .z.p, as bars.q: .u.upd stamps rows in UTC. A function, so a test can stand a time in.
    (` sv ns,`now) set {[] .z.p};
    (` sv ns,`on_batch) set on_batch[name;;];
    (` sv ns,`evaluate) set evaluate[name;];
    (` sv ns,`on_endofday) set end_of_day[name;];
    (` sv ns,`on_timer) set {[ns;tick] (get ` sv ns,`evaluate) (get ` sv ns,`now)[]}[ns]; / noqa: QF016 on_timer callback signature
    extra:(.qetl.job.stream.passthrough decl),enlist[`state]!enlist `pending`dropped`last_as_of`covered_from`withheld;
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

/ One rolling job's declaration, or a refusal naming it.
/ @param name the job
/ @return the declaration dict, with its defaults filled in
/ @throws error when nothing was defined under that name
/ @eg .qetl.job.stream.rolling.def[`order_quality]`windows  ->  0D00:01:00.000000000 0D00:05:00.000000000 0D01:00:00.000000000
def:{[name]
    if[not name in key registry;
        '"def: ",string[name]," is not a defined rolling job - defined: ",", " sv string key registry];
    first registry name}

/ Every defined rolling job.
/ @return a symbol vector
/ @eg `order_quality in .qetl.job.stream.rolling.defined[]  ->  1b
defined:{[] key registry}

/ The transform's input for one evaluation: the rows of the longest window,
/ once per window length in `lengths`, each tagged with its window, the
/ instant and whether the row is inside that window.
/ @param tc the event-time column
/ @param reach the longest declared window: rows older than as_of-reach are not offered
/ @param lengths the window lengths to offer them to
/ @param as_of the instant the windows end at
/ @param rows the buffered rows
/ @return the rows with window, as_of and inside appended; empty when no row is in reach
/ @private
tag:{[tc;reach;lengths;as_of;rows]
    et:rows tc;
    live:rows where (et>as_of-reach) and et<=as_of;
    if[0=count live; :live];
    one:{[tc;as_of;live;w]
        n:count live;
        live,'flip `window`as_of`inside!(n#w;n#as_of;(live tc)>as_of-w)}[tc;as_of;live];
    raze one each lengths}

/ The statistic of every key over every window length, as of one instant. Pure:
/ no clock, no buffer - the live job calls it on its buffer and a backfill twin
/ on rows read from the HDB, so both cut the window one way.
/ @param name the job
/ @param rows the input rows (at least the transform's input columns)
/ @param as_of the instant the windows end at
/ @param covered_from the instant the rows are complete from; a window longer than
/   as_of-covered_from is left out. Null for no limit (a twin reading history)
/ @return the transform's output: one row per key per window length, sorted by key then window
/ @throws error when rows lack a column the transform reads
/ @eg count .qetl.job.stream.rolling.compute[`order_quality;([] time:enlist 2026.10.09D10:00:30; order_id:enlist 1; sym:enlist `EURUSD; book:enlist `london; product:enlist `spot; side:enlist 1; size:enlist 1e6; price:enlist 1.1; order_status:enlist `filled);2026.10.09D10:01:00;0Np]  ->  3
compute:{[name;rows;as_of;covered_from]
    d:def[name];
    t:.qetl.transform.def d`transform;
    reach:last d`windows;
    lengths:(d`windows) where $[null covered_from; (count d`windows)#1b; covered_from<=as_of-d`windows];
    tagged:tag[d`event_time;reach;lengths;as_of;(d`kept)#rows];
    if[$[0=count lengths; 1b; 0=count tagged]; :0#t`output];
    out:.qetl.transform.apply[d`transform;enlist[d`input]!enlist (cols first value t`inputs)#tagged];
    (d[`by],`window) xasc out}

/ The rolling series over a grid of instants: what a backfill twin writes for a
/ range, and what the live job publishes tick by tick.
/ @param name the job
/ @param rows the input rows
/ @param as_ofs the instants to evaluate at
/ @return the outputs of compute at each, stacked
/ @eg count .qetl.job.stream.rolling.series[`order_quality;([] time:enlist 2026.10.09D10:00:30; order_id:enlist 1; sym:enlist `EURUSD; book:enlist `london; product:enlist `spot; side:enlist 1; size:enlist 1e6; price:enlist 1.1; order_status:enlist `filled);2026.10.09D10:01:00 2026.10.09D10:02:00]  ->  6
series:{[name;rows;as_ofs]
    t:.qetl.transform.def (def[name])`transform;
    outs:{[name;rows;a] compute[name;rows;a;0Np]}[name;rows] each as_ofs;
    $[count outs; raze outs; 0#t`output]}

/ Private: how far the host's clock is ahead of UTC, to the minute. The plant
/ names and rolls its log by the local date (.z.D), so its day starts at local
/ midnight. Read now, so on the two days a year the offset changes the start it
/ gives can be an hour out. A seam: a test replaces it to be a zone of its own.
/ @private
zone_offset:{[] 0D00:01*"j"$(.z.P-.z.p)%0D00:01}

/ Private: the start of the plant's day for an instant - local midnight, in UTC.
/ @private
day_start:{[ts] o:zone_offset[]; (`timestamp$`date$ts+o)-o}

/ Private: buffer one batch of events - refuse the late and the unclocked, and
/ evict what no future window can hold - or, for the output table, record the
/ grid points it holds as published. A batch on any other table is ignored.
/ @private
on_batch:{[name;t;x]
    d:def[name];
    ns:.qetl.job.stream.namespace name;
    if[t=d`publishes; :mark_published[ns;x]];
    if[(t<>d`events) or 0=count x; :()];
    pq:` sv ns,`pending;
    now:(get ` sv ns,`now)[];
    / the clock the row met (see the header); read before the projection drops `time`
    rcv:.qetl.job.stream.clock_of[now;x];
    x:(d`kept)#x;
    tc:d`event_time;
    et:x tc;
    / no distinct receipt stamp when the event time is the plant's own: never late
    late:$[tc=`time; (count x)#0b; (rcv-et)>d`lateness];
    out:late or null et;
    if[any out;
        why:?[null et;`no_event_time;`late];
        gone:(x where out),'flip enlist[`reason]!enlist why where out;
        (` sv ns,`dropped) set -1000 sublist (get ` sv ns,`dropped),gone;
        .qetl.log.warn[name;"row dropped - late or without an event time";`rows`first_event!(count gone;min gone tc)]];
    pq insert x where not out;
    stamps:rcv where not null rcv;
    if[count stamps;
        mark_covered[ns;min stamps];
        span:(last d`windows)+d`lateness;
        keep_from:(max stamps)-span;
        .qetl.job.stream.evict[pq;((get pq) tc)<=keep_from]];
    }

/ Private: the buffer is complete from the start of the day of the first clock
/ the job saw - the plant's log begins at the roll - and never earlier.
/ @private
mark_covered:{[ns;clock]
    cf:` sv ns,`covered_from;
    if[null get cf; cf set day_start clock];
    }

/ Private: grid points the log or the plant says are published.
/ @private
mark_published:{[ns;x]
    if[(0=count x) or not `as_of in cols x; :()];
    lq:` sv ns,`last_as_of;
    lq set (get lq)|max x`as_of;
    }

/ Publish the statistic as of the latest grid point, if it has not been, and
/ then drop the rows no future window can hold.
/ @param name the job
/ @param now the time to evaluate at
/ @return the number of rows published
/ @eg .qpipe.job.order_quality.evaluate 2026.10.09D10:01:30
evaluate:{[name;now]
    d:def[name];
    ns:.qetl.job.stream.namespace name;
    pq:` sv ns,`pending; lq:` sv ns,`last_as_of;
    as_of:(d`period) xbar now;
    if[$[null get lq; 0b; as_of<=get lq]; :0];
    mark_covered[ns;now];
    cf:get ` sv ns,`covered_from;
    note_withheld[name;ns;as_of;cf];
    out:compute[name;get pq;as_of;cf];
    if[count out; (get ` sv ns,`publish)[d`publishes;out]];
    lq set as_of;
    span:(last d`windows)+d`lateness;
    keep_from:as_of-span;
    .qetl.job.stream.evict[pq;((get pq) d`event_time)<=keep_from];
    count out}

/ Private: log when the set of window lengths withheld for want of coverage
/ changes, so a restart's gap is said once and its end too.
/ @private
note_withheld:{[name;ns;as_of;cf]
    d:def[name];
    wq:` sv ns,`withheld;
    now_withheld:(d`windows) where not cf<=as_of-d`windows;
    if[now_withheld~get wq; :()];
    wq set now_withheld;
    $[count now_withheld;
        .qetl.log.warn[name;"windows withheld until the buffer covers them - the day's log begins at the roll";`windows`covered_from!(now_withheld;cf)];
        .qetl.log.info[name;"every window is covered";enlist[`covered_from]!enlist cf]];
    }

/ The plant's day has ended. A rolling window is not day-bucketed, so nothing
/ is published and the buffer is kept; the series carries on across the roll.
/ @param name the job
/ @param dt the date that ended
/ @return the number of rows kept across the roll
/ @eg .qpipe.job.order_quality.on_endofday 2026.10.09
end_of_day:{[name;dt]
    n:count get ` sv (.qetl.job.stream.namespace name),`pending;
    .qetl.log.info[name;"end of day - windows roll on across it";`date`buffered!(dt;n)];
    n}

\d .qetl.job.stream

/ Declare a streaming job that keeps a statistic over rolling windows.
/ @param name the job name
/ @param decl events, transform, publishes, windows, lateness, period and the process declaration
/ @return the registered streaming job name
/ @throws error when the transform is not a one-input contract carrying window, as_of and inside
at_rolling:{[name;decl] .qetl.job.stream.rolling.define[name;decl]}

\d .
