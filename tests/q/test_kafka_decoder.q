/ test_kafka_decoder.q - who decodes a record (.qt.qetl.kafka.decoder, #1481).
/ .
/ Every case is a way for two decoder jobs to disagree about a record: both
/ publish it (a duplicate), both dead-letter it (two reasons for one record),
/ or neither does (a silent drop). The decoding itself is test_kafka_flow.q's.


/ The topic registry is shared: a case that declares a topic puts it back.
.qt.qetl.kafka.decoder.registry_saved:()
.qt.qetl.kafka.decoder.beforeNamespace_load:{[] `.qt.qetl.kafka.decoder.registry_saved set .qetl.kafka.topic.registry;}
.qt.qetl.kafka.decoder.afterNamespace_restore:{[] `.qetl.kafka.topic.registry set .qt.qetl.kafka.decoder.registry_saved;}

/ Envelope rows, one per topic, all at partition 0 offset 5 - the coordinates are identical on purpose.
.qt.qetl.kafka.decoder.rows:{[topics]
    n:count topics;
    ([] topic:topics; partition:n#0j; offset:n#5j; msg_key:n#enlist ""; broker_time:n#2026.10.20D09:00:00.000000000; payload:n#enlist "{}")}

/ Claim a topic for a job, with the shared example mapping.
.qt.qetl.kafka.decoder.claim:{[topic;job] .qetl.kafka.topic.define[topic;`mapping`output`job!(`kafka_client_flow;`client_flow;job)]}

.qt.qetl.kafka.decoder.test_a_job_answers_only_for_the_topics_it_owns:{[t]
    .qt.qetl.kafka.decoder.claim[`example.a;`job_a];
    .qt.qetl.kafka.decoder.claim[`example.b;`job_b];
    r:.qt.qetl.kafka.decoder.rows[`example.a`example.b];
    .qunit.assertEquals[(.qetl.kafka.decoder.mine[`job_a;r])`topic;enlist `example.a;"job_a keeps its own"];
    .qunit.assertEquals[(.qetl.kafka.decoder.mine[`job_b;r])`topic;enlist `example.b;"and job_b its own"]};

.qt.qetl.kafka.decoder.test_only_the_catch_all_takes_a_topic_nobody_claimed:{[t]
    r:.qt.qetl.kafka.decoder.rows[enlist `example.unclaimed];
    .qunit.assertEquals[count .qetl.kafka.decoder.mine[`kafka_flow;r];1;"kafka_flow dead-letters it"];
    .qunit.assertEquals[count .qetl.kafka.decoder.mine[`job_a;r];0;"another decoder leaves it alone"]};

.qt.qetl.kafka.decoder.test_the_catch_all_leaves_a_claimed_topic_to_its_owner:{[t]
    .qt.qetl.kafka.decoder.claim[`example.a;`job_a];
    r:.qt.qetl.kafka.decoder.rows[enlist `example.a];
    .qunit.assertEquals[count .qetl.kafka.decoder.mine[`kafka_flow;r];0;"kafka_flow does not dead-letter what job_a maps"]};

.qt.qetl.kafka.decoder.test_every_record_is_answered_for_by_exactly_one_job:{[t]
    .qt.qetl.kafka.decoder.claim[`example.a;`job_a];
    .qt.qetl.kafka.decoder.claim[`example.b;`job_b];
    r:.qt.qetl.kafka.decoder.rows[`example.a`example.b`uqf.client.flow`example.unclaimed];
    n:sum {[r;j] count .qetl.kafka.decoder.mine[j;r]}[r] each `kafka_flow`job_a`job_b;
    .qunit.assertEquals[n;count r;"four records, four answers: none dropped, none doubled"]};

.qt.qetl.kafka.decoder.test_a_second_job_cannot_declare_an_owned_topic:{[t]
    .qt.qetl.kafka.decoder.claim[`example.a;`job_a];
    .qunit.assertThrows[.qt.qetl.kafka.decoder.claim[`example.a];`job_b;"*example.a is owned by job_a, not job_b*";
        "two jobs would both decode it"];
    .qunit.assertEquals[.qetl.kafka.topic.owned `job_a;enlist `example.a;"and the owner is unchanged"]};

.qt.qetl.kafka.decoder.test_the_plan_agrees_with_the_declarations:{[t]
    / .qetl.load.topics is written from the files' text; the registry from running them.
    c:.qetl.load.topics;
    .qunit.assertEquals[(.qetl.kafka.topic.registry ([] topic:key c))`owner;value c;"the generated claims are the declared owners"]};

.qt.qetl.kafka.decoder.test_a_topic_the_plan_claims_cannot_be_taken_by_another_job:{[t]
    .qunit.assertThrows[.qt.qetl.kafka.decoder.claim[`uqf.client.flow];`job_a;"*owned by kafka_flow*";
        "the plan covers topics this process never loaded"]};

.qt.qetl.kafka.decoder.test_kafka_flow_is_a_whole_decoder:{[t]
    .qunit.assertEquals[.qetl.kafka.decoder.check `kafka_flow;`kafka_flow;"publishes what it decodes into, and the dead letters"]};
