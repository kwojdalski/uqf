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
/ READINESS AND DIAGNOSTICS (#1492). "subscribed" is not ready. The error
/ callback hands every client error to core/kafka_diagnostics.q, which names the
/ stage (bootstrap, advertised, group, consume) and logs each CAUSE once, with
/ the endpoint and the broker's own text, instead of a WARNING a second for as
/ long as librdkafka keeps retrying; on_timer then logs a `readiness` line when
/ the stage-by-stage picture changes. An assignment proves the bootstrap, the
/ advertised endpoints and the group; a polled record proves the topic may be read.
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
    `.kfk.logcb set .qpipe.job.kafka_feed.on_log;
    / kfk.q hands a message whose topic has no subscribed callback to
    / consumetopic[`], a no-op - and a group refusal has no topic, so it was
    / dropped and a feed with no group permission logged nothing (#1492).
    .kfk.consumetopic[`]:.qpipe.job.kafka_feed.on_message;
    `.qpipe.job.kafka_feed.api set .qpipe.job.kafka_feed.real_api[];
    }

/ librdkafka's log callback, in place of kfk.q's, which `show`s every line.
/ .
/ Those lines repeat what the error callback reports, at librdkafka's retry
/ rate: against an unresolvable advertised host, ten "FAIL" lines a second
/ beside the one ERROR on_error writes (#1492). They go to DEBUG, so
/ `.qetl.log.debug 1b` brings them back when they are wanted.
/ @param level the syslog level librdkafka gave
/ @param facility its facility, e.g. "FAIL"
/ @param text the line
.qpipe.job.kafka_feed.on_log:{[level;facility;text]
    .[.qetl.log.dbg;(`kafka;"librdkafka";`level`facility`text!(level;facility;text));::];}

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

/ The client's error callback: report once per cause, never throw (librdkafka reconnects on its own).
/ .
/ librdkafka calls this every second for as long as a broker is unreachable, so
/ logging each call is the bug (#1492): an unresolvable ADVERTISED hostname was a
/ WARNING a second. core/kafka_diagnostics.q names the stage and the endpoint
/ and logs a cause the first time only.
/ @param cid the client id
/ @param code librdkafka's error code
/ @param reason the text it gave
.qpipe.job.kafka_feed.on_error:{[cid;code;reason]
    .[.qetl.kafka.diag.report;(code;reason);::];}

/ What the readiness report said last, so a change is logged and a steady state is not.
.qpipe.job.kafka_feed.last_readiness:""

/ The facts the readiness table is derived from.
/ @return dict for .qetl.kafka.diag.readiness
.qpipe.job.kafka_feed.facts:{[]
    a:.qetl.kafka.try[{[x] .qpipe.job.kafka_feed.transport[][`assigned] 0b};enlist 0b];
    n:$[first a; $[98h=type last a; count last a; 0]; 0];
    s:(.qpipe.job.kafka_feed.state)`stats;
    `library`assigned`polled`published`commits`publish_errors`commit_errors!(0<count .qpipe.job.kafka_feed.api;n;s`polled;s`published;s`commits;s`publish_errors;s`commit_errors)}

/ The stage-by-stage readiness of this consumer (core/kafka_diagnostics.q).
/ .
/ "Subscribed" is not ready: a stage is ok only on proof - an assignment proves
/ the broker and its advertised endpoints were reached and the group joined; a
/ polled record proves the topic may be read.
/ @return a table of stage, status and detail
/ @eg exec stage from .qpipe.job.kafka_feed.readiness[]
.qpipe.job.kafka_feed.readiness:{[]
    .qetl.kafka.diag.readiness .qpipe.job.kafka_feed.facts[]}

/ What the last tick saw, so a stage is retired only on evidence newer than its cause.
.qpipe.job.kafka_feed.last_seen:`assigned`polled!0 0

/ Log the readiness when it changed, and retire the causes of stages that came good.
/ .
/ A stage is retired on NEW evidence only (#1500): the assignment going from none
/ to some, or the polled count rising since the last tick. librdkafka keeps its
/ assignment through a broker outage and `polled` is cumulative, so testing
/ their level cleared the cause every tick and the next callback logged it again.
/ .
/ Nor is a cause retired by evidence about something else (#1521): a record from
/ topic A does not retire topic B's refusal, nor one unreachable broker's cause
/ while another broker delivers. A cause goes when its own topic delivers, or
/ when it has gone quiet (core/kafka_diagnostics.q, "A cause is retired only by
/ evidence about its own subject"). A cause still being reported keeps its stage
/ failed and logs nothing more.
.qpipe.job.kafka_feed.report_readiness:{[]
    f:.qpipe.job.kafka_feed.facts[];
    seen:.qpipe.job.kafka_feed.last_seen;
    rose:(f`polled)>seen`polled;
    `.qpipe.job.kafka_feed.last_seen set `assigned`polled!(f`assigned;f`polled);
    / Evidence about a cause's OWN subject (#1521): the refused topic delivering. Any other partition delivering says nothing about it.
    .qetl.kafka.diag.clear_delivered .qetl.kafka.diag.take_delivered[];
    / Proof the stage works elsewhere retires only a cause that has gone quiet: a failing broker is reported every second or so whatever other brokers deliver.
    if[rose or 0<f`assigned; .qetl.kafka.diag.clear_quiet `bootstrap`advertised`group];
    if[rose; .qetl.kafka.diag.clear_quiet enlist `consume];
    r:.qetl.kafka.diag.readiness f;
    line:" " sv {string[x`stage],"=",string x`status} each r;
    if[not line~.qpipe.job.kafka_feed.last_readiness;
        `.qpipe.job.kafka_feed.last_readiness set line;
        .qetl.log.info[`kafka_feed;"readiness";enlist[`stages]!enlist line]];
    }

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
    / @[f;::;e], not .[f;();e]: the dot form never calls a niladic f (#1492)
    @[.qpipe.job.kafka_feed.report_readiness;::;{[e] ::}];
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
    `.qetl.kafka.diag.context set `bootstrap`group`topics!(trim each "," vs string cfg`metadata.broker.list;string cfg`group.id;(),topics);
    c:.qpipe.job.kafka_feed.api[`consumer] cfg;
    `.qpipe.job.kafka_feed.client set c;
    .qpipe.job.kafka_feed.api[`on_error][c;.qpipe.job.kafka_feed.on_error];
    .qpipe.job.kafka_feed.api[`subscribe][c;topics;.qpipe.job.kafka_feed.on_message];
    older:@[value;`.z.exit;{(::)}];
    `.z.exit set {[older;code] .qpipe.job.kafka_feed.shutdown[]; if[((type older) within 100 107h) and not older~(::); older code]}[older];
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
