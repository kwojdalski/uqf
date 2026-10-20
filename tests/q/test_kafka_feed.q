/ test_kafka_feed.q - the native q Kafka feed (.qt.qpipe.job.kafka_feed) over a
/ fake kfk api: configuration, optional loading, the callback inbox and the
/ transport the cycle drives. No broker, no native library.
/ .
/ The real library is exercised by tests/q/smoke_kafka_feed.q, which needs
/ UQF_KFK_HOME and UQF_KAFKA_BROKERS and says so when it skips.

.qt.qpipe.job.kafka_feed.calls:()

.qt.qpipe.job.kafka_feed.fake:{[]
    `consumer`subscribe`poll`commit`assignment`close`on_error!(
        {[cfg] `.qt.qpipe.job.kafka_feed.calls set .qt.qpipe.job.kafka_feed.calls,enlist (`consumer;cfg); 7i};
        {[c;topics;cb] `.qt.qpipe.job.kafka_feed.calls set .qt.qpipe.job.kafka_feed.calls,enlist (`subscribe;c;topics); };
        {[c;ms;n] `.qt.qpipe.job.kafka_feed.calls set .qt.qpipe.job.kafka_feed.calls,enlist (`poll;c;ms;n); 0};
        {[c;t;o] `.qt.qpipe.job.kafka_feed.calls set .qt.qpipe.job.kafka_feed.calls,enlist (`commit;c;t;o); };
        {[c] ([] topic:enlist `uqf.client.flow; partition:enlist 0i)};
        {[c] `.qt.qpipe.job.kafka_feed.calls set .qt.qpipe.job.kafka_feed.calls,enlist (`close;c); };
        {[c;cb] })}

.qt.qpipe.job.kafka_feed.saved:()
.qt.qpipe.job.kafka_feed.beforeNamespace_load:{[] `.qt.qpipe.job.kafka_feed.saved set (.qpipe.job.kafka_feed.api;.qpipe.job.kafka_feed.state;.qpipe.job.kafka_feed.client;.qpipe.job.kafka_feed.inbox);}
.qt.qpipe.job.kafka_feed.afterNamespace_restore:{[]
    s:.qt.qpipe.job.kafka_feed.saved;
    `.qpipe.job.kafka_feed.api set s 0; `.qpipe.job.kafka_feed.state set s 1;
    `.qpipe.job.kafka_feed.client set s 2; `.qpipe.job.kafka_feed.inbox set s 3;}

.qt.qpipe.job.kafka_feed.msg:{[off]
    `mtype`topic`client`partition`offset`msgtime`data`key`headers!(`;`uqf.client.flow;7i;0i;off;2026.10.20D09:00:00.000000000;"x"$"{}";"x"$"k1";()!())}

.qt.qpipe.job.kafka_feed.test_config_maps_the_settings_to_librdkafka_properties:{[t]
    c:.qpipe.job.kafka_feed.config_from `BROKERS`GROUP`OFFSET_RESET`SECURITY_PROTOCOL`SASL_MECHANISM`SASL_USERNAME`SSL_CA_LOCATION!("b1:9092,b2:9092";"g1";"latest";"SASL_SSL";"PLAIN";"svc";"/etc/ca.pem");
    .qunit.assertEquals[c`metadata.broker.list;`$"b1:9092,b2:9092";"brokers"];
    .qunit.assertEquals[(c`group.id;c`auto.offset.reset;c`security.protocol);(`g1;`latest;`SASL_SSL);"group, offset policy, protocol"];
    .qunit.assertEquals[(c`sasl.mechanism;c`sasl.username;c`ssl.ca.location);(`PLAIN;`svc;`$"/etc/ca.pem");"sasl and tls settings"]};

.qt.qpipe.job.kafka_feed.test_defaults_replay_the_backlog_in_a_group_of_its_own:{[t]
    c:.qpipe.job.kafka_feed.config_from (enlist `BROKERS)!enlist "b:9092";
    .qunit.assertEquals[(c`group.id;c`auto.offset.reset);(`$"uqf-kafka-q";`earliest);"the Python consumer's group is uqf-kafka-flow; a pilot group is never defaulted to"]};

