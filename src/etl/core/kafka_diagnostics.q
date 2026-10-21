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
/ Nothing here reads a secret, and nothing here throws: a diagnostic that
/ threw inside librdkafka's callback thread would be swallowed and lost.

\d .

/ What the process connects to, for naming an endpoint and the right remedy.
/ Set by the feed when it opens: `bootstrap` is the list of host:port strings,
/ `group` the consumer group.
.qetl.kafka.diag.context:`bootstrap`group!(();"")

/ The causes seen and not yet cleared. `repeats` counts the occurrences after the logged first.
.qetl.kafka.diag.causes:([cause:`symbol$()] stage:`symbol$(); kind:`symbol$(); endpoint:(); first_seen:`timestamp$(); repeats:`long$())

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
/ @return dict stage, kind, endpoint, node, code, detail (the text), hint, cause (a symbol identifying it)
/ @eg .qetl.kafka.diag.classify[-195i;"kafka-1:9093/1: Failed to resolve 'kafka-1:9093': No address associated with hostname"]`stage  ->  `advertised
.qetl.kafka.diag.classify:{[code;reason]
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
    cause:`$"|" sv (string stage;string kind;ep;string $[null code; `; code]);
    `stage`kind`endpoint`node`code`detail`hint`cause!(stage;kind;ep;p`node;code;reason;hint;cause)}

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
.qetl.kafka.diag.report:{[code;reason]
    r:.qetl.kafka.try[{[c;t] .qetl.kafka.diag.classify[c;t]};(code;reason)];
    if[not first r; :0b];
    d:last r;
    c:d`cause;
    / "N/N brokers are down" names no endpoint and follows the failure that took them down: it is that failure's repeat, not a second cause.
    if[(d[`kind]=`all_down) and 0<count open:select from .qetl.kafka.diag.causes where stage in `bootstrap`advertised, not kind=`all_down;
        update repeats:repeats+1 from `.qetl.kafka.diag.causes where cause=first exec cause from open;
        :0b];
    if[c in key .qetl.kafka.diag.causes;
        update repeats:repeats+1 from `.qetl.kafka.diag.causes where cause=c;
        :0b];
    `.qetl.kafka.diag.causes upsert (c;d`stage;d`kind;d`endpoint;.z.p;0);
    / A report nothing classified may be benign, so it is a WARNING, once; a named stage failing is an ERROR.
    .qetl.kafka.diag.say[$[d[`kind]=`other; `warn; `err];string[d`stage]," stage failed: ",d`hint;
        `stage`kind`endpoint`code`error!(d`stage;d`kind;d`endpoint;d`code;.qetl.kafka.diag.shorten[d`detail])];
    1b}

/ Private: a broker's text, bounded - it is untrusted input to a log line.
/ @private
.qetl.kafka.diag.shorten:{[s] $[300<count s; 300#s; s]}

/ Forget the causes of some stages because they have come good, logging each recovery once.
/ @param stages a symbol vector of stages
/ @return the number of causes cleared
/ @eg .qetl.kafka.diag.clear `bootstrap`advertised`group
.qetl.kafka.diag.clear:{[stages]
    / `(),stages` inside a where clause would be read as two clauses: the comma separates them.
    which:(),stages;
    gone:select from .qetl.kafka.diag.causes where stage in which;
    if[0=count gone; :0];
    delete from `.qetl.kafka.diag.causes where stage in which;
    {.qetl.kafka.diag.say[`info;string[x`stage]," stage recovered";`stage`endpoint`repeats_suppressed!(x`stage;x`endpoint;x`repeats)]} each 0!gone;
    count gone}

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
