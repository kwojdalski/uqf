/ kafka_feed.q - the native q Kafka consumer (.qpipe.job.kafka_feed, #1481).
/ .
/ Holds a Kafka subscription with KX's `kfk` interface (a thin wrapper over
/ librdkafka) and publishes every record, undecoded, onto `kafka_message`.
/ kafka_flow1 decodes and deduplicates. It replaces the Python consumer
/ (python/uqs external/kafka_streamer.py), which publishes the same envelope;
/ run one per consumer group.
/ .
/ WHY A STREAMING JOB, AND NOT `uqs connector`. A connector is a process
/ outside q that opens its own IPC handle. This one is a q process, so it takes
/ the same road as fx_feed: a declared feed (`subscribe_to` empty) that TorQ
/ starts, gives a tickerplant handle (.qtorq.feed_handle) and runs on a timer,
/ with the registry, ports, heartbeats and the process table all derived from
/ the declaration. Consumption is outside the tickerplant: this is its own
/ process, a client of it.
/ .
/ KAFKA STAYS OPTIONAL. Nothing here loads kfk or touches librdkafka at load
/ time. The library is found only when the process STARTS, in `on_start`, from
/ $UQF_KFK_HOME (a directory holding kfk.q and libkfk.so, built from
/ KxSystems/kafka v1.6.6 - docs/services/kafka.md has the steps). Unset or
/ missing, on_start throws naming the variable, and the process does not
/ start; every other process and every test run is unaffected. It is not in
/ the default start (start_with_all is off) because it consumes from outside.
/ .
/ VERSION. v1.6.6 is the last kfk for kdb+ 4.x, loaded with `2:` from libkfk.
/ KX's 2.0.0 is a KDB-X module with a different API (`use`kx.kafka`) and is
/ NOT supported here. v1.6.6 was loaded and run on KDB-X 5.0 (2026.07.23) in
/ this tree's integration test; kdb+ 4.x itself was not available to test on.
/ .
/ CONFIGURATION is environment only, UQF_KAFKA_<SETTING> (documented in
/ docs/reference/environment.md): BROKERS, TOPICS, GROUP, OFFSET_RESET,
/ SECURITY_PROTOCOL, SASL_MECHANISM, SASL_USERNAME, SSL_CA_LOCATION and
/ SASL_PASSWORD_ENV - which NAMES the variable holding the password, so a
/ secret is never written into a file or a process table. enable.auto.commit
/ is forced off: with it on, librdkafka commits on its own timer and the
/ publish-then-commit order (core/kafka_ingest.q) becomes a race.
/ .
/ OFFSET POLICY. A group with no committed offset starts at OFFSET_RESET
/ (default `earliest`: replaying the backlog is the case q handles; losing it
/ silently is not). Default group is `uqf-kafka-q`, never the Python consumer's.
/ .
/ SHUTDOWN, RECONNECT, REBALANCE. On exit the feed publishes what it holds,
/ commits it, unsubscribes and closes the client, which leaves the group
/ cleanly so a restart does not wait out the session timeout. Reconnection is
/ librdkafka's own; the error callback logs it. A rebalance is handled by the
/ cycle: offsets of partitions no longer held are not committed and the
/ broker's redelivery is absorbed downstream (kafka_ingest.q).