.qt.qpipe.job.kafka_feed.test_auto_commit_is_always_off:{[t]
    c:.qpipe.job.kafka_feed.config_from (enlist `BROKERS)!enlist "b:9092";
    .qunit.assertEquals[c`$"enable.auto.commit";`false;"or the commit order becomes a race"]};

.qt.qpipe.job.kafka_feed.test_config_refuses_a_missing_broker_and_an_unset_password:{[t]
    .qunit.assertThrows[.qpipe.job.kafka_feed.config_from;()!();"*UQF_KAFKA_BROKERS is not set*";"no brokers"];
    .qunit.assertThrows[.qpipe.job.kafka_feed.config_from;`BROKERS`SASL_PASSWORD_ENV`PASSWORD!("b:1";"MY_PW";"");"*MY_PW, which is not set*";"a named password that is not there"]};

.qt.qpipe.job.kafka_feed.test_the_password_comes_from_the_named_variable_only:{[t]
    c:.qpipe.job.kafka_feed.config_from `BROKERS`SASL_PASSWORD_ENV`PASSWORD!("b:1";"MY_PW";"s3cret");
    .qunit.assertEquals[c`sasl.password;`s3cret;"read from the variable the setting names"]};

.qt.qpipe.job.kafka_feed.test_topics_default_to_every_declared_topic_and_refuse_an_undeclared_one:{[t]
    .qunit.assertEquals[.qpipe.job.kafka_feed.topics_from ()!();.qetl.kafka.topic.known[];"all declared"];
    .qunit.assertEquals[.qpipe.job.kafka_feed.topics_from (enlist `TOPICS)!enlist " uqf.client.flow ";enlist `uqf.client.flow;"trimmed"];
    .qunit.assertThrows[.qpipe.job.kafka_feed.topics_from;(enlist `TOPICS)!enlist "nope.topic";"*nope.topic*";"a topic nothing would decode"]};

.qt.qpipe.job.kafka_feed.test_the_library_is_not_loaded_at_load_time_and_start_refuses_without_it:{[t]
    .qunit.assertEquals[0;count (.qt.qpipe.job.kafka_feed.saved) 0;"loading the tree touched no kfk"];
    .qunit.assertTrue[()~key `.kfk;"and defined no .kfk namespace"];
    if[0=count getenv `UQF_KFK_HOME;
        .qunit.assertThrows[.qpipe.job.kafka_feed.load_library;::;"*UQF_KFK_HOME is not set*";"a missing library is a clear start-up error"]]};

.qt.qpipe.job.kafka_feed.test_the_transport_polls_the_inbox_and_the_cycle_publishes_then_commits:{[t]
    `.qpipe.job.kafka_feed.api set .qt.qpipe.job.kafka_feed.fake[];
    `.qpipe.job.kafka_feed.client set 7i;
    `.qpipe.job.kafka_feed.state set .qetl.kafka.state_new[];
    pub:();
    `.qpipe.job.kafka_feed.publish set {[t;x] `.qt.qpipe.job.kafka_feed.pub set .qt.qpipe.job.kafka_feed.pub,enlist (t;x); count x};
    `.qt.qpipe.job.kafka_feed.pub set ();
    `.qt.qpipe.job.kafka_feed.calls set ();
    .qpipe.job.kafka_feed.on_message each .qt.qpipe.job.kafka_feed.msg each 4 5j;
    .qpipe.job.kafka_feed.on_timer[];
    p:.qt.qpipe.job.kafka_feed.pub;
    .qunit.assertEquals[(count p;first first p);(1;`kafka_message);"one publish onto kafka_message"];
    e:last first p;
    .qunit.assertEquals[(e`topic;e`partition;e`offset);(2#`uqf.client.flow;0 0j;4 5j);"coordinates from the message"];
    .qunit.assertEquals[(e`payload;e`msg_key);(2#enlist "{}";2#enlist "k1");"payload and key as received"];
    c:.qt.qpipe.job.kafka_feed.calls;
    cm:c where `commit=first each c;
    .qunit.assertEquals[(count cm;(first cm) 2;(first cm) 3);(1;`uqf.client.flow;(enlist 0i)!enlist 6j);"the offset to resume at, committed after the publish"];
    .qunit.assertEquals[count .qpipe.job.kafka_feed.inbox;0;"the inbox was drained"];
    `.qpipe.job.kafka_feed.publish set .qetl.job.stream.unwired `kafka_feed};

