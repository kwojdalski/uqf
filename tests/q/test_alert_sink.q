/ test_alert_sink.q - the alert_sink streaming job (.qt.qpipe.job.alert_sink).
/ .
/ The job's one edge to the world is `post`, replaced here by a fake target
/ that records every call and fails on demand. Everything else - throttle,
/ queue, retry, the dead record - runs as shipped.
/ .
/ The breaches are driven through `enqueue`/`flush` with explicit times where
/ the case is about the clock, and through `on_batch` where it is about the
/ whole path.


.qt.qpipe.job.alert_sink.hook:"http://hook.invalid/t0k3n"

/ Every (url; body) the fake target was asked to POST, in order.
.qt.qpipe.job.alert_sink.calls:()

/ How many of the next POSTs the fake target refuses.
.qt.qpipe.job.alert_sink.failures:0

.qt.qpipe.job.alert_sink.fake_post:{[target;body]
    `.qt.qpipe.job.alert_sink.calls set .qt.qpipe.job.alert_sink.calls,enlist (target;body);
    if[.qt.qpipe.job.alert_sink.failures>0;
        `.qt.qpipe.job.alert_sink.failures set .qt.qpipe.job.alert_sink.failures-1;
        '"boom"];
    "ok"}

/ The fake target answering as a real server does: a status line and a body,
/ judged by the transport's own rule. A non-2xx must fail the delivery even
/ though a client like .Q.hp would hand back its body (#987).
/ How many of the next POSTs get `status` instead of a 200.
.qt.qpipe.job.alert_sink.bad_status:0
.qt.qpipe.job.alert_sink.status:500

.qt.qpipe.job.alert_sink.answering_post:{[target;body]
    `.qt.qpipe.job.alert_sink.calls set .qt.qpipe.job.alert_sink.calls,enlist (target;body);
    code:200;
    if[.qt.qpipe.job.alert_sink.bad_status>0;
        `.qt.qpipe.job.alert_sink.bad_status set .qt.qpipe.job.alert_sink.bad_status-1;
        code:.qt.qpipe.job.alert_sink.status];
    .qetl.webhook.check "HTTP/1.1 ",string[code]," Reason\r\n\r\nwebhook down"}

/ Fresh state, the fake wired in and a URL configured.
.qt.qpipe.job.alert_sink.ready:{[]
    `.qt.qpipe.job.alert_sink.calls set ();
    `.qt.qpipe.job.alert_sink.failures set 0;
    `.qt.qpipe.job.alert_sink.bad_status set 0;
    `.qt.qpipe.job.alert_sink.status set 500;
    .qetl.job.stream.reset `alert_sink;
    `.qpipe.job.alert_sink.post set .qt.qpipe.job.alert_sink.fake_post;
    setenv[`UQF_SOURCE_CRED_ALERT_SINK;.qt.qpipe.job.alert_sink.hook];
    }

.qt.qpipe.job.alert_sink.beforeNamespace_load:{[] `.qt.qpipe.job.alert_sink.saved set .qpipe.job.alert_sink.post;}
.qt.qpipe.job.alert_sink.afterNamespace_restore:{[]
    `.qpipe.job.alert_sink.post set .qt.qpipe.job.alert_sink.saved;
    setenv[`UQF_SOURCE_CRED_ALERT_SINK;""];}

/ fx_limit_breach rows as the plant delivers them.
.qt.qpipe.job.alert_sink.breach:{[syms] .qt.qpipe.job.alert_sink.breach_at[syms;.qt.qpipe.job.alert_sink.t0]}