\d .

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant.
.qpipe.job.kafka_feed.publish:.qetl.job.stream.unwired `kafka_feed;

/ The consumer's state (marks, pending commits, backlog, stats).
.qpipe.job.kafka_feed.state:.qetl.kafka.state_new[]

/ The kfk calls the feed makes, by name. Empty until the library is loaded
/ (`load_library`), so no code path can reach librdkafka by accident, and a
/ test can supply a fake without defining a .kfk namespace.
.qpipe.job.kafka_feed.api:()!()

/ The messages the consumer callback has received since the last poll.
.qpipe.job.kafka_feed.inbox:()

/ The consumer client id, set by `open`.
.qpipe.job.kafka_feed.client:0Ni

/ Env setting suffix -> the librdkafka property it sets.
.qpipe.job.kafka_feed.properties:(`BROKERS`GROUP`OFFSET_RESET`SECURITY_PROTOCOL`SASL_MECHANISM`SASL_USERNAME`SSL_CA_LOCATION)!`metadata.broker.list`group.id`auto.offset.reset`security.protocol`sasl.mechanism`sasl.username`ssl.ca.location

/ Settings with a default when unset.
.qpipe.job.kafka_feed.defaults:(`GROUP`OFFSET_RESET)!("uqf-kafka-q";"earliest")

/ Every setting suffix read from UQF_KAFKA_<SUFFIX>.
.qpipe.job.kafka_feed.suffixes:(key .qpipe.job.kafka_feed.properties),`TOPICS`SASL_PASSWORD_ENV

/ The librdkafka configuration from the settings given.
/ .
/ Pure, so the mapping is tested without a process. Refuses a missing broker
/ list, and a SASL password that is named but unset: a feed that would
/ authenticate with nothing is better stopped than left retrying.
/ @param env dict of setting suffix -> string; unset settings absent or empty. PASSWORD, when SASL_PASSWORD_ENV is given, is the value that variable held
/ @return dict of property -> symbol, always with enable.auto.commit false
/ @throws error naming the setting
/ @eg .qpipe.job.kafka_feed.config_from[(enlist `BROKERS)!enlist "broker:9092"]`group.id  ->  `$"uqf-kafka-q"
.qpipe.job.kafka_feed.config_from:{[env]
    p:.qpipe.job.kafka_feed.properties;
    base:(key p)!(count p)#enlist "";
    base:base,.qpipe.job.kafka_feed.defaults;
    given:(k where (k:key p) in key env)#env;
    given:(where 0<count each given)#given;
    vals:base,given;
    if[0=count vals`BROKERS; '"kafka_feed: UQF_KAFKA_BROKERS is not set - the bootstrap servers to connect to"];
    vals:(where 0<count each vals)#vals;
    cfg:p[key vals]!`$value vals;
    if[0<count env`SASL_PASSWORD_ENV;
        if[0=count env`PASSWORD;
            '"kafka_feed: UQF_KAFKA_SASL_PASSWORD_ENV names ",env[`SASL_PASSWORD_ENV],", which is not set"];
        cfg[`sasl.password]:`$env`PASSWORD];
    cfg[`$"enable.auto.commit"]:`false;
    cfg}

/ The topics to subscribe to: UQF_KAFKA_TOPICS (comma separated) or every known topic.
/ .
/ Known is declared here or claimed by a decoder job in the generated load plan:
/ this process loads no decoder, and a sidecar's topics are theirs to declare.
/ @param env dict of setting suffix -> string
/ @return a symbol list
/ @throws error when a listed topic is declared by no decoder job - it would be dead-lettered record by record
/ @eg .qpipe.job.kafka_feed.topics_from[()!()]  ->  .qetl.kafka.topic.known[]
.qpipe.job.kafka_feed.topics_from:{[env]
    raw:$[`TOPICS in key env; env`TOPICS; ""];
    if[0=count trim raw; :.qetl.kafka.topic.known[]];
    t:`$trim each "," vs raw;
    if[count bad:t except .qetl.kafka.topic.known[];
        '"kafka_feed: UQF_KAFKA_TOPICS names ",(", " sv string bad),", which no .qetl.kafka.topic.define declares"];
    t}

/ The settings as the environment gives them.
/ @return dict of setting suffix -> string, with PASSWORD read from the variable SASL_PASSWORD_ENV names
.qpipe.job.kafka_feed.read_env:{[]
    env:s!getenv each `$"UQF_KAFKA_",/:string s:.qpipe.job.kafka_feed.suffixes;
    pw:env`SASL_PASSWORD_ENV;
    env[`PASSWORD]:$[count pw; getenv `$pw; ""];
    env}

/ ------------------------------------------------------------ THE LIBRARY

/ Load KX's kfk from $UQF_KFK_HOME and bind the calls the feed makes.
/ .
/ kfk.q opens libkfk relative to the working directory, so the load happens
/ there and the directory is put back even when the load throws.
/ @return nothing - .qpipe.job.kafka_feed.api is filled
/ @throws error naming UQF_KFK_HOME and what is missing
.qpipe.job.kafka_feed.load_library:{[]
    home:getenv `UQF_KFK_HOME;
    if[0=count home; '"kafka_feed: UQF_KFK_HOME is not set - the directory holding kfk.q and libkfk.so (KxSystems/kafka v1.6.6), see docs/services/kafka.md"];
    if[not `kfk.q in key hsym `$home; '"kafka_feed: ",home,"/kfk.q does not exist - UQF_KFK_HOME must hold kfk.q and libkfk.so"];
    cwd:first system "pwd";
    system "cd ",home;
    r:@[{system "l kfk.q"; `ok};::;{x}];
    system "cd ",cwd;
    if[not r~`ok; '"kafka_feed: could not load kfk from ",home,": ",r];
    `.qpipe.job.kafka_feed.api set .qpipe.job.kafka_feed.real_api[];
    }

/ The kfk calls, as functions over a client id. Bound late: `.kfk` exists only once load_library has run.
/ @return the api dictionary
.qpipe.job.kafka_feed.real_api:{[]
    `consumer`subscribe`poll`commit`assignment`close`on_error!(
        {[cfg] .kfk.Consumer cfg};
        {[c;topics;cb] .kfk.Subscribe[c;topics;enlist .kfk.PARTITION_UA;(count topics)#enlist cb]};
        {[c;ms;n] .kfk.Poll[c;ms;n]};
        {[c;t;offs] .kfk.CommitOffsets[c;t;offs;0b]};
        {[c] .kfk.Assignment c};
        {[c] .kfk.Unsub c; .kfk.ClientDel c};
        {[c;cb] .kfk.errcbreg[c;cb]})}

/ --------------------------------------------------------------- THE FEED

/ The consumer callback: hold the message until the next cycle polls it out.
/ @param m a kfk message dictionary
.qpipe.job.kafka_feed.on_message:{[m] `.qpipe.job.kafka_feed.inbox set .qpipe.job.kafka_feed.inbox,enlist m;}

/ The client's error callback: log, never throw (librdkafka reconnects on its own).
.qpipe.job.kafka_feed.on_error:{[cid;code;reason]
    .[{.qetl.log.warn[`kafka;"client error";`client`code`reason!(x;y;z)]};(cid;code;reason);::];}

/ The messages received since the last call, as the table kafka_ingest.q expects.
/ @param ms a list of kfk message dictionaries
/ @return a table of mtype topic partition offset msgtime data key
.qpipe.job.kafka_feed.messages_table:{[ms]
    c:`mtype`topic`partition`offset`msgtime`data`key;
    if[0=count ms; :flip c!(`symbol$();`symbol$();`int$();`long$();`timestamp$();();())];
    flip c!{[ms;k] ms@\:k}[ms] each c}