.qt.qpipe.job.kafka_feed.test_shutdown_flushes_and_closes_the_client:{[t]
    `.qpipe.job.kafka_feed.api set .qt.qpipe.job.kafka_feed.fake[];
    `.qpipe.job.kafka_feed.client set 7i;
    `.qpipe.job.kafka_feed.state set .qetl.kafka.state_new[];
    `.qt.qpipe.job.kafka_feed.pub set ();
    `.qpipe.job.kafka_feed.publish set {[t;x] `.qt.qpipe.job.kafka_feed.pub set .qt.qpipe.job.kafka_feed.pub,enlist (t;x); count x};
    `.qt.qpipe.job.kafka_feed.calls set ();
    st:.qetl.kafka.state_new[];
    st[`backlog]:.qetl.kafka.from_messages .qpipe.job.kafka_feed.messages_table enlist .qt.qpipe.job.kafka_feed.msg 9j;
    `.qpipe.job.kafka_feed.state set st;
    .qpipe.job.kafka_feed.shutdown[];
    k:first each .qt.qpipe.job.kafka_feed.calls;
    .qunit.assertEquals[k;`commit`close;"commit what was published, then close, in that order"];
    .qunit.assertTrue[null .qpipe.job.kafka_feed.client;"and forget the client"];
    `.qpipe.job.kafka_feed.publish set .qetl.job.stream.unwired `kafka_feed};

.qt.qpipe.job.kafka_feed.test_the_feed_is_a_feed_that_is_not_in_the_default_start:{[t]
    d:.qetl.job.stream.def `kafka_feed;
    .qunit.assertEquals[(d`subscribe_to;d`publishes;d`procname);(`symbol$();enlist `kafka_message;`kafka_feed1);"publishes the envelope, subscribes to nothing"];
    .qunit.assertFalse[$[`start_with_all in key d; d`start_with_all; 0b];"it needs a broker, so it is on demand"]};

.qt.qpipe.job.kafka_feed.test_read_env_reads_every_setting_and_the_password_by_reference:{[t]
    e:.qpipe.job.kafka_feed.read_env[];
    .qunit.assertTrue[all .qpipe.job.kafka_feed.suffixes in key e;"every UQF_KAFKA_<SETTING> is read"];
    .qunit.assertTrue[`PASSWORD in key e;"and the password variable's value, when one is named"]};

.qt.qpipe.job.kafka_feed.test_the_real_api_names_every_call_the_feed_makes:{[t]
    a:.qpipe.job.kafka_feed.real_api[];
    .qunit.assertEquals[asc key a;asc `consumer`subscribe`poll`commit`assignment`close`on_error;"bound late, so building it needs no library"];
    .qunit.assertTrue[all 100h=type each value a;"all functions"]};

.qt.qpipe.job.kafka_feed.test_on_error_logs_and_never_throws:{[t]
    .qpipe.job.kafka_feed.on_error[7i;-195i;"broker transport failure"];
    .qunit.assertTrue[1b;"a client error callback that threw would be swallowed by librdkafka's thread, not logged"]};

