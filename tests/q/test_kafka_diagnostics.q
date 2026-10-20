/ test_kafka_diagnostics.q - which stage of Kafka-to-plant failed, said once
/ (.qt.qetl.kafka.diag, #1492). A fake client report, no broker, no library.
/ .
/ The reports are the texts librdkafka gives; the real ones were read off a
/ broker whose advertised hostname does not resolve.

.qt.qetl.kafka.diag.lines:()
.qt.qetl.kafka.diag.real_emit:.qetl.log.emit
.qt.qetl.kafka.diag.saved:()

/ Capture what is logged, and start every case with no causes and a known context.
.qt.qetl.kafka.diag.setUp_capture:{[]
    `.qt.qetl.kafka.diag.saved set (.qetl.kafka.diag.context;.qetl.kafka.diag.causes);
    `.qt.qetl.kafka.diag.lines set ();
    delete from `.qetl.kafka.diag.causes;
    `.qetl.kafka.diag.context set `bootstrap`group!(enlist "localhost:9092";"uqf-example-g1");
    `.qetl.log.emit set {[level;id;msg] `.qt.qetl.kafka.diag.lines set .qt.qetl.kafka.diag.lines,enlist (level;msg)};
    }

.qt.qetl.kafka.diag.tearDown_restore:{[]
    `.qetl.log.emit set .qt.qetl.kafka.diag.real_emit;
    s:.qt.qetl.kafka.diag.saved;
    `.qetl.kafka.diag.context set s 0;
    `.qetl.kafka.diag.causes set s 1;
    }

/ The text librdkafka gives for an advertised broker whose name does not resolve.
.qt.qetl.kafka.diag.unresolved:"broker-1.invalid:9093/1: Failed to resolve 'broker-1.invalid:9093': No address associated with hostname (after 0ms in state CONNECT)"

/ The texts below were read off a real Apache Kafka 4.3.1 broker by a librdkafka 2.16.0 client (the library under kfk): a broker whose DECOY listener advertises a name that cannot resolve, and a SASL broker with a deny-by-default authorizer.
.qt.qetl.kafka.diag.real_resolve:"broker-unresolvable.invalid:43687/1: Failed to resolve 'broker-unresolvable.invalid:43687': Name or service not known (after 1ms in state CONNECT)"
.qt.qetl.kafka.diag.real_group:"FindCoordinator response error: Group authorization failed."
.qt.qetl.kafka.diag.real_topic:"Subscribed topic not available: example.crypto.trades: Broker: Topic authorization failed"

/ --- classification ---------------------------------------------------------

.qt.qetl.kafka.diag.test_an_unresolvable_advertised_host_is_an_advertised_endpoint_failure:{[t]
    d:.qetl.kafka.diag.classify[-195i;.qt.qetl.kafka.diag.unresolved];
    .qunit.assertEquals[(d`stage;d`kind;d`endpoint;d`node);(`advertised;`connect;"broker-1.invalid:9093";enlist "1");"the stage, the endpoint the BROKER advertised, and its node"];
    .qunit.assertTrue[d[`hint] like "*advertised.listeners*";"and the remedy names the broker setting"]};

