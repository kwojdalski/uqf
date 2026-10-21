/ kafka_diagnostics.q - which STAGE of "Kafka to the plant" is broken, and
/ saying so once (.qetl.kafka.diag, #1492).
/ .
/ "Subscribed" is not "ingesting". A consumer passes through six stages and
/ each fails differently:
/ .
/   library      the native kfk library is loaded
/   bootstrap    the bootstrap server answers and metadata comes back
/   advertised   the broker addresses IN that metadata are reachable from here
/   group        the consumer group is joined and partitions are assigned
/   consume      records are polled, and the principal may read the topic
/   published    the polled records reached the tickerplant
/   committed    their offsets were committed
/ .
/ (A seventh, "rows visible in the RDB", is the verifier's: this process
/ cannot see what another one stores.)
/ .
/ BOOTSTRAP IS NOT ADVERTISED. A client dials the bootstrap list once, to ask
/ for metadata; every later connection - the group coordinator, each
/ partition leader - goes to the addresses the BROKER advertises, which may
/ be names this host cannot resolve, or ports a tunnel does not forward.
/ librdkafka reports that as a transport error on a node that is not
/ `bootstrap`, over and over, once a second. classify reads the node out of
/ the message and files it under `advertised`, which is the line a human
/ needs: "the broker told us to go to X and X is unreachable".
/ .
/ ONCE PER CAUSE. A cause is (stage; endpoint; code). report logs it the first
/ time it is seen, at ERROR, naming the stage, the endpoint and the broker's
/ own text, and counts the repeats silently. clear logs the recovery, with the
/ repeat count, when the stage comes good - so a later, different failure is
/ reported again rather than lost behind an old one.
/ .
/ A GROUP PERMISSION IS NOT A TOPIC PERMISSION. The broker refuses them
/ separately (GROUP_AUTHORIZATION_FAILED and TOPIC_AUTHORIZATION_FAILED); a
/ principal that may DESCRIBE a group has shown nothing about reading the
/ topic. The only proof of consume permission is polling a record, which is
/ why the `consume` stage turns ok on a polled record and on nothing else.
/ .
/ A CAUSE IS RETIRED ONLY BY EVIDENCE ABOUT ITS OWN SUBJECT (#1521). A polled
/ record from topic A says nothing about topic B's refusal, nor about one
/ unreachable broker among several, so "some partition delivered" never retires
/ a `consume` or `advertised` cause. What does:
/   - a record from the refused topic (the cause's `subject`, when the report
/     named a topic: clear_delivered);
/   - the cause having gone quiet - not reported for `quiet` - while the stage
/     is proved elsewhere (clear_quiet). A cause that is still being reported is
/     open whatever else works, so readiness says failed and the log is not
/     told "recovered" only for the error to come back on the next callback.
/ A `consume` cause whose report named no topic can only go quiet.
/ .
/ Nothing here reads a secret, and nothing here throws: a diagnostic that
/ threw inside librdkafka's callback thread would be swallowed and lost.

\d .

/ What the process connects to, for naming an endpoint and the right remedy.
/ Set by the feed when it opens: `bootstrap` is the list of host:port strings,
/ `group` the consumer group.
/ `topics` is the subscribed list: a report that names one of them is about that topic.
.qetl.kafka.diag.context:`bootstrap`group`topics!(();"";`symbol$())

/ The causes seen and not yet cleared. `repeats` counts the occurrences after the logged first, `touched` is when it was last reported, `subject` the topic it is about (null when unknown or not about one).
.qetl.kafka.diag.causes:([cause:`symbol$()] stage:`symbol$(); kind:`symbol$(); endpoint:(); first_seen:`timestamp$(); repeats:`long$(); subject:`symbol$(); touched:`timestamp$())

/ How long a cause must go unreported before a stage proved elsewhere may retire it. librdkafka repeats a failing connection every second or so (reconnect.backoff.max.ms defaults to 10s), so this is several of its periods.
.qetl.kafka.diag.quiet:0D00:00:30

/ The topics that delivered a record since the feed last asked (take_delivered).
.qetl.kafka.diag.delivered:`symbol$()

/ librdkafka's names for the errors that need a different remedy, and what each is.
.qetl.kafka.diag.names:(`TOPIC_AUTHORIZATION_FAILED`GROUP_AUTHORIZATION_FAILED`UNKNOWN_TOPIC_OR_PART,`$("_UNKNOWN_TOPIC";"_AUTHENTICATION";"_ALL_BROKERS_DOWN"))!`topic_auth`group_auth`unknown_topic`unknown_topic`sasl`all_down

/ Broker and client error codes of the same kinds, for a callback that gives a number.
.qetl.kafka.diag.numbers:(29 30 3 -188 -169 -187)!`topic_auth`group_auth`unknown_topic`unknown_topic`sasl`all_down

/ The stage each kind of failure belongs to (a connect failure is decided by the node: see classify).
.qetl.kafka.diag.kind_stage:(`topic_auth`group_auth`unknown_topic`sasl`all_down`other)!`consume`group`consume`bootstrap`bootstrap`unclassified

/ Private: case-insensitive "contains" over a string.
/ @private
.qetl.kafka.diag.has:{[s;sub] 0<count ss[lower s;lower sub]}

/ Private: the text between the first pair of single quotes, or empty.
/ @private
.qetl.kafka.diag.quoted:{[s]
    i:s?"'";
    if[i=count s; :""];
    r:(i+1)_s;
    j:r?"'";
    $[j=count r; ""; j#r]}

/ Private: split librdkafka's "host:port/node: text" prefix.
/ .
/ The node is `bootstrap` for a bootstrap connection and a broker id for an
/ advertised one; the group coordinator's thread is named instead.
/ @return dict endpoint (a string, empty when none could be read) and node (a string)
/ @private
.qetl.kafka.diag.prefix:{[reason]
    q:.qetl.kafka.diag.quoted[reason];
    i:first ss[reason;": "];
    lead:$[null i; ""; i#reason];
    coord:.qetl.kafka.diag.has[lead;"coordinator"];
    lead:$[coord; trim (1+lead?":")_lead; lead];
    j:lead?"/";
    ep:$[j<count lead; j#lead; lead];
    node:$[j<count lead; (j+1)_lead; ""];
    if[0<count q; ep:q];
    if[any " " = ep; ep:""];
    `endpoint`node!(ep;$[coord; "coordinator"; node])}

/ Which kind of failure a client report is, from its code (a number or a
/ librdkafka error name) and the broker's own text.
/ .
/ The text decides what the code cannot: a transport error is a resolve
/ failure, a refused connection or a timeout, all of them `connect`.
/ @param code an int or long error code, a symbol error name, or null
/ @param reason the broker's or client's text
/ @return one of connect, topic_auth, group_auth, unknown_topic, sasl, all_down, other
/ @eg .qetl.kafka.diag.kind_of[30i;"Group authorization failed"]  ->  `group_auth
.qetl.kafka.diag.kind_of:{[code;reason]
    k:$[-11h=type code; .qetl.kafka.diag.names code; (type code) in -5 -6 -7h; .qetl.kafka.diag.numbers `long$code; `];
    if[not null k; :k];
    h:.qetl.kafka.diag.has[reason;];
    if[h "group authorization"; :`group_auth];
    if[h "not authorized to access group"; :`group_auth];
    if[h "topic authorization"; :`topic_auth];
    if[h "not authorized to access topic"; :`topic_auth];
    if[h "sasl authentication"; :`sasl];
    if[h "brokers are down"; :`all_down];
    if[(h "failed to resolve") or (h "connection refused") or (h "connect to") or (h "connection setup timed out") or h "broker transport failure"; :`connect];
    `other}

/ Classify one client report: stage, kind, endpoint and what to do about it.
/ .
/ A connect failure is `bootstrap` when its node is the bootstrap connection
/ (or its endpoint is in the bootstrap list and names no broker), and
/ `advertised` for any other node: the broker's metadata sent the client
/ there.
/ @param code an int or long error code, a symbol error name, or null
/ @param reason the text librdkafka gave
/ @return dict stage, kind, endpoint, node, code, detail (the text), hint, subject, cause (a symbol identifying it)
/ @eg .qetl.kafka.diag.classify[-195i;"kafka-1:9093/1: Failed to resolve 'kafka-1:9093': No address associated with hostname"]`stage  ->  `advertised
.qetl.kafka.diag.classify:{[code;reason] .qetl.kafka.diag.classify_topic[code;reason;`]}

/ Classify one client report, knowing which topic the client attached it to.
/ @param code an int or long error code, a symbol error name, or null
/ @param reason the text librdkafka gave
/ @param topic the topic the report came with, null when none
/ @return the dict classify returns, with `subject` the topic of a consume report
/ @eg .qetl.kafka.diag.classify_topic[29i;"Topic authorization failed";`orders]`subject  ->  `orders
.qetl.kafka.diag.classify_topic:{[code;reason;topic]
    reason:$[10h=type reason; reason; -11h=type reason; string reason; ""];
    kind:.qetl.kafka.diag.kind_of[code;reason];
    p:.qetl.kafka.diag.prefix[reason];
    ep:p`endpoint;
    boot:(),.qetl.kafka.diag.context`bootstrap;
    listed:any ep~/:boot;
    on_boot:(p[`node]~"bootstrap") or listed and 0=count p`node;
    stage:$[kind=`connect; $[on_boot; `bootstrap; `advertised]; .qetl.kafka.diag.kind_stage kind];
    grp:.qetl.kafka.diag.context`group;
    hint:$[
        stage=`advertised; "the broker's metadata sends clients to ",ep,", which this host cannot reach - the broker's advertised.listeners must name an address reachable from here (or the name must resolve, and the port be forwarded, through a tunnel)";
        stage=`bootstrap; $[kind=`sasl; "the broker refused the credentials - check the user, mechanism and the password variable's value"; kind=`all_down; "no broker answered - check UQF_KAFKA_BROKERS, the network path and the security protocol"; "the bootstrap server ",ep," cannot be reached - check UQF_KAFKA_BROKERS and the network path (or the tunnel)"];
        kind=`group_auth; "this principal may not use consumer group ",grp," - grant Read on that Group resource; topic Read does not cover it";
        kind=`topic_auth; "this principal may not read the topic - grant Read and Describe on it; permission to describe the group proves nothing about this";
        kind=`unknown_topic; "the topic does not exist, or this principal cannot see it";
        "unclassified client report"];
    subject:$[stage=`consume; .qetl.kafka.diag.subject_of[topic;reason]; `];
    cause:`$"|" sv (string stage;string kind;ep;string $[null code; `; code]),$[null subject; (); enlist string subject];
    `stage`kind`endpoint`node`code`detail`hint`subject`cause!(stage;kind;ep;p`node;code;reason;hint;subject;cause)}