.qt.qpipe.job.kafka_feed.test_open_subscribes_and_chains_the_exit_handler:{[t]
    `.qpipe.job.kafka_feed.api set .qt.qpipe.job.kafka_feed.fake[];
    `.qt.qpipe.job.kafka_feed.calls set ();
    older:@[value;`.z.exit;{(::)}];
    .qpipe.job.kafka_feed.open[(enlist `group.id)!enlist `g;enlist `uqf.client.flow];
    k:first each .qt.qpipe.job.kafka_feed.calls;
    `.z.exit set older;
    .qunit.assertEquals[k;`consumer`subscribe;"created, then subscribed"];
    .qunit.assertEquals[.qpipe.job.kafka_feed.client;7i;"the client id is kept"]};

/ --- readiness and diagnostics (#1492) --------------------------------------

.qt.qpipe.job.kafka_feed.lines:()

/ Run f with the log captured and the diagnostics empty, then put everything back.
.qt.qpipe.job.kafka_feed.with_log:{[f]
    emit:.qetl.log.emit; ctx:.qetl.kafka.diag.context; causes:.qetl.kafka.diag.causes; last_r:.qpipe.job.kafka_feed.last_readiness;
    `.qt.qpipe.job.kafka_feed.lines set ();
    delete from `.qetl.kafka.diag.causes;
    `.qpipe.job.kafka_feed.last_readiness set "";
    `.qetl.log.emit set {[level;id;msg] `.qt.qpipe.job.kafka_feed.lines set .qt.qpipe.job.kafka_feed.lines,enlist (level;msg)};
    r:@[f;::;{[e] (`failed;e)}];
    `.qetl.log.emit set emit; `.qetl.kafka.diag.context set ctx; `.qetl.kafka.diag.causes set causes; `.qpipe.job.kafka_feed.last_readiness set last_r;
    if[(2=count r) and `failed~first r; 'last r];
    r}

.qt.qpipe.job.kafka_feed.test_open_tells_the_diagnostics_what_it_connects_to:{[t]
    `.qpipe.job.kafka_feed.api set .qt.qpipe.job.kafka_feed.fake[];
    older:@[value;`.z.exit;{(::)}];
    ctx:.qetl.kafka.diag.context;
    .qpipe.job.kafka_feed.open[`metadata.broker.list`group.id!(`$"b1:9092, b2:9092";`g1);enlist `uqf.client.flow];
    got:.qetl.kafka.diag.context;
    `.qetl.kafka.diag.context set ctx; `.z.exit set older;
    .qunit.assertEquals[got`bootstrap;("b1:9092";"b2:9092");"the bootstrap list, trimmed, so an endpoint can be told from an advertised one"];
    .qunit.assertEquals[got`group;"g1";"and the group, for the remedy"]};

.qt.qpipe.job.kafka_feed.test_readiness_is_not_ready_until_it_is_proved:{[t]
    `.qpipe.job.kafka_feed.api set .qt.qpipe.job.kafka_feed.fake[];
    `.qpipe.job.kafka_feed.client set 7i;
    `.qpipe.job.kafka_feed.state set .qetl.kafka.state_new[];
    / with_log empties the causes first: other suites' polled events leave some behind.
    r:.qt.qpipe.job.kafka_feed.with_log {.qpipe.job.kafka_feed.readiness[]};
    .qunit.assertEquals[exec stage from r;`library`bootstrap`advertised`group`consume`published`committed;"every stage named"];
    .qunit.assertEquals[(exec status from r where stage=`library);enlist `ok;"the fake library is loaded"];
    .qunit.assertEquals[(exec status from r where stage=`consume);enlist `pending;"and nothing has been polled: not ready, whatever was logged"]};

.qt.qpipe.job.kafka_feed.test_the_readiness_line_is_logged_when_it_changes_and_not_otherwise:{[t]
    `.qpipe.job.kafka_feed.api set .qt.qpipe.job.kafka_feed.fake[];
    `.qpipe.job.kafka_feed.client set 7i;
    `.qpipe.job.kafka_feed.state set .qetl.kafka.state_new[];
    .qt.qpipe.job.kafka_feed.with_log {
        do[5; .qpipe.job.kafka_feed.report_readiness[]];
        .qunit.assertEquals[count .qt.qpipe.job.kafka_feed.lines;1;"five identical turns, one line"];
        st:.qpipe.job.kafka_feed.state; st[`stats;`polled]:3; `.qpipe.job.kafka_feed.state set st;
        .qpipe.job.kafka_feed.report_readiness[];
        .qunit.assertEquals[count .qt.qpipe.job.kafka_feed.lines;2;"a stage turning ok is a new line"];
        m:last last .qt.qpipe.job.kafka_feed.lines;
        .qunit.assertTrue[(m like "*group=ok*") and m like "*consume=ok*";"an assignment and a polled record: group and consume ok"]}}

.qt.qpipe.job.kafka_feed.test_an_unresolvable_advertised_host_across_many_turns_logs_one_error_and_the_stage:{[t]
    `.qpipe.job.kafka_feed.api set (.qt.qpipe.job.kafka_feed.fake[]),(enlist `assignment)!enlist {[c] ()};
    `.qpipe.job.kafka_feed.client set 7i;
    `.qpipe.job.kafka_feed.state set .qetl.kafka.state_new[];
    .qt.qpipe.job.kafka_feed.with_log {
        `.qetl.kafka.diag.context set `bootstrap`group!(enlist "127.0.0.1:9092";"g1");
        reason:"broker-unresolvable.invalid:43687/1: Failed to resolve 'broker-unresolvable.invalid:43687': Name or service not known (after 1ms in state CONNECT)";
        do[60;
            .qpipe.job.kafka_feed.on_error[7i;-193i;reason];
            .qpipe.job.kafka_feed.on_error[7i;-187i;"1/1 brokers are down"];
            .qpipe.job.kafka_feed.on_timer[]];
        errs:.qt.qpipe.job.kafka_feed.lines where `ERROR=first each .qt.qpipe.job.kafka_feed.lines;
        .qunit.assertEquals[count errs;1;"120 client errors over 60 timer turns: one ERROR line"];
        .qunit.assertEquals[count .qt.qpipe.job.kafka_feed.lines where `WARNING=first each .qt.qpipe.job.kafka_feed.lines;0;"and no WARNING repeated every second"];
        m:last first errs;
        .qunit.assertTrue[(m like "*advertised stage failed*") and (m like "*broker-unresolvable.invalid:43687*") and m like "*Name or service not known*";"naming the stage, the host:port and the resolver's error"];
        r:.qpipe.job.kafka_feed.readiness[];
        .qunit.assertEquals[(exec status from r where stage=`advertised);enlist `failed;"readiness says advertised failed"];
        .qunit.assertEquals[(exec status from r where stage=`group);enlist `pending;"and the group is merely pending: nothing else is blamed"]}}

.qt.qpipe.job.kafka_feed.test_start_stops_before_connecting_when_kafka_is_not_set_up:{[t]
    if[0=count getenv `UQF_KAFKA_BROKERS;
        .qunit.assertThrows[.qpipe.job.kafka_feed.on_start;::;"*UQF_KAFKA_BROKERS is not set*";"no brokers: the process does not start"]]};

/ What test_job_output_contracts.q drives kafka_feed with: a feed has no batch to
/ push, so the driver puts two messages in the inbox over the fake api and runs
/ one timer turn. Everything it touches is put back.
.qt.qpipe.job.kafka_feed.contract_driver:{[]
    s:(.qpipe.job.kafka_feed.api;.qpipe.job.kafka_feed.state;.qpipe.job.kafka_feed.client;.qpipe.job.kafka_feed.inbox);
    `.qpipe.job.kafka_feed.api set .qt.qpipe.job.kafka_feed.fake[];
    `.qpipe.job.kafka_feed.client set 7i;
    `.qpipe.job.kafka_feed.state set .qetl.kafka.state_new[];
    .qpipe.job.kafka_feed.on_message each .qt.qpipe.job.kafka_feed.msg each 0 1j;
    r:@[{.qpipe.job.kafka_feed.on_timer[]; 1b};::;{[e] e}];
    `.qpipe.job.kafka_feed.api set s 0; `.qpipe.job.kafka_feed.state set s 1;
    `.qpipe.job.kafka_feed.client set s 2; `.qpipe.job.kafka_feed.inbox set s 3;
    if[not r~1b; 'r];
    }
