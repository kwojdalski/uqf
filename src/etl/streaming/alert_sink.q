/ alert_sink.q - delivers `fx_limit_breach` rows to a webhook, the first
/ OUTBOUND sink (.qpipe.job.alert_sink, #947).
/ .
/ Until this job existed a breach was published and then reached nobody: jobs
/ could write only inward (a table, the HDB, the tickerplant).
/ .
/ THE GUARANTEE, stated at its weakest true strength (pipeline-philosophy §2):
/ AT LEAST ONCE, per breach, as far as this process lives. A breach is POSTed
/ again when the target answered with an error after having acted on the
/ request, and again when the process dies between a successful POST and
/ the removal of the row from `pending`. It is NOT exactly-once and it is
/ not durable either: `pending` and `dead` are in memory, so a restart loses
/ what was queued. The receiver must tolerate a repeat.
/ .
/ WHY A STREAMING JOB AND NOT A .qetl.io MANAGER. A manager is the place a
/ BOUNDED worker's window goes, after the window is fetched and before its
/ coverage is staged; it is called once per window, with a batch that is
/ finished data, and a failure there fails the window. A breach is an event
/ that nobody asked for as a window: it arrives on a subscription, has no
/ range and no coverage to protect, and its failure policy (retry a few
/ ticks, then record) is not "fail the run". The consumer-of-a-table shape is
/ exactly what a streaming job already is, and it keeps the framework core
/ free of a second delivery concept. A manager for FILE sinks from bounded
/ workers (Parquet/CSV) is the part of #947 this does not do.
/ .
/ THROTTLING. fx_positions already throttles what it publishes, but a
/ restart REPLAYS and a second publisher is possible, so the sink applies
/ .qlimit.throttle again, on the same identity (scope and metric). A breach
/ that was recorded as dead leaves the throttle, so the next report of it is
/ delivered rather than silenced for another period.
/ .
/ RETRIES, BOUNDED AND RECORDED. A delivery is attempted when the breach
/ arrives and then once per timer tick, at most `max_attempts` times. The
/ one after that moves it to `dead` with its last error, and logs it at
/ error. Nothing is slept on: the tick is the backoff.
/ .
/ THE URL is a credential (webhook URLs carry their token), so it is read from
/ UQF_SOURCE_CRED_ALERT_SINK only and is never logged, and no file in this
/ tree names one. With the variable unset the job REFUSES: on_batch throws a
/ message naming the variable, which .qetl.stream_health counts as failing,
/ rather than idling and letting breaches vanish unannounced.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qpipe.job.alert_sink

/ The columns of a breach this job delivers: fx_limit_breach less the plant's
/ `time`, which is not part of a breach's identity (see .qlimit.scope_cols).
breach_cols:`sym`book`product`metric`observed`cap`severity`utilisation

/ How long a delivered breach stays quiet. As fx_positions' own period.
alert_period:0D00:05;

/ How many times one breach is attempted before it is recorded as dead.
max_attempts:3

/ Throttle state: breach identity -> when it was last queued.
alerts:.qlimit.no_alerts[]

/ Breaches awaiting delivery. `body` is the JSON text to POST, built once so
/ that every attempt sends the same bytes.
pending:([] id:`symbol$(); body:(); attempts:`long$(); error:())

/ Breaches given up on after max_attempts, with the last error. The record
/ that makes a failed delivery visible rather than dropped.
dead:([] id:`symbol$(); body:(); attempts:`long$(); error:(); failed_at:`timestamp$())

/ The webhook URL, from the environment.
/ @return the URL as a string - a credential, so never log it
/ @throws error naming UQF_SOURCE_CRED_ALERT_SINK when it is unset
/ @private
url:{[]
    env_var:.qetl.source.credential_var[`alert_sink];
    v:getenv `$env_var;
    if[0=count v;
        '"alert_sink: ",env_var," is not set - the job refuses to run without a webhook URL, so a breach is never dropped unannounced"];
    v}

/ The HTTP POST, and the seam a test replaces with a fake target. Throws on a
/ connection error or any non-2xx status (.qetl.webhook.post reads the status
/ line; .Q.hp does NOT signal on one, it returns the error body - #987).
/ @param target the URL
/ @param body the JSON text
/ @return the response body
/ @eg .qpipe.job.alert_sink.post[`$":http://localhost:9/hook";"{}"]
post:{[target;body] .qetl.webhook.post[target;body]}

