/ uptime.q - when each streaming job was up and subscribed (.qetl.uptime, #630).
/ .
/ A bounded worker's output has the coverage ledger: every window it published
/ is recorded, and a re-run fetches only what is missing. A streaming job's
/ output had nothing. When the job died for twenty minutes its table had a
/ twenty-minute hole, a restart with `replay 1b` rebuilt the job's STATE with
/ publish muted - republishing a day would duplicate it - and nothing anywhere
/ said where the hole was. This records the other side of that: the intervals
/ the job was up and subscribed, so `gaps` can name what lies between them.
/ .
/ A WEAK CLAIM, AND KEPT APART FROM COVERAGE ON PURPOSE. "Subscribed" is not
/ "published": a job can be up and publish nothing, because nothing arrived or
/ its handler filtered it all. Coverage means "these rows were written", and a
/ backfill SKIPS covered windows on the strength of it; folding this weaker
/ fact in would let a window be skipped that was never written. So it is its
/ own table, `etl_stream_uptime`, and only ever answers "when was the job
/ listening".
/ .
/ ONE ROW PER SESSION - one start of one job in one process. A session opens
/ once the job is wired and subscribed (.qetl.job.stream.start), and a timer
/ moves its last_seen every `period`. Nothing closes it: a clean exit and a
/ crash look the same, ending at the last beat. That UNDER-claims by at most
/ one period at each session's end, which is the safe direction - a gap
/ reported that was not one costs a redundant refill; a gap missed costs
/ silently missing rows.
/ .
/ Stored in the status directory beside the run and coverage ledgers, by the
/ same durable write under its own lock, so `uqs gaps` reads it after the
/ process is gone - which is exactly when it is needed.

\d .qetl.uptime

/ Seconds between beats. Also the most a session can under-claim at its end.
period:0D00:01:00

/ The sessions THIS process opened, which are the only ones it beats.
mine:`guid$()

/ Create the root table if it is absent, and return its name.
/ Backtick form: inside \d .qetl.uptime a bare name resolves to this namespace.
init_table:{[]
    if[not `etl_stream_uptime in tables `.;
        `etl_stream_uptime set ([] session:`guid$(); job:`symbol$(); process:`symbol$();
            host:`symbol$(); pid:`int$(); started_at:`timestamp$(); last_seen:`timestamp$())];
    `etl_stream_uptime}

/ The root table.
sessions:{[] init_table[]; value `etl_stream_uptime}

/ Private: where the table is kept.
path:{[] (.qetl.job.bounded.state.lock_dir[]),"/etl_stream_uptime"}

/ Private: replace the in-memory table with what is on disk, if anything is.
reload:{[]
    init_table[];
    p:path[];
    if[not ()~key hsym `$p; `etl_stream_uptime set .qetl.job.bounded.state.durable_get p];
    `etl_stream_uptime}

/ Private: read, change and write back under this table's own lock, so two
/ processes beating at once cannot each write over the other's row.
update_shared:{[f;args]
    .qetl.job.bounded.state.with_file_lock[`etl_stream_uptime;
        {[f;args] reload[]; r:f . args; .qetl.job.bounded.state.durable_set[path[];sessions[]]; r};
        (f;args)]}

/ Open a session for `job`: it is now up and subscribed.
/ @param job the streaming job's name
/ @return the session id
begin:{[job]
    system"mkdir -p ",.qetl.job.bounded.state.lock_dir[];
    id:first 1?0Ng;
    now:.z.p;
    update_shared[{[id;job;now]
        `etl_stream_uptime insert (id;job;@[value;`.proc.procname;`];.z.h;.z.i;now;now)};
        (id;job;now)];
    `.qetl.uptime.mine set mine,id;
    id}

/ Move last_seen to now for every session this process opened.
/ @return the number of sessions beaten
beat:{[]
    if[0=count mine; :0];
    now:.z.p;
    update_shared[{[ids;now] update last_seen:now from `etl_stream_uptime where session in ids};
        (mine;now)];
    count mine}

/ Where, in [range_from; range_to), was `job` not up and subscribed?
/ .
/ Every session of the job counts, from any process, so a job restarted
/ elsewhere still closes the hole it would have left. The arithmetic is the
/ coverage ledger's own (.qetl.coverage.gaps): half-open intervals, shared
/ boundaries compose, a hole is never merged away.
/ @param job the streaming job's name
/ @param range_from start of the range
/ @param range_to end of the range, exclusive
/ @return a table of range_from/range_to; empty when the job was up throughout
/ @eg .qetl.uptime.gaps[`nothing_streams_this;2026.09.13D00:00;2026.09.14D00:00]  ->  ([] range_from:enlist 2026.09.13D00:00; range_to:enlist 2026.09.14D00:00)
gaps:{[job;range_from;range_to]
    j:job;
    up:select range_from:started_at, range_to:last_seen from sessions[]
        where job=j, last_seen>started_at;
    .qetl.coverage.gaps[range_from;range_to;up]}

/ The bounded workers that can refill `job`'s gaps: those whose dataset is a
/ table the job publishes. markout publishes demo_execution_quality, and
/ hdb_demo_markouts_backfill re-derives demo_execution_quality from the HDB - so it is
/ markout's twin. A job with none cannot be refilled; `uqs gaps` says so.
/ @param job the streaming job's name
/ @return the worker names, a symbol list
/ @eg .qetl.uptime.twins `demo_markout  ->  ,`hdb_demo_markouts_backfill
twins:{[job]
    out:(),(.qetl.job.stream.def job)`publishes;
    workers:.qetl.job.bounded.defined[];
    workers where {[out;w] ((.qetl.job.bounded.def w)`dataset) in out}[out] each workers}

/ Load what earlier processes recorded. Called by a reader that starts cold
/ (uqs gaps), and harmless to call twice.
/ @return the table name
attach:{[] reload[]}

\d .