/ Private: the topic a consume report is about - the one it came with, else a subscribed topic its text names.
/ @private
.qetl.kafka.diag.subject_of:{[topic;reason]
    if[(-11h=type topic) and not null topic; :topic];
    ctx:.qetl.kafka.diag.context;
    topics:$[`topics in key ctx; (),ctx`topics; `symbol$()];
    named:topics where {[r;t] 0<count ss[r;string t]}[reason] each topics;
    $[count named; first named; `]}

/ Private: log, and never throw.
/ @private
.qetl.kafka.diag.say:{[level;text;fields]
    .[{[l;t;f] $[l=`err; .qetl.log.err[`kafka;t;f]; l=`warn; .qetl.log.warn[`kafka;t;f]; .qetl.log.info[`kafka;t;f]]};(level;text;fields);{[e] ::}];}

/ Report a client error once per cause.
/ .
/ The first sighting of a cause is logged at ERROR with the stage, the
/ endpoint, the code and the broker's text, and the remedy; every repeat only
/ raises the cause's count. Never throws, so it is safe in librdkafka's callback.
/ @param code an int or long error code, a symbol error name, or null
/ @param reason the text librdkafka gave
/ @return 1b when this was the first sighting (and was logged), else 0b
.qetl.kafka.diag.report:{[code;reason] .qetl.kafka.diag.report_topic[code;reason;`]}

/ Report a client error that came with a topic (a consumer message with an mtype), once per cause.
/ @param code an int or long error code, a symbol error name, or null
/ @param reason the text librdkafka gave
/ @param topic the topic the report came with, null when none
/ @return 1b when this was the first sighting (and was logged), else 0b
/ @eg .qetl.kafka.diag.report_topic[29i;"Topic authorization failed";`orders]
.qetl.kafka.diag.report_topic:{[code;reason;topic]
    r:.qetl.kafka.try[{[c;t;p] .qetl.kafka.diag.classify_topic[c;t;p]};(code;reason;topic)];
    if[not first r; :0b];
    d:last r;
    c:d`cause;
    / "N/N brokers are down" names no endpoint and follows the failure that took them down: it is that failure's repeat, not a second cause.
    if[(d[`kind]=`all_down) and 0<count open:select from .qetl.kafka.diag.causes where stage in `bootstrap`advertised, not kind=`all_down;
        update repeats:repeats+1, touched:.z.p from `.qetl.kafka.diag.causes where cause=first exec cause from open;
        :0b];
    if[c in key .qetl.kafka.diag.causes;
        update repeats:repeats+1, touched:.z.p from `.qetl.kafka.diag.causes where cause=c;
        :0b];
    `.qetl.kafka.diag.causes upsert (c;d`stage;d`kind;d`endpoint;.z.p;0;d`subject;.z.p);
    / A report nothing classified may be benign, so it is a WARNING, once; a named stage failing is an ERROR.
    .qetl.kafka.diag.say[$[d[`kind]=`other; `warn; `err];string[d`stage]," stage failed: ",d`hint;
        `stage`kind`endpoint`code`topic`error!(d`stage;d`kind;d`endpoint;d`code;d`subject;.qetl.kafka.diag.shorten[d`detail])];
    1b}

/ Private: a broker's text, bounded - it is untrusted input to a log line.
/ @private
.qetl.kafka.diag.shorten:{[s] $[300<count s; 300#s; s]}

/ Private: forget some causes, logging each recovery once.
/ @param gone the causes table rows to retire (unkeyed, with their cause column)
/ @return the number retired
/ @private
.qetl.kafka.diag.retire:{[gone]
    if[0=count gone; :0];
    ids:gone`cause;
    delete from `.qetl.kafka.diag.causes where cause in ids;
    {.qetl.kafka.diag.say[`info;string[x`stage]," stage recovered";`stage`endpoint`repeats_suppressed!(x`stage;x`endpoint;x`repeats)]} each gone;
    count gone}

/ Forget the causes of some stages because they have come good, logging each recovery once.
/ @param stages a symbol vector of stages
/ @return the number of causes cleared
/ @eg .qetl.kafka.diag.clear `bootstrap`advertised`group
.qetl.kafka.diag.clear:{[stages]
    / `(),stages` inside a where clause would be read as two clauses: the comma separates them.
    which:(),stages;
    .qetl.kafka.diag.retire[0!select from .qetl.kafka.diag.causes where stage in which]}

/ Retire the causes of some stages that have gone quiet: not reported for `quiet`.
/ .
/ For a stage proved elsewhere (an assignment, a polled record). A cause that
/ is still being reported is open, whatever else works; and a `consume` cause
/ with a known subject is never retired this way - only by its topic delivering.
/ @param stages a symbol vector of stages
/ @return the number of causes cleared
/ @eg .qetl.kafka.diag.clear_quiet `bootstrap`advertised`group
.qetl.kafka.diag.clear_quiet:{[stages]
    which:(),stages;
    cutoff:.z.p-.qetl.kafka.diag.quiet;
    .qetl.kafka.diag.retire[0!select from .qetl.kafka.diag.causes where stage in which, touched<=cutoff, (not stage=`consume) or null subject]}

/ Retire the `consume` causes whose own topic delivered a record.
/ @param topics a symbol vector of the topics that delivered
/ @return the number of causes cleared
/ @eg .qetl.kafka.diag.clear_delivered enlist `orders
.qetl.kafka.diag.clear_delivered:{[topics]
    which:(),topics;
    .qetl.kafka.diag.retire[0!select from .qetl.kafka.diag.causes where stage=`consume, subject in which, not null subject]}

/ Note the topics that delivered a record, for the feed's next readiness turn.
/ @param topics a symbol vector
/ @return nothing
/ @eg .qetl.kafka.diag.note_delivered enlist `orders
.qetl.kafka.diag.note_delivered:{[topics]
    `.qetl.kafka.diag.delivered set distinct .qetl.kafka.diag.delivered,topics;
    }

/ The topics that delivered since the last call, and forget them.
/ @return a symbol vector
/ @eg .qetl.kafka.diag.take_delivered[]
.qetl.kafka.diag.take_delivered:{[]
    r:.qetl.kafka.diag.delivered;
    `.qetl.kafka.diag.delivered set `symbol$();
    r}

/ The readiness of each stage, from what a consumer knows about itself.
/ .
/ ok only on proof: an assignment proves the bootstrap, advertised and group
/ stages (metadata came back, the coordinator answered, partitions were
/ handed out); a polled record proves consume; and so on. Before that a stage
/ is pending, or failed when a cause is open on it.
/ @param facts dict of library (boolean), assigned (the partition count), polled, published, commits (counts) and publish_errors, commit_errors (counts)
/ @return a table of stage, status (ok, pending or failed) and detail
/ @eg exec status from .qetl.kafka.diag.readiness `library`assigned`polled`published`commits`publish_errors`commit_errors!(1b;0;0;0;0;0;0)
.qetl.kafka.diag.readiness:{[facts]
    open:0!.qetl.kafka.diag.causes;
    why:{[open;s] $[count w:select from open where stage=s; (string first w`kind),"@",first w`endpoint; ""]}[open];
    st:{[open;ok;s] $[s in open`stage; `failed; ok; `ok; `pending]}[open];
    assigned:0<facts`assigned;
    rows:(
        (`library; $[facts`library; `ok; `failed]; "");
        (`bootstrap; st[assigned;`bootstrap]; why`bootstrap);
        (`advertised; st[assigned;`advertised]; why`advertised);
        (`group; st[assigned;`group]; why`group);
        (`consume; st[0<facts`polled;`consume]; why`consume);
        (`published; $[0<facts`published; `ok; (0<facts`publish_errors) and 0=facts`published; `failed; `pending]; "");
        (`committed; $[0<facts`commits; `ok; (0<facts`commit_errors) and 0=facts`commits; `failed; `pending]; ""));
    ([] stage:rows[;0]; status:rows[;1]; detail:rows[;2])}

\d .
