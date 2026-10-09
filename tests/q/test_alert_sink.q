/ test_alert_sink.q - the alert_sink streaming job (.alert_sinktest).
/ .
/ The job's one edge to the world is `post`, replaced here by a fake target
/ that records every call and fails on demand. Everything else - throttle,
/ queue, retry, the dead record - runs as shipped.
/ .
/ The breaches are driven through `enqueue`/`flush` with explicit times where
/ the case is about the clock, and through `on_batch` where it is about the
/ whole path.

\d .alert_sinktest

hook:"http://hook.invalid/t0k3n"

/ Every (url; body) the fake target was asked to POST, in order.
calls:()

/ How many of the next POSTs the fake target refuses.
failures:0

fake_post:{[target;body]
    `.alert_sinktest.calls set .alert_sinktest.calls,enlist (target;body);
    if[.alert_sinktest.failures>0;
        `.alert_sinktest.failures set .alert_sinktest.failures-1;
        '"boom"];
    "ok"}

/ The fake target answering as a real server does: a status line and a body,
/ judged by the transport's own rule. A non-2xx must fail the delivery even
/ though a client like .Q.hp would hand back its body (#987).
/ How many of the next POSTs get `status` instead of a 200.
bad_status:0
status:500

answering_post:{[target;body]
    `.alert_sinktest.calls set .alert_sinktest.calls,enlist (target;body);
    code:200;
    if[.alert_sinktest.bad_status>0;
        `.alert_sinktest.bad_status set .alert_sinktest.bad_status-1;
        code:.alert_sinktest.status];
    .qetl.webhook.check "HTTP/1.1 ",string[code]," Reason\r\n\r\nwebhook down"}

/ Fresh state, the fake wired in and a URL configured.
ready:{[]
    `.alert_sinktest.calls set ();
    `.alert_sinktest.failures set 0;
    `.alert_sinktest.bad_status set 0;
    `.alert_sinktest.status set 500;
    .qetl.job.stream.reset `alert_sink;
    `.qpipe.job.alert_sink.post set .alert_sinktest.fake_post;
    setenv[`UQF_SOURCE_CRED_ALERT_SINK;.alert_sinktest.hook];
    }

beforeNamespace_load:{[] `.alert_sinktest.saved set .qpipe.job.alert_sink.post;}
afterNamespace_restore:{[]
    `.qpipe.job.alert_sink.post set .alert_sinktest.saved;
    setenv[`UQF_SOURCE_CRED_ALERT_SINK;""];}

/ fx_limit_breach rows as the plant delivers them.
breach:{[syms]
    n:count syms;
    ([] time:n#2026.10.09D09:00:00; sym:syms; book:n#`london; product:n#`spot; metric:n#`base_qty;
        observed:n#2e6; cap:n#1e6; severity:n#`hard; utilisation:n#2f)}

t0:2026.10.09D09:00:00

test_a_breach_is_delivered_to_the_configured_url:{[t]
    ready[];
    .qpipe.job.alert_sink.on_batch[`fx_limit_breach;breach[enlist `EURUSD]];
    .qunit.assertEquals[count .alert_sinktest.calls;1;"one POST"];
    .qunit.assertEquals[first first .alert_sinktest.calls;.alert_sinktest.hook;"to the URL from the environment"];
    msg:.j.k last first .alert_sinktest.calls;
    .qunit.assertEquals[msg[`breach][`sym];"EURUSD";"the breach travels in the body"];
    .qunit.assertEquals[msg[`breach][`observed];2e6;"with its observed value"];
    .qunit.assertEquals[msg[`text] like "limit breach (hard): EURUSD london spot base_qty*";1b;"and a text a chat webhook can show"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.pending;0;"a delivered breach leaves the queue"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.dead;0;"and is not recorded as dead"]};

test_a_clean_batch_delivers_nothing:{[t]
    ready[];
    .qpipe.job.alert_sink.on_batch[`fx_limit_breach;0#breach[`EURUSD]];
    .qpipe.job.alert_sink.on_batch[`fx_position;breach[enlist `EURUSD]];
    .qpipe.job.alert_sink.on_timer[];
    .qunit.assertEquals[count .alert_sinktest.calls;0;"no POST for an empty batch, another table or an idle tick"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.alerts;0;"and nothing marked alerted"]};

test_a_repeat_inside_the_window_is_suppressed_and_one_outside_is_not:{[t]
    ready[];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0];1;"the first is queued"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0+0D00:01];0;"the same breach a minute on is quiet"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[breach[enlist `GBPUSD];.alert_sinktest.t0+0D00:01];1;"another pair is its own alert"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0+0D00:06];1;"past alert_period it is delivered again"];
    .qunit.assertEquals[(.qpipe.job.alert_sink.flush .alert_sinktest.t0)`delivered;3;"three queued, three sent"]};

test_a_failing_target_is_retried_then_recorded:{[t]
    ready[];
    `.alert_sinktest.failures set 100;
    .qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0];
    r1:.qpipe.job.alert_sink.flush .alert_sinktest.t0;
    .qunit.assertEquals[r1;`delivered`retrying`failed!0 1 0;"the first failure stays queued"];
    r2:.qpipe.job.alert_sink.flush .alert_sinktest.t0;
    .qunit.assertEquals[r2;`delivered`retrying`failed!0 1 0;"and the second"];
    r3:.qpipe.job.alert_sink.flush .alert_sinktest.t0+0D00:00:20;
    .qunit.assertEquals[r3;`delivered`retrying`failed!0 0 1;"the third is the last: given up on"];
    .qunit.assertEquals[count .alert_sinktest.calls;3;"exactly max_attempts POSTs, no more"];
    d:.qpipe.job.alert_sink.dead;
    .qunit.assertEquals[count d;1;"recorded, not dropped"];
    .qunit.assertEquals[first d`attempts;3j;"with its attempt count"];
    .qunit.assertEquals[first d`error;"boom";"and the last error"];
    .qunit.assertEquals[first d`failed_at;.alert_sinktest.t0+0D00:00:20;"and when it gave up"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.pending;0;"and the queue is clear"];
    .qunit.assertEquals[.qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0+0D00:00:30];1;"a dead breach leaves the throttle, so its next report is queued"]};

test_an_http_error_is_a_failed_delivery_not_a_delivered_one:{[t]
    ready[];
    `.qpipe.job.alert_sink.post set .alert_sinktest.answering_post;
    `.alert_sinktest.bad_status set 100;
    .qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0];
    r1:.qpipe.job.alert_sink.flush .alert_sinktest.t0;
    .qunit.assertEquals[r1;`delivered`retrying`failed!0 1 0;"a 500 is not delivered: the breach stays pending"];
    .qunit.assertEquals[first .qpipe.job.alert_sink.pending`error;"webhook: answered 500";"with the status as its error"];
    .qpipe.job.alert_sink.flush .alert_sinktest.t0;
    r3:.qpipe.job.alert_sink.flush .alert_sinktest.t0+0D00:00:20;
    .qunit.assertEquals[r3;`delivered`retrying`failed!0 0 1;"and is given up on after max_attempts"];
    .qunit.assertEquals[count .alert_sinktest.calls;3;"having been retried, not dropped after one"];
    d:.qpipe.job.alert_sink.dead;
    .qunit.assertEquals[(count d;first d`attempts;first d`error);(1;3j;"webhook: answered 500");"recorded in dead with the status"]};

