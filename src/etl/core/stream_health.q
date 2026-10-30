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
/               `uqs job gaps` reports, with the twin that refills it
/   on disk     stream_health_<job>.txt in the status directory, one JSON
/               object written on each beat - what `uqs status`'s Batches
/               column reads, with no q - and, for a polling feed, the
/               pages it republished as possibly duplicated (#1651)
/ .
/ THE FILE IS READ OUTSIDE q, by python/uqs/src/uqs/stack/stream_health.py,
/ which carries its name and keys as literals: a renamed key is a change to
/ both. It is written beside the airflow_status files, by the same
/ write-then-rename, so a reader sees the old object or the new, never half.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .

/ Per job, in this process. last_error is a symbol: one value per row, and
/ the message is shown, never parsed.
.qetl.stream_health.batches:([job:`symbol$()] ok:`long$(); failed:`long$(); last_error:`symbol$(); last_failure_at:`timestamp$())

/ What `job`'s batches have done in this process.
/ @param job the streaming job's name
/ @return dict ok, failed, last_error, last_failure_at - zeros for a job that has handled none
/ @eg .qetl.stream_health.of[`nothing_streams_this]`failed  ->  0
.qetl.stream_health.of:{[job]
    $[job in exec job from .qetl.stream_health.batches;
      .qetl.stream_health.batches job;
      `ok`failed`last_error`last_failure_at!(0;0;`;0Np)]}

/ Per job, the polling pages fetched while the previous page was in doubt
/ (#1651, stream_delivery.q): republished whole as possibly duplicated, or
/ resolved by row key, and the rows each left out or sent again.
.qetl.stream_health.doubts:([job:`symbol$()] duplicated:`long$(); duplicated_rows:`long$(); resolved:`long$(); resolved_rows:`long$(); last_range:`symbol$())

/ What `job`'s in-doubt pages have come to in this process.
/ @param job the streaming job's name
/ @return dict duplicated (pages republished whole), duplicated_rows,
/   resolved (pages settled by row key), resolved_rows (rows found already
/   published and left out) and last_range (the last in-doubt page's from
/   and to cursors, as text)
/ @eg .qetl.stream_health.doubts_of[`nothing_streams_this]`duplicated  ->  0
.qetl.stream_health.doubts_of:{[job]
    $[job in exec job from .qetl.stream_health.doubts;
      .qetl.stream_health.doubts job;
      `duplicated`duplicated_rows`resolved`resolved_rows`last_range!(0;0;0;0;`)]}

/ Count one in-doubt page of `job`'s.
/ @param job the streaming job's name
/ @param resolved 1b when a row key settled it, 0b when it was republished whole
/ @param n rows left out as already published (resolved), else rows sent again
/ @param m the page's in-flight marker, carrying from and to
/ @return the job's counts after this page
.qetl.stream_health.record_doubt:{[job;resolved;n;m]
    h:.qetl.stream_health.doubts_of[job];
    f:$[resolved; `resolved`resolved_rows; `duplicated`duplicated_rows];
    h[f]+:1,n;
    h[`last_range]:`$(-3!m`from)," -> ",-3!m`to;
    `.qetl.stream_health.doubts upsert (enlist[`job]!enlist job),h;
    h}

/ Count one batch of `job`'s: handled, or failed with `err`.
/ @param job the streaming job's name
/ @param ok 1b when on_batch returned, 0b when it threw
/ @param err the error, a string; ignored when ok
/ @return the job's counts after this batch
.qetl.stream_health.record:{[job;ok;err]
    h:.qetl.stream_health.of[job];
    h:$[ok; @[h;`ok;+;1]; h,`failed`last_error`last_failure_at!(1+h`failed;`$err;.z.p)];
    `.qetl.stream_health.batches upsert (enlist[`job]!enlist job),h;
    h}

/ Write `job`'s file: its counts and whether a batch failed since the last
/ beat (`failing`). Called by .qetl.uptime.beat, which knows the second.
/ @param job the streaming job's name
/ @param failing 1b when a batch of the job's failed since the previous beat
/ @return the file's path
.qetl.stream_health.write:{[job;failing]
    h:.qetl.stream_health.of[job];
    dup:.qetl.stream_health.doubts_of[job];
    dir:.qetl.status.status_dir[];
    system"mkdir -p ",dir;
    payload:`job`process`pid`host`at`ok`failed`failing`last_error`last_failure_at!(
        job;.qetl.run.proc_name[];.z.i;string .z.h;.z.p;h`ok;h`failed;failing;
        string h`last_error;$[null h`last_failure_at; ""; string h`last_failure_at]);
    payload,:`possibly_duplicated_pages`possibly_duplicated_rows!dup`duplicated`duplicated_rows;
    target:dir,"/stream_health_",string[job],".txt";
    tmp:target,".tmp";
    (hsym `$tmp) 0: enlist .j.j payload;
    system"mv ",tmp," ",target;
    target}

\d .