/ The message a breach becomes: a `text` for chat webhooks and the breach
/ itself as `breach` for anything that parses it.
/ @param row a breach as a dictionary of breach_cols
/ @return JSON text
/ @eg .qpipe.job.alert_sink.payload `sym`book`product`metric`observed`cap`severity`utilisation!(`EURUSD;`london;`spot;`base_qty;2e6;1e6;`hard;2f)
payload:{[row]
    text:"limit breach (",string[row`severity],"): ",string[row`sym]," ",string[row`book]," ",string[row`product]," ",
         string[row`metric]," ",string[row`observed]," against cap ",string[row`cap];
    .j.j `text`breach!(text;row)}

/ Queue the breaches that survive the throttle.
/ @param x fx_limit_breach rows
/ @param now the time to throttle against
/ @return how many were queued
/ @private
enqueue:{[x;now]
    r:.qlimit.throttle[.qpipe.job.alert_sink.alerts;breach_cols#x;now;.qpipe.job.alert_sink.alert_period];
    `.qpipe.job.alert_sink.alerts set r`state;
    fresh:r`alerts;
    if[0=count fresh; :0];
    scope:.qlimit.scope_cols fresh;
    ids:.qlimit.identity ?[fresh;();0b;(scope,`metric)!scope,`metric];
    `.qpipe.job.alert_sink.pending upsert flip `id`body`attempts`error!(ids;payload each fresh;(count fresh)#0j;(count fresh)#enlist "");
    count fresh}

/ Attempt every pending delivery once. A success leaves the queue; a failure
/ is counted and stays, until its last attempt, when it is recorded in
/ `dead`, logged, and released from the throttle.
/ @param now the time to record a dead breach at
/ @return a dict of delivered, retrying and failed counts
/ @throws error when no URL is configured
/ @eg .qpipe.job.alert_sink.flush .z.p
flush:{[now]
    p:.qpipe.job.alert_sink.pending;
    if[0=count p; :`delivered`retrying`failed!0 0 0];
    target:url[];
    errs:{[target;body] .[{[target;body] .qpipe.job.alert_sink.post[target;body]; ""};(target;body);{[e] $[10h=type e; e; "delivery failed"]}]}[target] each p`body;
    ok:0=count each errs;
    tried:update attempts:attempts+1, error:errs from p;
    exhausted:(not ok) and (tried`attempts)>=.qpipe.job.alert_sink.max_attempts;
    gave_up:tried where exhausted;
    `.qpipe.job.alert_sink.pending set tried where (not ok) and not exhausted;
    if[count gave_up;
        `.qpipe.job.alert_sink.dead upsert update failed_at:now from gave_up;
        `.qpipe.job.alert_sink.alerts set (key[.qpipe.job.alert_sink.alerts] except gave_up`id)#.qpipe.job.alert_sink.alerts;
        {[r] .qetl.log.err[`alert_sink;"breach not delivered - recorded in dead";`id`attempts`error!(r`id;r`attempts;r`error)]} each gave_up];
    `delivered`retrying`failed!(count where ok;count .qpipe.job.alert_sink.pending;count gave_up)}

/ A batch of breaches: throttle, queue, and try to deliver.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    if[not t=`fx_limit_breach; :()];
    if[0=count x; :()];
    / Before the throttle is touched: a refusal must not mark anything alerted.
    url[];
    enqueue[x;.z.p];
    flush[.z.p];
    }

/ The retry tick: what is still pending is attempted again. Silent while
/ nothing is pending, so an unconfigured job refuses only when it has a
/ breach to deliver.
on_timer:{[] flush[.z.p]; }

\d .

/ The process registry is read from this declaration: `procname` is the
/ process that runs it, and `start_with_all` whether `uqs start all` starts it
/ (absent: on demand, until the connection budget has room).
.qetl.job.stream.define[`alert_sink;`procname`subscribe_to`publishes`on_batch`period`on_timer`note`state`ephemeral!(
    `alert_sink1;
    enlist `fx_limit_breach;
    `symbol$();
    .qpipe.job.alert_sink.on_batch;
    0D00:00:10;
    .qpipe.job.alert_sink.on_timer;
    "outbound webhook for fx_limit_breach, at least once; refuses to run without UQF_SOURCE_CRED_ALERT_SINK. On demand: it needs a webhook URL, and without one it would only fail";
    `alerts`pending`dead;
    "a restart loses deliveries still pending or dead-lettered - logged when queued and when they fail; replaying the day would POST every breach again, and delivery is at-least-once by design (see the header)")];