/ The transport kafka_ingest.q's cycle drives, over the real (or a fake) api.
/ @return the transport dictionary
.qpipe.job.kafka_feed.transport:{[]
    .qetl.kafka.transport_keys!(
        {[n] .qpipe.job.kafka_feed.api[`poll][.qpipe.job.kafka_feed.client;5;n];
             ms:.qpipe.job.kafka_feed.inbox;
             `.qpipe.job.kafka_feed.inbox set ();
             .qpipe.job.kafka_feed.messages_table ms};
        {[t;x]
             if[not t=`kafka_message; '"kafka_feed publishes only kafka_message, not ",string t];
             .qpipe.job.kafka_feed.publish[`kafka_message;x]};
        {[t;o] .qpipe.job.kafka_feed.api[`commit][.qpipe.job.kafka_feed.client;t;o]};
        {[u] a:.qpipe.job.kafka_feed.api[`assignment] .qpipe.job.kafka_feed.client;
             $[0=count a; flip `topic`partition!(`symbol$();`int$()); flip `topic`partition!(a@\:`topic;a@\:`partition)]})}

/ One turn: poll, publish, commit - in that order (kafka_ingest.q).
.qpipe.job.kafka_feed.on_timer:{[]
    `.qpipe.job.kafka_feed.state set .qetl.kafka.cycle[.qpipe.job.kafka_feed.transport[];.qpipe.job.kafka_feed.state];
    }

/ Leave cleanly: publish and commit what is held, then close the client.
/ Each step is protected so one failure does not skip the close.
.qpipe.job.kafka_feed.shutdown:{[]
    if[null .qpipe.job.kafka_feed.client; :()];
    tr:.qpipe.job.kafka_feed.transport[];
    r:.qetl.kafka.try[{[tr;st] .qetl.kafka.flush[tr;st]};(tr;.qpipe.job.kafka_feed.state)];
    if[first r; `.qpipe.job.kafka_feed.state set last r];
    .[{.qpipe.job.kafka_feed.api[`close] x};enlist .qpipe.job.kafka_feed.client;{[e] .qetl.log.warn[`kafka;"close failed";enlist[`error]!enlist e]}];
    `.qpipe.job.kafka_feed.client set 0Ni;
    }

/ Create the consumer, subscribe, and arrange a clean exit.
/ @param cfg the librdkafka configuration
/ @param topics the topics
.qpipe.job.kafka_feed.open:{[cfg;topics]
    c:.qpipe.job.kafka_feed.api[`consumer] cfg;
    `.qpipe.job.kafka_feed.client set c;
    .qpipe.job.kafka_feed.api[`on_error][c;.qpipe.job.kafka_feed.on_error];
    .qpipe.job.kafka_feed.api[`subscribe][c;topics;.qpipe.job.kafka_feed.on_message];
    older:@[value;`.z.exit;{(::)}];
    `.z.exit set {[older;code] .qpipe.job.kafka_feed.shutdown[]; if[100h=type older; older code]}[older];
    .qetl.log.info[`kafka_feed;"subscribed";`topics`group!(topics;cfg`group.id)];
    }

/ Start: read the settings, load the library, subscribe. Throws, and so stops the
/ process before it connects to the tickerplant, when Kafka is not set up.
.qpipe.job.kafka_feed.on_start:{[]
    env:.qpipe.job.kafka_feed.read_env[];
    cfg:.qpipe.job.kafka_feed.config_from env;
    topics:.qpipe.job.kafka_feed.topics_from env;
    .qpipe.job.kafka_feed.load_library[];
    .qpipe.job.kafka_feed.open[cfg;topics];
    }

\d .

.qetl.job.stream.define[`kafka_feed;`procname`subscribe_to`publishes`period`on_timer`on_start`note!(
    `kafka_feed1;
    `symbol$();
    enlist `kafka_message;
    0D00:00:00.200;
    .qpipe.job.kafka_feed.on_timer;
    .qpipe.job.kafka_feed.on_start;
    "the native q Kafka consumer (KX kfk, pinned v1.6.6): publishes every record undecoded onto kafka_message, committing offsets only after the publish returned. Needs UQF_KFK_HOME and UQF_KAFKA_BROKERS, so it is started on demand, never with the stack. The Python consumer (uqs connector start kafka) publishes the same table; run one per group")];
