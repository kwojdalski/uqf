/ stream_carry.q - state a streaming job carries across days and restarts
/ (#963): its declaration check, the handler that routes the carry table's
/ batches, the recording a replay keeps and the snapshot end of day publishes.
/ .
/ The same namespace as stream_job.q, which calls these from `define`,
/ `start` and `end_of_day`; loaded straight after it. Split out of it for
/ size alone (q-module-size); what is recovered when a carrying job was down
/ across an end of day is stream_carry_recovery.q.

\d .

/ ------------------------------------------------------------ CARRY
/ .
/ State a job carries across days and restarts (#963): a position book, say.
/ The job declares it - carry:`state`table!(`book;`position_open) - and the
/ shell does the rest, so no job hand-builds recovery again (#960 was two
/ jobs getting that wrong together):
/ .
/   at end of day   end_of_day publishes the state onto `table`, after the
/                   plant has rolled its log: it opens the new day's log.
/                   Each row carries `carried_to`, the plant `time` of the
/                   last batch the state had applied (#1015).
/   on replay       batches are applied as they come, and also recorded
/                   until the snapshot arrives. The state is then SET from
/                   it and the recorded rows later than its `carried_to`
/                   applied again: the plant tells the job its day ended
/                   asynchronously, so a batch can be logged ahead of the
/                   snapshot that live it was applied after. Exact for any
/                   state, lot order included, however late the snapshot.
/   live            the snapshot is this job's own echo, and is ignored.
/ .
/   down at the   a job that was down across an end of day restarts into a log
/   roll          with no snapshot; catch_up (stream_carry_recovery.q) first
/                 replays the logs since the last end of day it saw (#1072).
/ .
/ What a recording holds is capped (`cap` rows, default 100000), because a
/ log with no snapshot - an empty book yesterday - is recorded to its end.
/ A snapshot that arrives after the cap was hit is logged as an error: the
/ rows dropped are missing from the restored state, and that is never silent.

/ Every carry's recording during a replay: job -> (status; rows held;
/ batches), status `recording, `full once the cap was hit, `restored.
.qetl.job.stream.carry_log:(`symbol$())!()

/ job -> 1b once catch_up replayed the log of the day after the job's last end
/ of day (#1189): the state is then complete even though no log held a snapshot.
.qetl.job.stream.caught_up:(`symbol$())!`boolean$()

/ job -> the plant `time` of the last batch its carried state applied.
.qetl.job.stream.carried_to:(`symbol$())!`timestamp$()

/ job -> why its carried snapshot, replayed, could not be restored (#2893):
/ what missed_end_of_day reports once the replay is over. Cleared with the
/ replay's other records (`replayed`).
.qetl.job.stream.unrestored:(`symbol$())!`symbol$()

/ Private: refuse a malformed carry, naming it.
/ @private
.qetl.job.stream.check_carry:{[job;decl;replays]
    c:decl`carry;
    who:"define: ",string[job],"'s carry";
    if[not 99h=type c; 'who," must be a dict of state and table"];
    if[count `state`table except key c; 'who," needs state (the variable it carries) and table (where its snapshot goes)"];
    if[not -11h=type c`state; 'who,"'s state must be the name of a variable in the job's namespace"];
    if[not (c`table) in (),decl`outputs; 'who,"'s table ",string[c`table]," must be one the job publishes"];
    if[not replays; 'who," needs replay 1b - the snapshot is read back by a replay"];
    .qetl.declared.refuse_unknown[who;"a carry";c;`state`table`cap];
    if[$[`cap in key c; not (-7h=type c`cap) and 0<c`cap; 0b]; 'who,"'s cap must be a positive long, the rows a replay may hold"];
    / as `state` is checked: a name that is not there fails every end of day's snapshot (#2859)
    if[not (c`state) in key decl`ns; 'who,"'s state names ",string[c`state],", which ",string[decl`ns]," does not define"];
    }

/ The handler a job's batches go through: its guarded on_batch, behind its
/ carry when it declares one. What start subscribes with, and what a test
/ replays through.
/ @param job the job's name
/ @return a function of (table;rows)
/ @eg .qetl.job.stream.handler[`fx_positions]
.qetl.job.stream.handler:{[job]
    h:.qetl.job.stream.guarded[job;.qetl.job.stream.def[job]`on_batch];
    $[`carry in key .qetl.job.stream.def job; .qetl.job.stream.carried[job;h;;]; h]}

/ Private: route one batch for a job that carries state.
/ @private
.qetl.job.stream.carried:{[job;h;t;x]
    c:.qetl.job.stream.def[job]`carry;
    if[t=c`table;
        / counted and logged as guarded counts an on_batch, then re-raised for the
        / replay to quarantine: the batch that holds the whole carried state must
        / not be the one failure nothing counts (#2893)
        if[.qetl.job.stream.replaying;
            .[.qetl.job.stream.restore;(job;h;x);{[job;t;e]
                .qetl.job.stream.unrestored[job]:`$e;
                .[.qetl.stream_health.record;(job;0b;e);::];
                .[{.qetl.log.ch.err[`eod;y;(enlist[`job]!enlist x),z]};(job;"carried snapshot could not be restored";`table`error!(t;e));::];
                'e}[job;t]]];
        :()];
    h[t;x];
    if[(`time in cols x) and count x; .qetl.job.stream.carried_to[job]:max (.qetl.job.stream.carried_to job;max x`time)];
    if[.qetl.job.stream.replaying; .qetl.job.stream.record[job;t;x]];
    }

/ Private: how many rows a job's carry may hold during a replay.
/ @private
.qetl.job.stream.carry_cap:{[job] $[`cap in key .qetl.job.stream.def[job]`carry; .qetl.job.stream.def[job][`carry]`cap; 100000]}

/ Private: keep a replayed batch until the snapshot arrives, up to the cap; past it,
/ note the latest `time` dropped (#1372; none counts as latest, #1384).
/ @private
.qetl.job.stream.record:{[job;t;x]
    r:$[job in key .qetl.job.stream.carry_log; .qetl.job.stream.carry_log job; (`recording;0;();0Np)];
    d:$[0=count x; 0Np; `time in cols x; max x`time; 0Wp];
    if[(r 0) in `full`restored_full; .qetl.job.stream.carry_log[job]:(r 0;r 1;();max (r 3;d)); :()];
    if[.qetl.job.stream.carry_cap[job]<n:r[1]+count x; .qetl.job.stream.carry_log[job]:($[`restored=r 0; `restored_full; `full];n;();max (r 3;d)); :()];
    .qetl.job.stream.carry_log[job]:(r 0;n;r[2],enlist (t;x);r 3);
    }

/ Private: set the carried state from its snapshot, then apply again the
/ recorded rows the snapshot does not hold - those later than its
/ `carried_to`. A recording that hit its cap and dropped rows later than that
/ has lost rows the state needs: logged as an error, never silent (#1015, #1372).
/ @private
.qetl.job.stream.restore:{[job;h;x]
    c:.qetl.job.stream.def[job]`carry;
    r:$[job in key .qetl.job.stream.carry_log; .qetl.job.stream.carry_log job; (`recording;0;();0Np)];
    hwm:$[(`carried_to in cols x) and count x; max x`carried_to; 0Np];
    if[((r 0) in `full`restored_full) and (null hwm) or hwm<r 3;
        .qetl.log.ch.err[`eod;"carry snapshot arrived after its recording hit the cap - batches logged before it are not re-applied, so the restored state is missing them";
            (enlist[`job]!enlist job),`table`held`cap!(c`table;r 1;.qetl.job.stream.carry_cap[job])]];
    v:` sv (.qetl.job.stream.def[job]`ns),c`state;
    cur:get v;
    if[count lacks:(cols 0!cur) except cols x;
        '"carry snapshot on ",string[c`table]," lacks ",(", " sv string lacks),", which ",string[c`state]," now has - the state changed shape since it was published"];
    rows:(cols 0!cur)#x;
    v set $[99h=type cur; (keys cur) xkey rows; rows];
    / The state now holds what the snapshot applied, so the next snapshot says
    / so too (#2337). Neither null nor -0Wp (max over nulls) is a boundary.
    if[hwm>-0Wp; .qetl.job.stream.carried_to[job]:max (.qetl.job.stream.carried_to job;hwm)];
    .qetl.job.stream.carry_log[job]:(`restored;0;();0Np);
    {[h;hwm;b] rs:b 1; if[(not null hwm) and `time in cols rs; rs:select from rs where time>hwm]; if[count rs; h[b 0;rs]]}[h;hwm] each r 2;
    }

/ Publish a job's carried state onto its snapshot table - what end_of_day
/ does for each running job that carries one - each row stamped with the
/ plant `time` of the last batch the state applied, so a replay re-applies
/ exactly what the snapshot lacks. Nothing when the state is empty: a
/ restart with no snapshot starts flat, which is the same thing.
/ @param job the job's name
/ @return how many rows were published
/ @eg .qetl.job.stream.snapshot[`fx_positions]
.qetl.job.stream.snapshot:{[job]
    c:.qetl.job.stream.def[job]`carry;
    rows:0!get ` sv (.qetl.job.stream.def[job]`ns),c`state;
    if[count rows;
        hwm:$[job in key .qetl.job.stream.carried_to; .qetl.job.stream.carried_to job; 0Np];
        rows:update carried_to:hwm from rows;
        n0:.qetl.job.stream.withheld_n;
        (get ` sv (.qetl.job.stream.def[job]`ns),`publish)[c`table;rows];
        / a publish check that withheld the batch returns :: and no error (#1307)
        if[n0<>.qetl.job.stream.withheld_n; '"carry snapshot withheld by the publish check"]];
    count rows}

\d .