/ The same, stamped by the plant at `tm`.
.qt.qpipe.job.alert_sink.breach_at:{[syms;tm]
    n:count syms;
    ([] time:n#tm; sym:syms; book:n#`london; product:n#`spot; metric:n#`base_qty;
        observed:n#2e6; cap:n#1e6; severity:n#`hard; utilisation:n#2f)}

.qt.qpipe.job.alert_sink.t0:2026.10.09D09:00:00

.qt.qpipe.job.alert_sink.test_a_breach_is_delivered_to_the_configured_url:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    .qpipe.job.alert_sink.on_batch[`fx_limit_breach;.qt.qpipe.job.alert_sink.breach[enlist `EURUSD]];
    .qunit.assertEquals[count .qt.qpipe.job.alert_sink.calls;1;"one POST"];
    .qunit.assertEquals[first first .qt.qpipe.job.alert_sink.calls;.qt.qpipe.job.alert_sink.hook;"to the URL from the environment"];
    msg:.j.k last first .qt.qpipe.job.alert_sink.calls;
    .qunit.assertEquals[msg[`breach][`sym];"EURUSD";"the breach travels in the body"];
    .qunit.assertEquals[msg[`breach][`observed];2e6;"with its observed value"];
    .qunit.assertEquals[msg[`text] like "limit breach (hard): EURUSD london spot base_qty*";1b;"and a text a chat webhook can show"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.pending;0;"a delivered breach leaves the queue"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.dead;0;"and is not recorded as dead"]};

.qt.qpipe.job.alert_sink.test_a_clean_batch_delivers_nothing:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    .qpipe.job.alert_sink.on_batch[`fx_limit_breach;0#.qt.qpipe.job.alert_sink.breach[`EURUSD]];
    .qpipe.job.alert_sink.on_batch[`fx_position;.qt.qpipe.job.alert_sink.breach[enlist `EURUSD]];
    .qpipe.job.alert_sink.on_timer[];
    .qunit.assertEquals[count .qt.qpipe.job.alert_sink.calls;0;"no POST for an empty batch, another table or an idle tick"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.alerts;0;"and nothing marked alerted"]};

.qt.qpipe.job.alert_sink.test_a_replay_of_a_queued_breach_is_suppressed_and_a_later_report_is_not:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    e:{[tm] .qt.qpipe.job.alert_sink.breach_at[enlist `EURUSD;tm]};
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0;.z.p];1;"the first is queued"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0;.z.p];0;"the same row replayed is quiet"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0-0D00:01;.z.p];0;"and so is an older one"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach_at[enlist `GBPUSD;.qt.qpipe.job.alert_sink.t0];.z.p];1;"another pair is its own alert"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0+0D00:05;.z.p];1;"a reminder a period later is delivered"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach_at[`EURUSD`EURUSD;.qt.qpipe.job.alert_sink.t0+0D00:10],.qt.qpipe.job.alert_sink.breach_at[enlist `EURUSD;.qt.qpipe.job.alert_sink.t0+0D00:11];.z.p];1;"two new reports of one breach in a batch are one delivery"];
    .qunit.assertEquals[(.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0)`delivered;4;"four queued, four sent"]};

/ #1151: the sink once re-throttled on its OWN receipt clock with the
/ publisher's period, so a reminder published exactly one period after the
/ first, but received with less latency, fell inside the sink's period and was
/ dropped. The receipt time must not matter.
.qt.qpipe.job.alert_sink.test_a_reminder_is_delivered_whatever_its_delivery_latency:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    p1:.qt.qpipe.job.alert_sink.t0;
    p2:p1+0D00:05;
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach_at[enlist `EURUSD;p1];p1+0D00:00:00.012];1;"the first, received 12ms after publication"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach_at[enlist `EURUSD;p2];p2+0D00:00:00.003];1;"the reminder, received 3ms after: delivered"]};

/ #1182: a restarted fx_positions re-publishes every standing breach with a
/ fresh plant time; inside the period that is a repeat, not a new report.
.qt.qpipe.job.alert_sink.test_a_restart_republication_inside_the_period_is_suppressed:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    e:{[tm] .qt.qpipe.job.alert_sink.breach_at[enlist `EURUSD;tm]};
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0;.z.p];1;"delivered at T"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0+0D00:01:05;.z.p];0;"re-published 65s later with a fresh time: dropped"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0;.z.p];0;"a replay with the same time: dropped"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0+0D00:05;.z.p];1;"the reminder at exactly +period passes"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[e .qt.qpipe.job.alert_sink.t0+0D00:09:30;.z.p];0;"and the gap restarts from the reminder"];
    .qunit.assertEquals[(.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0)`delivered;2;"two sent in all"]};

.qt.qpipe.job.alert_sink.test_the_period_matches_the_publishers:{[t]
    .qunit.assertEquals[.qpipe.job.alert_sink.alert_period;.qpipe.job.fx_positions.alert_period;"the sink's gap is fx_positions' reminder period"]};

