/ stream_carry_recovery.q - a carrying job that was down across an end of day
/ (#1072): the marker of the last end of day it saw, the catch-up that replays
/ the logs of the days it missed, and the error for what no log could restore.
/ .
/ The same namespace as stream_job.q, which runs these from `start` (and
/ writes the marker from `end_of_day`); loaded straight after it.

\d .

/ Where a carrying job records the last day it saw end (#1072): beside the
/ ledgers, in the status directory, so it outlives the process.
/ @param job the job's name
/ @return the file path
/ @eg .qetl.job.stream.carry_marker_path `fx_positions
.qetl.job.stream.carry_marker_path:{[job] (.qetl.job.bounded.state.lock_dir[]),"/carry_",string job}

/ The last end of day a carrying job recorded, 0Nd when none (a first start).
/ A marker that is there but unreadable, .bak and all, is not a first start:
/ it throws, rather than answer the 0Nd that would skip catch-up quietly (#2310).
/ @param job the job's name
/ @return a date, or 0Nd
/ @throws error when the marker and its .bak are both unreadable
/ @eg .qetl.job.stream.last_end_seen `fx_positions
.qetl.job.stream.last_end_seen:{[job]
    p:.qetl.job.stream.carry_marker_path[job];
    if[()~key hsym `$p; :0Nd];
    first (.qetl.job.bounded.state.durable_get p)`last_end_of_day}

/ How many batches the publish seam has withheld (a check or the plant's
/ contract): `withhold` returns :: like a publisher that sent nothing, so
/ `snapshot` reads this to tell a withheld snapshot from a published one.
.qetl.job.stream.withheld_n:0

/ At the end of a day, record that a carrying job carried across it (#1072) -
/ but only once its snapshot was published (#1307). A failed or withheld
/ snapshot leaves the marker where it was, so a restart catches up from the
/ earlier log, or says what is missing.
/ @param dt the date that ended
/ @param j the job's name
/ @return 1b when the marker was recorded
/ @eg .qetl.job.stream.carry_end_of_day[2026.10.09;`no_such_job]  / 0b: the snapshot failed, so no marker
.qetl.job.stream.carry_end_of_day:{[dt;j]
    ok:@[{.qetl.job.stream.snapshot x; 1b};j;{[dt;j;e] .qetl.log.ch.err[`eod;"carry snapshot failed";(enlist[`job]!enlist j),`date`error!(dt;e)]; 0b}[dt;j]];
    if[not ok; :0b];
    / A one-row table, not a bare date: PeachQ cannot read a `set atom back.
    @[{[dt;j] .qetl.job.bounded.state.durable_set[.qetl.job.stream.carry_marker_path[j];([] last_end_of_day:enlist dt)]; 1b}[dt];j;
        {[j;e] .qetl.log.ch.warn[`eod;"could not record the end of day it carried";(enlist[`job]!enlist j),enlist[`error]!enlist e]; 0b}[j]]}

/ The keys of a transport that can recover a carrying job across an end of day.
.qetl.job.stream.recovery_keys:`earlier_days`plant_day`has_log

/ Judge a transport's recovery keys against a job (#1220). Only a job with
/ carry and replay needs them.
/ @param job the job's name
/ @param tr the transport, after connect
/ @return the job name
/ @throws error naming the recovery keys the transport lacks, when it has some but not all
/ @eg .qetl.job.stream.require_recovery[`fx_positions;.qetl.job.stream.recovery_keys!3#enlist {[x]}]  / `fx_positions
.qetl.job.stream.require_recovery:{[job;tr]
    d:.qetl.job.stream.def[job];
    if[not (`carry in key d) and d`replay; :job];
    present:.qetl.job.stream.recovery_keys inter key tr;
    if[count[present]=count .qetl.job.stream.recovery_keys; :job];
    if[count present;
        '"start: the transport for ",string[job]," has some recovery keys but not all - missing ",
            ", " sv string .qetl.job.stream.recovery_keys except present];
    / $[], not `and`: q evaluates both sides, and tr`recovery of a transport without it is a null
    if[$[`recovery in key tr; not tr`recovery; 0b]; :job];
    .qetl.log.ch.err[`eod;"this transport has none of earlier_days, plant_day and has_log, so a restart across an end of day will lose the state this job carries - give the transport all three, or state recovery:0b if it cannot recover";
        (enlist[`job]!enlist job),enlist[`transport_keys]!enlist key tr];
    job}

/ The day the plant's log is on - the log `subscribe` is about to replay (#1190).
/ The plant owns that fact; the job's own clock can disagree with it (another
/ host or zone, or a restart after midnight but before the plant's timer rolls
/ the log), and the logs replayed before and by `subscribe` must not overlap.
/ A transport with no `plant_day` falls back to the job's clock.
/ @param tr the transport
/ @return a date
/ @eg .qetl.job.stream.plant_today ()!()  / no plant_day: the job's own clock
.qetl.job.stream.plant_today:{[tr] $[`plant_day in key tr; tr[`plant_day][]; .z.D]}

/ Before a carrying job replays today's log, rebuild what an end of day it
/ was down for left out (#1072). The snapshot is written by a job RUNNING at
/ the roll, so one down across it restarts into a log with none. The state
/ is the snapshot that opens the log of the day after the last end of day the
/ job saw, plus every fill since: so the logs from then to yesterday are
/ replayed first, in order, through the same handler (and with the same
/ `replaying` and muted publish) as today's. Correct however many days were
/ missed, because each log continues the one before it. Needs the transport's
/ optional `earlier_days`[tables; first day; last day], to the day BEFORE the
/ plant's own (`plant_day`), so the days replayed here and the log `subscribe`
/ replays never overlap (#1190). When the log of every missed day was
/ replayed (`has_log`), the job is marked `caught_up`: with no snapshot
/ anywhere, that state is a book that was flat at the marker plus every later
/ fill, and is complete (#1189). A missed day with no log is logged as an
/ ERROR naming it - earlier_days skips it, and a snapshot restored from the
/ first day's log would keep missed_end_of_day quiet (#2323). One without
/ earlier_days leaves missed_end_of_day to say what is lost.
/ @param job the job's name
/ @param tr the transport
/ @param tbls the tables subscribed to
/ @param h the handler to replay through
/ @return how many messages were replayed
/ @eg .qetl.job.stream.catch_up[`fx_positions;()!();`executions`fx_position_open;{[t;x]}]  / a transport with no earlier_days: nothing to replay
.qetl.job.stream.catch_up:{[job;tr;tbls;h]
    / an unreadable marker leaves nothing to replay from: missed_end_of_day reports it (#2310)
    last_eod:@[.qetl.job.stream.last_end_seen;job;{[e] 0Nd}];
    today:.qetl.job.stream.plant_today[tr];
    if[(null last_eod) or last_eod>=today-1; :0];
    if[not `earlier_days in key tr; :0];
    msgs:tr[`earlier_days][tbls;last_eod+1;today-1];
    if[`has_log in key tr;
        days:(last_eod+1)+til today-1+last_eod;
        missing:days where not tr[`has_log] each days;
        .qetl.job.stream.caught_up[job]:0=count missing;
        if[count missing;
            .qetl.log.ch.err[`eod;"no log for some of the days it was down across - the state it carried lacks their fills";
                (enlist[`job]!enlist job),`last_end_of_day`through`missing!(last_eod;today-1;missing)]]];
    {[h;m] h . m}[h] each msgs;
    .qetl.log.ch.info[`eod;"replayed the logs of the days it was down across, to rebuild the state it carried";
        (enlist[`job]!enlist job),`last_end_of_day`through`messages!(last_eod;today-1;count msgs)];
    count msgs}

/ After a carrying job's replay, say whether it found no snapshot because
/ it missed an end of day (#1072). The snapshot is written by a job RUNNING
/ at the roll; one down across it leaves the new day's log without one, and
/ the restart rebuilds from that day's fills alone - every position carried
/ from before is missing. A first start (no marker) and a flat book at the
/ last end of day (marker for yesterday, nothing to snapshot) are not that,
/ and pass quietly. After catch_up has replayed the earlier days a snapshot
/ is found and this is quiet; so is a catch_up that read every log from the day
/ after the marker, snapshot or not (#1189: the book was flat at the marker).
/ It speaks when that log could not be read (a transport without earlier_days
/ or has_log, or logs since cleared), and when the marker itself cannot be
/ (#2310).
/ @param job the job's name
/ @param today the date the replayed log is for (the plant's, see plant_today)
/ @return 1b when an end of day was missed, after logging it as an ERROR
/ @eg .qetl.job.stream.missed_end_of_day[`fx_positions;.z.D]
.qetl.job.stream.missed_end_of_day:{[job;today]
    if[(first $[job in key .qetl.job.stream.carry_log; .qetl.job.stream.carry_log job; enlist `none]) in `restored`restored_full; :0b];
    if[job in key .qetl.job.stream.caught_up; if[.qetl.job.stream.caught_up job; :0b]];
    r:@[{(1b;.qetl.job.stream.last_end_seen[x])};job;{(0b;x)}];
    if[not first r;
        .qetl.log.ch.err[`eod;"replayed with no carried snapshot, and the marker of the last end of day this job saw cannot be read - whether it missed one cannot be told, so the state carried from before today may be missing";
            (enlist[`job]!enlist job),`error`today`table!(last r;today;.qetl.job.stream.def[job][`carry]`table)];
        :1b];
    last_eod:last r;
    if[(null last_eod) or last_eod>=today-1; :0b];
    .qetl.log.ch.err[`eod;"replayed with no carried snapshot, and the last end of day this job saw was before yesterday - it was down at an end of day, so the state carried from before today is missing, and no earlier log held it";
        (enlist[`job]!enlist job),`last_end_of_day`today`table!(last_eod;today;.qetl.job.stream.def[job][`carry]`table)];
    1b}

/ The replay of a job's log is over: forget what its carry recorded and
/ whether it caught up.
/ @param job the job's name
/ @return the job's name
/ @eg .qetl.job.stream.replayed[`fx_positions]  ->  `fx_positions
.qetl.job.stream.replayed:{[job] .qetl.job.stream.carry_log::(enlist job) _ .qetl.job.stream.carry_log; .qetl.job.stream.caught_up::(enlist job) _ .qetl.job.stream.caught_up; job}

\d .
