/ stream_carry_recovery.q - a carrying job that was down across an end of day
/ (#1072): the marker of the last end of day it saw, the catch-up that replays
/ the logs of the days it missed, and the error for what no log could restore.
/ .
/ The same namespace as stream_job.q, which runs these from `start` (and
/ writes the marker from `end_of_day`); loaded straight after it.

\d .qetl.job.stream

/ Where a carrying job records the last day it saw end (#1072): beside the
/ ledgers, in the status directory, so it outlives the process.
/ @param job the job's name
/ @return the file path
/ @eg .qetl.job.stream.carry_marker_path `fx_positions
carry_marker_path:{[job] (.qetl.job.bounded.state.lock_dir[]),"/carry_",string job}

/ The last end of day a carrying job recorded, 0Nd when none (a first start).
/ @param job the job's name
/ @return a date, or 0Nd
/ @eg .qetl.job.stream.last_end_seen `fx_positions
last_end_seen:{[job]
    p:carry_marker_path job;
    if[()~key hsym `$p; :0Nd];
    @[{first (.qetl.job.bounded.state.durable_get x)`last_end_of_day};p;{[e] 0Nd}]}

/ The day the plant's log is on - the log `subscribe` is about to replay (#1190).
/ The plant owns that fact; the job's own clock can disagree with it (another
/ host or zone, or a restart after midnight but before the plant's timer rolls
/ the log), and the logs replayed before and by `subscribe` must not overlap.
/ A transport with no `plant_day` falls back to the job's clock.
/ @param tr the transport
/ @return a date
/ @eg .qetl.job.stream.plant_today ()!()  / no plant_day: the job's own clock
plant_today:{[tr] $[`plant_day in key tr; tr[`plant_day][]; .z.D]}

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
/ replays never overlap (#1190). When the log of the first missed day was
/ replayed (`has_log`), the job is marked `caught_up`: with no snapshot
/ anywhere, that state is a book that was flat at the marker plus every later
/ fill, and is complete (#1189). One without earlier_days (TorQ's
/ per-table, rotated logs are the plant's to read) leaves missed_end_of_day to
/ say what is lost.
/ @param job the job's name
/ @param tr the transport
/ @param tbls the tables subscribed to
/ @param h the handler to replay through
/ @return how many messages were replayed
/ @eg .qetl.job.stream.catch_up[`fx_positions;()!();`executions`fx_position_open;{[t;x]}]  / a transport with no earlier_days: nothing to replay
catch_up:{[job;tr;tbls;h]
    last_eod:last_end_seen job;
    today:plant_today tr;
    if[(null last_eod) or last_eod>=today-1; :0];
    if[not `earlier_days in key tr; :0];
    msgs:tr[`earlier_days][tbls;last_eod+1;today-1];
    if[`has_log in key tr; caught_up[job]:tr[`has_log][last_eod+1]];
    {[h;m] h . m}[h] each msgs;
    .qetl.log.info[job;"replayed the logs of the days it was down across, to rebuild the state it carried";
        `last_end_of_day`through`messages!(last_eod;today-1;count msgs)];
    count msgs}

/ After a carrying job's replay, say whether it found no snapshot because
/ it missed an end of day (#1072). The snapshot is written by a job RUNNING
/ at the roll; one down across it leaves the new day's log without one, and
/ the restart rebuilds from that day's fills alone - every position carried
/ from before is missing. A first start (no marker) and a flat book at the
/ last end of day (marker for yesterday, nothing to snapshot) are not that,
/ and pass quietly. After catch_up has replayed the earlier days a snapshot
/ is found and this is quiet; so is a catch_up that read the log of the day
/ after the marker, snapshot or not (#1189: the book was flat at the marker).
/ It speaks when that log could not be read (a transport without earlier_days
/ or has_log, or logs since cleared).
/ @param job the job's name
/ @param today the date the replayed log is for (the plant's, see plant_today)
/ @return 1b when an end of day was missed, after logging it as an ERROR
/ @eg .qetl.job.stream.missed_end_of_day[`fx_positions;.z.D]
missed_end_of_day:{[job;today]
    if[`restored~first $[job in key carry_log; carry_log job; enlist `none]; :0b];
    if[job in key caught_up; if[caught_up job; :0b]];
    last_eod:last_end_seen job;
    if[(null last_eod) or last_eod>=today-1; :0b];
    .qetl.log.err[job;"replayed with no carried snapshot, and the last end of day this job saw was before yesterday - it was down at an end of day, so the state carried from before today is missing, and no earlier log held it";
        `last_end_of_day`today`table!(last_eod;today;def[job][`carry]`table)];
    1b}

\d .