.qt.qpipe.job.alert_sink.test_a_failing_target_is_retried_then_recorded:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    `.qt.qpipe.job.alert_sink.failures set 100;
    .qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach[enlist `EURUSD];.qt.qpipe.job.alert_sink.t0];
    r1:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    .qunit.assertEquals[r1;`delivered`retrying`failed!0 1 0;"the first failure stays queued"];
    r2:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    .qunit.assertEquals[r2;`delivered`retrying`failed!0 1 0;"and the second"];
    r3:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0+0D00:00:20;
    .qunit.assertEquals[r3;`delivered`retrying`failed!0 0 1;"the third is the last: given up on"];
    .qunit.assertEquals[count .qt.qpipe.job.alert_sink.calls;3;"exactly max_attempts POSTs, no more"];
    d:.qpipe.job.alert_sink.dead;
    .qunit.assertEquals[count d;1;"recorded, not dropped"];
    .qunit.assertEquals[first d`attempts;3j;"with its attempt count"];
    .qunit.assertEquals[first d`error;"boom";"and the last error"];
    .qunit.assertEquals[first d`failed_at;.qt.qpipe.job.alert_sink.t0+0D00:00:20;"and when it gave up"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.pending;0;"and the queue is clear"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach_at[enlist `EURUSD;.qt.qpipe.job.alert_sink.t0+0D00:00:30];.qt.qpipe.job.alert_sink.t0+0D00:00:30];1;"a dead breach leaves the dedup state, so its next report is queued"]};

.qt.qpipe.job.alert_sink.test_an_http_error_is_a_failed_delivery_not_a_delivered_one:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    `.qpipe.job.alert_sink.post set .qt.qpipe.job.alert_sink.answering_post;
    `.qt.qpipe.job.alert_sink.bad_status set 100;
    .qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach[enlist `EURUSD];.qt.qpipe.job.alert_sink.t0];
    r1:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    .qunit.assertEquals[r1;`delivered`retrying`failed!0 1 0;"a 500 is not delivered: the breach stays pending"];
    .qunit.assertEquals[first .qpipe.job.alert_sink.pending`error;"webhook: answered 500";"with the status as its error"];
    .qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    r3:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0+0D00:00:20;
    .qunit.assertEquals[r3;`delivered`retrying`failed!0 0 1;"and is given up on after max_attempts"];
    .qunit.assertEquals[count .qt.qpipe.job.alert_sink.calls;3;"having been retried, not dropped after one"];
    d:.qpipe.job.alert_sink.dead;
    .qunit.assertEquals[(count d;first d`attempts;first d`error);(1;3j;"webhook: answered 500");"recorded in dead with the status"]};

.qt.qpipe.job.alert_sink.test_a_4xx_is_retried_and_a_recovered_endpoint_delivers:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    `.qpipe.job.alert_sink.post set .qt.qpipe.job.alert_sink.answering_post;
    `.qt.qpipe.job.alert_sink.status set 401;
    `.qt.qpipe.job.alert_sink.bad_status set 1;
    .qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach[enlist `EURUSD];.qt.qpipe.job.alert_sink.t0];
    r1:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    .qunit.assertEquals[(r1`delivered;first .qpipe.job.alert_sink.pending`error);(0;"webhook: answered 401");"the 401 is a failure"];
    r2:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    .qunit.assertEquals[r2;`delivered`retrying`failed!1 0 0;"and the 200 after it delivers"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.dead;0;"nothing is dead"]};

.qt.qpipe.job.alert_sink.test_a_transient_failure_is_delivered_on_the_retry:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    `.qt.qpipe.job.alert_sink.failures set 1;
    .qpipe.job.alert_sink.enqueue[.qt.qpipe.job.alert_sink.breach[enlist `EURUSD];.qt.qpipe.job.alert_sink.t0];
    .qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    r:.qpipe.job.alert_sink.flush .qt.qpipe.job.alert_sink.t0;
    .qunit.assertEquals[r;`delivered`retrying`failed!1 0 0;"the retry gets through"];
    .qunit.assertEquals[count .qt.qpipe.job.alert_sink.calls;2;"after one refusal and one success"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.dead;0;"and nothing is recorded as lost"];
    .qunit.assertEquals[(first .qt.qpipe.job.alert_sink.calls)~last .qt.qpipe.job.alert_sink.calls;1b;"the same bytes were sent both times"]};

.qt.qpipe.job.alert_sink.test_with_no_url_the_job_refuses_and_marks_nothing_alerted:{[t]
    .qt.qpipe.job.alert_sink.ready[];
    setenv[`UQF_SOURCE_CRED_ALERT_SINK;""];
    .qunit.assertThrows[.qpipe.job.alert_sink.on_batch[`fx_limit_breach];.qt.qpipe.job.alert_sink.breach[enlist `EURUSD];
        "*UQF_SOURCE_CRED_ALERT_SINK is not set*";"the message names the variable to set"];
    .qunit.assertEquals[count .qt.qpipe.job.alert_sink.calls;0;"nothing was sent"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.alerts;0;"and the throttle is untouched, so the breach is not silenced once a URL is set"]};

.qt.qpipe.job.alert_sink.test_the_job_is_declared_as_a_subscriber_to_fx_limit_breach:{[t]
    d:.qetl.job.stream.def `alert_sink;
    .qunit.assertEquals[d`subscribe_to;enlist `fx_limit_breach;"it reads the breaches"];
    .qunit.assertEquals[count d`publishes;0;"and publishes nothing"]};
