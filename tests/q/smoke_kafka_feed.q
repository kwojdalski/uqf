/ smoke_kafka_feed.q - the native Kafka feed against a REAL broker and the real
/ kfk library (#1481). Not a qUnit suite: it needs infrastructure, so it is run
/ by hand, or by CI that has them, and says why when it cannot run.
/ .
/   UQF_KFK_HOME       directory holding kfk.q and libkfk.so (KxSystems/kafka v1.6.6)
/   UQF_KAFKA_BROKERS  bootstrap servers of a DISPOSABLE broker
/ .
/   q tests/q/smoke_kafka_feed.q
/ .
/ Exit 0 with a "SKIPPED: <reason>" line when either is unset; exit 1 on a
/ failed check. It makes its own topic and consumer group (timestamped), so it
/ never touches an existing group - in particular not a production one.
/ .
/ What it proves, against the broker:
/   1. records published to a topic arrive on kafka_message with their
/      coordinates, key, broker time and payload as sent
/   2. the offset is committed only after the publish, and the broker's
/      committed offset is the one after the last record
/   3. a consumer that publishes but cannot commit (simulated) and is then
/      replaced in the same group is redelivered the same records, and
/      kafka_flow turns BOTH deliveries into ONE client_flow row each
/   4. a malformed record is dead-lettered, not lost

skipped:{[why] -1 "SKIPPED: ",why; exit 0};
if[0=count getenv `UQF_KFK_HOME; skipped "UQF_KFK_HOME is not set (the directory holding kfk.q and libkfk.so)"];
if[0=count getenv `UQF_KAFKA_BROKERS; skipped "UQF_KAFKA_BROKERS is not set (a disposable broker)"];

\l src/init.q
\l src/etl/init.q

failures:0;
check:{[ok;msg] -1 $[ok;"ok   ";"FAIL "],msg; if[not ok; failures::failures+1]; ok};
sleep_ms:{system "sleep ",string x%1000};

suffix:ssr[string .z.p;":";"."];
suffix:ssr[ssr[suffix;"D";"_"];".";"_"];
topic:`$"uqf.smoke.",suffix;
grp:"uqf-smoke-",suffix;
env:`BROKERS`GROUP`OFFSET_RESET!(getenv `UQF_KAFKA_BROKERS;grp;"earliest");
cfg:.qpipe.job.kafka_feed.config_from env;

.qpipe.job.kafka_feed.load_library[];
.qetl.kafka.topic.define[topic;`mapping`output!(`kafka_client_flow;`client_flow)];

/ --- a producer of our own, on the same library
good:{[i] "{\"sym\":\"EURUSD\",\"side\":\"buy\",\"qty\":1000000,\"price\":",string[1.1+0.001*i],",\"client\":\"ACME\",\"trade_id\":",string[100+i],"}"};
pcfg:(enlist `metadata.broker.list)!enlist `$getenv `UQF_KAFKA_BROKERS;
producer:.kfk.Producer pcfg;
ptopic:.kfk.Topic[producer;topic;()!()];
send:{[ps] {.kfk.Pub[ptopic;.kfk.PARTITION_UA;x;enlist "k"]} each ps; .kfk.Flush[producer;10000i]};
send (good 0;good 1;"not json";good 3);

/ --- the feed, publishing into recorders
env_pub:();
`.qpipe.job.kafka_feed.publish set {[t;x] `env_pub set env_pub,enlist (t;x); count x};
`.qpipe.job.kafka_flow.publish set {[t;x] `flow_pub set flow_pub,enlist (t;x); count x};
flow_pub:();
envelope:{[] $[count env_pub; (,/) env_pub[;1]; .qetl.kafka.envelope_empty]};
spin:{[n;secs] t:.z.p; while[(n>count envelope[]) and secs>(`long$.z.p-t)%1e9; .qpipe.job.kafka_feed.on_timer[]; sleep_ms 50]};

.qpipe.job.kafka_feed.open[cfg;enlist topic];
spin[4;30];
e:envelope[];
check[4=count e;"four records arrived on kafka_message, got ",string count e];
check[all e[`topic]=topic;"each carries its topic"];
check[(asc e`offset)~0 1 2 3j;"offsets 0-3, from the broker"];
check[all e[`msg_key]~\:enlist "k";"the message key is preserved"];
check[(e[`payload] 1)~good 1;"the payload is exactly as sent"];
check[all not null e`broker_time;"the broker timestamp is preserved"];
.qpipe.job.kafka_feed.on_timer[]; .qpipe.job.kafka_feed.on_timer[];
c:.kfk.CommittedOffsets[.qpipe.job.kafka_feed.client;topic;enlist 0i];
check[4=(first c)`offset;"the broker committed offset 4, the one after the last record: ",.Q.s1 c];
.qpipe.job.kafka_flow.marks:.qetl.kafka.marks_empty;
.qpipe.job.kafka_flow.on_batch[`kafka_message;e];
check[3=count flow_rows:(,/) flow_pub[;1] where `client_flow=flow_pub[;0];"kafka_flow decodes the three good records"];
dead:(,/) flow_pub[;1] where `kafka_dead_letter=flow_pub[;0];
check[(1=count dead) and `malformed=first dead`reason;"and dead-letters the malformed one"];
.qpipe.job.kafka_feed.shutdown[];

/ --- publish succeeds, commit does not: crash between the two
send (good 5;good 6);
env_pub:(); flow_pub:();
real:.qpipe.job.kafka_feed.api;
.qpipe.job.kafka_feed.api:real,(enlist `commit)!enlist {[c;t;o] '"simulated: the process died before the commit"};
.qpipe.job.kafka_feed.state:.qetl.kafka.state_new[];
.qpipe.job.kafka_feed.open[cfg;enlist topic];
spin[2;30];
check[2=count envelope[];"the doomed consumer published two records"];
.qpipe.job.kafka_flow.on_batch[`kafka_message;envelope[]];
before:count (,/) flow_pub[;1] where `client_flow=flow_pub[;0];
.qpipe.job.kafka_feed.shutdown[];

/ --- its replacement, same group, is redelivered the same records
.qpipe.job.kafka_feed.api:real;
env_pub:();
.qpipe.job.kafka_feed.state:.qetl.kafka.state_new[];
.qpipe.job.kafka_feed.open[cfg;enlist topic];
spin[2;30];
again:envelope[];
check[(asc again`offset)~4 5j;"the replacement was redelivered offsets 4 and 5, as nothing was committed"];
.qpipe.job.kafka_flow.on_batch[`kafka_message;again];
after:count (,/) flow_pub[;1] where `client_flow=flow_pub[;0];
check[(before=2) and after=2;"kafka_flow published each of the two trades once across both deliveries (",string[before]," then ",string[after],")"];
.qpipe.job.kafka_feed.on_timer[]; .qpipe.job.kafka_feed.on_timer[];
c2:.kfk.CommittedOffsets[.qpipe.job.kafka_feed.client;topic;enlist 0i];
check[6=(first c2)`offset;"and the replacement committed past them: ",.Q.s1 c2];
.qpipe.job.kafka_feed.shutdown[];

-1 $[failures;"FAILED: ",string[failures]," check(s)";"all checks passed"];
exit $[failures;1;0]