test_a_4xx_is_retried_and_a_recovered_endpoint_delivers:{[t]
    ready[];
    `.qpipe.job.alert_sink.post set .alert_sinktest.answering_post;
    `.alert_sinktest.status set 401;
    `.alert_sinktest.bad_status set 1;
    .qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0];
    r1:.qpipe.job.alert_sink.flush .alert_sinktest.t0;
    .qunit.assertEquals[(r1`delivered;first .qpipe.job.alert_sink.pending`error);(0;"webhook: answered 401");"the 401 is a failure"];
    r2:.qpipe.job.alert_sink.flush .alert_sinktest.t0;
    .qunit.assertEquals[r2;`delivered`retrying`failed!1 0 0;"and the 200 after it delivers"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.dead;0;"nothing is dead"]};

test_a_transient_failure_is_delivered_on_the_retry:{[t]
    ready[];
    `.alert_sinktest.failures set 1;
    .qpipe.job.alert_sink.enqueue[breach[enlist `EURUSD];.alert_sinktest.t0];
    .qpipe.job.alert_sink.flush .alert_sinktest.t0;
    r:.qpipe.job.alert_sink.flush .alert_sinktest.t0;
    .qunit.assertEquals[r;`delivered`retrying`failed!1 0 0;"the retry gets through"];
    .qunit.assertEquals[count .alert_sinktest.calls;2;"after one refusal and one success"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.dead;0;"and nothing is recorded as lost"];
    .qunit.assertEquals[(first .alert_sinktest.calls)~last .alert_sinktest.calls;1b;"the same bytes were sent both times"]};

test_with_no_url_the_job_refuses_and_marks_nothing_alerted:{[t]
    ready[];
    setenv[`UQF_SOURCE_CRED_ALERT_SINK;""];
    .qunit.assertThrows[.qpipe.job.alert_sink.on_batch[`fx_limit_breach];breach[enlist `EURUSD];
        "*UQF_SOURCE_CRED_ALERT_SINK is not set*";"the message names the variable to set"];
    .qunit.assertEquals[count .alert_sinktest.calls;0;"nothing was sent"];
    .qunit.assertEquals[count .qpipe.job.alert_sink.alerts;0;"and the throttle is untouched, so the breach is not silenced once a URL is set"]};

test_the_job_is_declared_as_a_subscriber_to_fx_limit_breach:{[t]
    d:.qetl.job.stream.def `alert_sink;
    .qunit.assertEquals[d`subscribe_to;enlist `fx_limit_breach;"it reads the breaches"];
    .qunit.assertEquals[count d`publishes;0;"and publishes nothing"]};

\d .