.qt.qetl.kafka.diag.test_the_same_failure_on_the_bootstrap_connection_is_a_bootstrap_failure:{[t]
    d:.qetl.kafka.diag.classify[-195i;"localhost:9092/bootstrap: Failed to resolve 'localhost:9092': No address associated with hostname"];
    .qunit.assertEquals[(d`stage;d`endpoint);(`bootstrap;"localhost:9092");"node bootstrap"];
    d2:.qetl.kafka.diag.classify[-195i;"localhost:9092: Connection refused"];
    .qunit.assertEquals[d2`stage;`bootstrap;"an endpoint in the bootstrap list that names no broker is bootstrap too"];
    d3:.qetl.kafka.diag.classify[-195i;"localhost:9092/0: Connect to ipv4#127.0.0.1:9092 failed: Connection refused"];
    .qunit.assertEquals[d3`stage;`advertised;"a broker id means the metadata sent the client there, even at a bootstrap address"]};

.qt.qetl.kafka.diag.test_the_group_coordinator_thread_is_an_advertised_endpoint:{[t]
    d:.qetl.kafka.diag.classify[-195i;"GroupCoordinator: broker-1.invalid:9093: Failed to resolve 'broker-1.invalid:9093': No address associated with hostname"];
    .qunit.assertEquals[(d`stage;d`endpoint;d`node);(`advertised;"broker-1.invalid:9093";"coordinator");"the coordinator is reached at its advertised name"]};

.qt.qetl.kafka.diag.test_a_group_and_a_topic_authorisation_failure_are_different_stages:{[t]
    g:.qetl.kafka.diag.classify[30i;"Group authorization failed: Not authorized to access group"];
    tp:.qetl.kafka.diag.classify[`TOPIC_AUTHORIZATION_FAILED;"Subscribed topic not available: example.crypto.trades: Broker: Topic authorization failed"];
    .qunit.assertEquals[(g`stage;g`kind);(`group;`group_auth);"the group"];
    .qunit.assertEquals[(tp`stage;tp`kind);(`consume;`topic_auth);"the topic - separate, so a group permission proves nothing about it"];
    .qunit.assertTrue[g[`hint] like "*group uqf-example-g1*";"the hint names the group"];
    .qunit.assertTrue[tp[`hint] like "*proves nothing*";"and says a group check is no proof of read"]};

.qt.qetl.kafka.diag.test_the_text_alone_classifies_when_there_is_no_code:{[t]
    .qunit.assertEquals[.qetl.kafka.diag.kind_of[0Ni;"Not authorized to access group: x"];`group_auth;"group, by text"];
    .qunit.assertEquals[.qetl.kafka.diag.kind_of[0Ni;"SASL authentication error: bad credentials"];`sasl;"sasl, by text"];
    .qunit.assertEquals[.qetl.kafka.diag.kind_of[0Ni;"something new"];`other;"anything else is unclassified, not dropped"]};

/ --- once per cause ---------------------------------------------------------

.qt.qetl.kafka.diag.test_a_cause_repeating_every_second_is_logged_once:{[t]
    first_time:.qetl.kafka.diag.report[-195i;.qt.qetl.kafka.diag.unresolved];
    rest:{.qetl.kafka.diag.report[-195i;.qt.qetl.kafka.diag.unresolved]} each til 59;
    .qunit.assertEquals[(first_time;any rest);(1b;0b);"only the first sighting reports"];
    .qunit.assertEquals[count .qt.qetl.kafka.diag.lines;1;"sixty callbacks, one line"];
    m:last first .qt.qetl.kafka.diag.lines;
    .qunit.assertEquals[first first .qt.qetl.kafka.diag.lines;`ERROR;"at ERROR, not a repeating WARNING"];
    .qunit.assertTrue[m like "*advertised stage failed*";"naming the stage"];
    .qunit.assertTrue[m like "*broker-1.invalid:9093*";"the host:port"];
    .qunit.assertTrue[m like "*No address associated with hostname*";"and the broker's own error"];
    .qunit.assertEquals[first exec repeats from .qetl.kafka.diag.causes;59;"the repeats are counted"]};

.qt.qetl.kafka.diag.test_a_different_cause_is_not_hidden_behind_an_old_one:{[t]
    .qetl.kafka.diag.report[-195i;.qt.qetl.kafka.diag.unresolved];
    .qetl.kafka.diag.report[-195i;"broker-2.invalid:9093/2: Failed to resolve 'broker-2.invalid:9093': No address associated with hostname"];
    .qetl.kafka.diag.report[30i;"Group authorization failed"];
    .qunit.assertEquals[count .qt.qetl.kafka.diag.lines;3;"another endpoint and another stage each report"]};

.qt.qetl.kafka.diag.test_recovery_is_logged_and_the_same_failure_reports_again_afterwards:{[t]
    do[5; .qetl.kafka.diag.report[-195i;.qt.qetl.kafka.diag.unresolved]];
    n:.qetl.kafka.diag.clear `advertised`group;
    .qunit.assertEquals[n;1;"one cause cleared"];
    m:last last .qt.qetl.kafka.diag.lines;
    .qunit.assertTrue[(m like "*advertised stage recovered*") and m like "*repeats_suppressed=4*";"the recovery says how many repeats it saw"];
    .qunit.assertEquals[.qetl.kafka.diag.report[-195i;.qt.qetl.kafka.diag.unresolved];1b;"a failure after recovery is news again"]};

.qt.qetl.kafka.diag.test_reporting_never_throws:{[t]
    .qunit.assertEquals[.qetl.kafka.diag.report[::;::];1b;"a null code and a null reason are still a report"];
    .qunit.assertEquals[.qetl.kafka.diag.report[`;"x"];0b;"and an unknown one is the same unclassified cause, counted not repeated"]};

/ --- the feed's own error callback and the poll's events -----------------------

.qt.qetl.kafka.diag.test_the_feed_s_error_callback_reports_an_unresolvable_host_once:{[t]
    {.qpipe.job.kafka_feed.on_error[7i;-195i;.qt.qetl.kafka.diag.unresolved]} each til 60;
    .qunit.assertEquals[count .qt.qetl.kafka.diag.lines;1;"sixty librdkafka callbacks, one line"];
    .qunit.assertTrue[(last first .qt.qetl.kafka.diag.lines) like "*advertised stage failed*";"as an advertised-endpoint failure"]};

.qt.qetl.kafka.diag.test_a_polled_client_event_repeating_each_poll_is_logged_once:{[t]
    tr:`poll`publish`commit`assigned!({[n] .qt.qetl.kafka.diag.events[]};{[t;x] count x};{[t;o] 1b};{[u] ([] topic:`symbol$(); partition:`int$())});
    do[20; .qetl.kafka.take[tr;.qetl.kafka.state_new[]]];
    .qunit.assertEquals[count .qt.qetl.kafka.diag.lines;1;"twenty polls reporting the same refusal, one line"];
    .qunit.assertTrue[(last first .qt.qetl.kafka.diag.lines) like "*consume stage failed*";"as the consume stage"]};

.qt.qetl.kafka.diag.events:{[] flip `mtype`topic`partition`offset`msgtime`data`key!(enlist `TOPIC_AUTHORIZATION_FAILED;enlist `example.crypto.trades;enlist 0i;enlist 0;enlist 0Np;enlist "x"$"Topic authorization failed";enlist `byte$())}

/ --- readiness ----------------------------------------------------------------

.qt.qetl.kafka.diag.test_subscribed_is_not_ready:{[t]
    f:`library`assigned`polled`published`commits`publish_errors`commit_errors!(1b;0;0;0;0;0;0);
    r:.qetl.kafka.diag.readiness f;
    .qunit.assertEquals[exec stage from r;`library`bootstrap`advertised`group`consume`published`committed;"every stage, in order"];
    .qunit.assertEquals[exec status from r;`ok`pending`pending`pending`pending`pending`pending;"a subscription proves the library and nothing else"]};

.qt.qetl.kafka.diag.test_each_stage_turns_ok_only_on_its_own_proof:{[t]
    f:`library`assigned`polled`published`commits`publish_errors`commit_errors!(1b;2;5;5;1;0;0);
    .qunit.assertEquals[exec status from .qetl.kafka.diag.readiness f;7#`ok;"assigned, polled, published and committed: all ok"];
    g:`library`assigned`polled`published`commits`publish_errors`commit_errors!(1b;2;5;0;0;3;0);
    .qunit.assertEquals[exec status from .qetl.kafka.diag.readiness g;`ok`ok`ok`ok`ok`failed`pending;"a publish that keeps failing is failed, and nothing is committed after it"]};

.qt.qetl.kafka.diag.test_an_open_cause_marks_its_stage_failed_with_the_endpoint:{[t]
    .qetl.kafka.diag.report[-195i;.qt.qetl.kafka.diag.unresolved];
    f:`library`assigned`polled`published`commits`publish_errors`commit_errors!(1b;0;0;0;0;0;0);
    r:.qetl.kafka.diag.readiness f;
    a:first select from r where stage=`advertised;
    .qunit.assertEquals[a`status;`failed;"advertised"];
    .qunit.assertTrue[a[`detail] like "*broker-1.invalid:9093*";"with the endpoint"];
    .qunit.assertEquals[(first select from r where stage=`bootstrap)`status;`pending;"bootstrap is not failed: only advertised is"]};

/ --- what a real broker said ----------------------------------------------------

.qt.qetl.kafka.diag.test_the_real_unresolvable_advertised_host_and_its_all_brokers_down_are_one_cause:{[t]
    / librdkafka alternates the two, about ten times a second at first and once a second after.
    do[100;
        .qetl.kafka.diag.report[-193i;.qt.qetl.kafka.diag.real_resolve];
        .qetl.kafka.diag.report[-187i;"1/1 brokers are down"]];
    .qunit.assertEquals[count .qt.qetl.kafka.diag.lines;1;"two hundred client errors, one log line"];
    .qunit.assertEquals[(select stage,kind,endpoint from .qetl.kafka.diag.causes)`stage;enlist `advertised;"the advertised stage"];
    .qunit.assertEquals[first exec repeats from .qetl.kafka.diag.causes;199;"the brokers-down report is the same cause's repeat"];
    m:last first .qt.qetl.kafka.diag.lines;
    .qunit.assertTrue[(m like "*broker-unresolvable.invalid:43687*") and m like "*Name or service not known*";"the host:port and the resolver's own words"]};

.qt.qetl.kafka.diag.test_brokers_down_with_nothing_before_it_is_a_bootstrap_failure:{[t]
    d:.qetl.kafka.diag.classify[-187i;"1/1 brokers are down"];
    .qunit.assertEquals[d`stage;`bootstrap;"no earlier cause explains it"]};

.qt.qetl.kafka.diag.test_the_real_group_and_topic_refusals_name_their_own_resource:{[t]
    g:.qetl.kafka.diag.classify[30i;.qt.qetl.kafka.diag.real_group];
    tp:.qetl.kafka.diag.classify[29i;.qt.qetl.kafka.diag.real_topic];
    .qunit.assertEquals[(g`stage;tp`stage);`group`consume;"a denied group and a denied topic are different stages"];
    .qunit.assertEquals[tp`kind;`topic_auth;"by code"];
    .qunit.assertEquals[.qetl.kafka.diag.kind_of[0Ni;.qt.qetl.kafka.diag.real_group];`group_auth;"and by text alone"];
    .qunit.assertEquals[.qetl.kafka.diag.kind_of[0Ni;.qt.qetl.kafka.diag.real_topic];`topic_auth;"likewise"]};
