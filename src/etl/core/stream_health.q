/ stream_health.q - what each streaming job's batches did (.qetl.stream_health, #832).
/ .
/ A batch handler that throws is trapped by the transport - TorQ's upd
/ trap - so the process stays up, its heartbeat stays green, and it silently
/ drops every batch. The shell's guard (.qetl.job.stream.guarded) logs the
/ error and re-raises it, which put the failure in the process's own err log
/ and nowhere an operator looks. This keeps it as STATE instead:
/ .
/   in memory   per job, the batches handled, the batches that threw, and the
/               last failure - counted by guarded on every batch
/   uptime      .qetl.uptime.beat ends a session at the last beat before a
/               failed batch, so the span a job dropped becomes a gap that
/               `uqs gaps` reports, with the twin that refills it
/   on disk     stream_health_<job>.txt in the status directory, one JSON
/               object written on each beat - what `uqs summary`'s Batches
/               column reads, with no q
/ .
/ THE FILE IS READ OUTSIDE q, by python/uqs/src/uqs/stack/stream_health.py,
/ which carries its name and keys as literals: a renamed key is a change to
/ both. It is written beside the airflow_status files, by the same
/ write-then-rename, so a reader sees the old object or the new, never half.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qetl.stream_health

/ Per job, in this process. last_error is a symbol: one value per row, and
/ the message is shown, never parsed.
batches:([job:`symbol$()] ok:`long$(); failed:`long$(); last_error:`symbol$(); last_failure_at:`timestamp$())

/ What `job`'s batches have done in this process.
/ @param job the streaming job's name
/ @return dict ok, failed, last_error, last_failure_at - zeros for a job that has handled none
/ @eg .qetl.stream_health.of[`nothing_streams_this]`failed  ->  0
of:{[job]
    $[job in exec job from .qetl.stream_health.batches;
      .qetl.stream_health.batches job;
      `ok`failed`last_error`last_failure_at!(0;0;`;0Np)]}

/ Count one batch of `job`'s: handled, or failed with `err`.
/ @param job the streaming job's name
/ @param ok 1b when on_batch returned, 0b when it threw
/ @param err the error, a string; ignored when ok
/ @return the job's counts after this batch
record:{[job;ok;err]
    h:of job;
    h:$[ok; @[h;`ok;+;1]; h,`failed`last_error`last_failure_at!(1+h`failed;`$err;.z.p)];
    `.qetl.stream_health.batches upsert (enlist[`job]!enlist job),h;
    h}

/ Write `job`'s file: its counts and whether a batch failed since the last
/ beat (`failing`). Called by .qetl.uptime.beat, which knows the second.
/ @param job the streaming job's name
/ @param failing 1b when a batch of the job's failed since the previous beat
/ @return the file's path
write:{[job;failing]
    h:of job;
    dir:.qetl.status.status_dir[];
    system"mkdir -p ",dir;
    payload:`job`process`pid`host`at`ok`failed`failing`last_error`last_failure_at!(
        job;.qetl.run.proc_name[];.z.i;string .z.h;.z.p;h`ok;h`failed;failing;
        string h`last_error;$[null h`last_failure_at; ""; string h`last_failure_at]);
    target:dir,"/stream_health_",string[job],".txt";
    tmp:target,".tmp";
    (hsym `$tmp) 0: enlist .j.j payload;
    system"mv ",tmp," ",target;
    target}

\d .
